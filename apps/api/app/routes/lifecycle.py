"""Document lifecycle routes — list / get / extract / semantic / review.

Covers everything between an uploaded version and a validated /
rejected one:

- catalog reads (``GET /documents`` + filters, ``GET /documents/{id}``)
- extraction trigger / retry / read
- semantic-document trigger / read
- generated Markdown read
- raw bytes read (powers Knowledge Explorer's per-type viewers)
- validate / reject endpoints — the side-effect chain now lives in
  :class:`app.services.review_service.ReviewService` (audit #223).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response

from app.dependencies import PipelineServices
from app.errors import ApiError, ErrorCode
from app.models.document import DocumentVersionStatus
from app.schemas.chunk_location import (
    CHUNK_LOCATION_SCHEMA_VERSION,
    MAX_CHUNK_LOCATIONS_LIMIT,
    ChunkLocation,
    ChunkLocationsResponse,
    ChunkSource,
)
from app.schemas.document import (
    Document,
    DocumentListResponse,
    DocumentVersion,
    LineageResponse,
    LineageVersion,
    SimilarDocument,
    SimilarDocumentsResponse,
)
from app.schemas.document_confidence import DocumentConfidenceResponse
from app.schemas.document_topic import DOCUMENT_TOPIC_SCHEMA_VERSION
from app.schemas.extraction import ExtractionJobSnapshot, NormalizedRect, RawExtraction
from app.schemas.high_value_chunks import HighValueChunksResponse
from app.schemas.scope import DocumentScopesResponse, ScopeRef
from app.schemas.semantic_document import SemanticDocument
from app.services.auth import (
    User,
    assert_can_access_document,
    get_caller_scopes,
    require_contributor,
    require_reviewer,
    require_viewer,
)
from app.services.auth.scope_filter import ALL_SCOPES_SENTINEL, user_can_access
from app.services.catalog_store import InvalidCursor, _encode_cursor
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionFailed
from app.services.extraction_worker import ExtractionRequest, QueueFull
from app.services.idempotency_store import hash_json_body
from app.services.knowledge.high_value_chunks import HighValueChunksService
from app.services.semantic_output_service import (
    SemanticGenerationFailed,
    UnknownSemanticMethod,
)
from app.settings import Settings

from ._helpers import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    MIN_PAGE_LIMIT,
    ReviewRequest,
    _check_idempotency,
    _store_idempotency,
)

log = logging.getLogger(__name__)

# Re-exported so existing test imports of ``DocumentVersion`` etc. via
# ``app.routes`` keep working through the package façade.
__all__ = ["build_lifecycle_router", "DocumentVersion"]


def build_lifecycle_router(services: PipelineServices) -> APIRouter:
    """Register the document lifecycle routes."""
    router = APIRouter()

    @router.get(
        "/documents",
        operation_id="list_documents",
        response_model=DocumentListResponse,
    )
    def list_documents(
        limit: int = DEFAULT_PAGE_LIMIT,
        cursor: str | None = None,
        status: list[str] | None = Query(default=None),
        q: str | None = Query(default=None, max_length=200),
        caller_scopes: tuple[ScopeRef, ...] = Depends(get_caller_scopes),
        _user: User = Depends(require_viewer),
    ) -> Any:
        """List document families with optional status / filename filters (#86).

        - ``status`` is repeatable. ``?status=VALIDATED&status=NEEDS_REVIEW``
          returns only documents whose latest version is in either state.
          Unknown status names yield 400 with a clear allowed-set message
          rather than a silent 0-result page.
        - ``q`` is a case-insensitive substring match against the
          document's ``original_filename``. Trims whitespace; an empty
          string after trim is treated as "no filter".
        - Filters apply before pagination. Re-walking with a different
          filter requires dropping the cursor.

        Scope filter (EPIC-D D.5, ADR-020 §2): the response is filtered
        to documents linked to the caller's allowed scopes (default
        ``personal:<current_user.id>``). ``KW_AUTH_MODE=disabled``
        skips the predicate for back-compat.
        """
        if limit < MIN_PAGE_LIMIT or limit > MAX_PAGE_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"limit must be between {MIN_PAGE_LIMIT} and {MAX_PAGE_LIMIT}; got {limit}."
                ),
            )

        status_set: frozenset[DocumentVersionStatus] | None = None
        if status:
            valid_values = {s.value for s in DocumentVersionStatus}
            normalized = {value.strip().upper() for value in status if value.strip()}
            unknown = normalized - valid_values
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Unknown status: {', '.join(sorted(unknown))}. "
                        f"Allowed values: {', '.join(sorted(valid_values))}."
                    ),
                )
            if normalized:
                status_set = frozenset(DocumentVersionStatus(v) for v in normalized)

        filename_query = q.strip() if q is not None else None
        if filename_query == "":
            filename_query = None

        try:
            items, next_cursor = _list_documents_with_scope(
                services=services,
                caller_scopes=caller_scopes,
                limit=limit,
                cursor=cursor,
                status_filter=status_set,
                filename_query=filename_query,
            )
        except InvalidCursor as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid cursor: {exc}",
            ) from exc
        return {"items": items, "next_cursor": next_cursor}

    @router.get(
        "/documents/{document_id}",
        operation_id="get_document",
        response_model=Document,
    )
    def get_document(
        request: Request,
        document_id: str,
        current_user: User = Depends(require_viewer),
    ) -> Any:
        document = services.documents.get_document(document_id)
        if document is None:
            # ADR-027 §3 / slice 6: a fully-purged document is hidden
            # from the standard read path (the catalog filters
            # ``archived_at IS NULL`` per #265). Reach into the
            # archived-inclusive accessor and surface a 410 Gone
            # only when *every* version in the family is PURGED;
            # otherwise the row really does not exist for this
            # caller and the original 404 stands.
            archived = services.documents.catalog._get_document_including_archived(  # type: ignore[attr-defined]
                document_id,
            )
            if archived is not None and _all_versions_purged(archived):
                raise _purged_document_error(document_id)
            raise HTTPException(status_code=404, detail="Document not found.")
        # Hidden-existence semantics (D.5): a 404 here is indistinguishable
        # from "document doesn't exist", so an enumeration probe can't
        # tell whether the row is missing or owned by another user.
        assert_can_access_document(request=request, document_id=document_id, user=current_user)
        return document

    @router.post(
        "/documents/{document_id}/versions/{version_id}/extract",
        operation_id="extract_version",
        response_model=RawExtraction | ExtractionJobSnapshot,
        responses={
            200: {
                "model": RawExtraction,
                "description": (
                    "Inline extraction completed (``KW_EXTRACTION_INLINE=true``, the default)."
                ),
            },
            202: {
                "model": ExtractionJobSnapshot,
                "description": (
                    "Async extraction enqueued (``KW_EXTRACTION_INLINE=false``). "
                    "Poll ``GET /documents/{document_id}`` for the version's "
                    "lifecycle progression."
                ),
            },
            503: {
                "description": (
                    "Async extraction queue is at capacity. Includes "
                    "``Retry-After: 5`` and ``KW_QUEUE_FULL`` envelope."
                ),
            },
        },
    )
    def extract_document(
        request: Request,
        document_id: str,
        version_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        current_user: User = Depends(require_contributor),
    ) -> Any:
        # D.5: hidden-existence semantics — a 404 here is indistinguishable
        # from "no such document" so we don't leak that another user
        # owns this row.
        assert_can_access_document(request=request, document_id=document_id, user=current_user)
        _route = "/documents/{document_id}/versions/{version_id}/extract"
        _req_hash = hash_json_body(
            None,
            path_params={"document_id": document_id, "version_id": version_id},
        )
        cached = _check_idempotency(
            store=services.idempotency,
            idempotency_key=idempotency_key,
            route=_route,
            request_hash=_req_hash,
        )
        if cached is not None:
            return cached
        if services.settings.extraction_inline:
            return _run_inline_extract(
                services=services,
                document_id=document_id,
                version_id=version_id,
                idempotency_key=idempotency_key,
                route=_route,
                request_hash=_req_hash,
                actor=current_user.id,
            )
        return _enqueue_extract(
            request=request,
            services=services,
            document_id=document_id,
            version_id=version_id,
            actor=current_user.id,
        )

    @router.post(
        "/documents/{document_id}/versions/{version_id}/retry-extraction",
        operation_id="retry_extraction",
        response_model=RawExtraction | ExtractionJobSnapshot,
        responses={
            200: {
                "model": RawExtraction,
                "description": ("Inline retry completed (``KW_EXTRACTION_INLINE=true``)."),
            },
            202: {
                "model": ExtractionJobSnapshot,
                "description": (
                    "Async retry enqueued (``KW_EXTRACTION_INLINE=false``). "
                    "The version transitions ``FAILED → QUEUED_FOR_EXTRACTION``."
                ),
            },
            503: {
                "description": (
                    "Async extraction queue is at capacity. Includes "
                    "``Retry-After: 5`` and ``KW_QUEUE_FULL`` envelope."
                ),
            },
        },
    )
    def retry_extraction(
        request: Request,
        document_id: str,
        version_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        current_user: User = Depends(require_contributor),
    ) -> Any:
        """Retry extraction for a previously-FAILED version (#87, ADR-006 PR-2).

        Returns the fresh ``RawExtraction`` (200) on success in inline
        mode, an :class:`ExtractionJobSnapshot` (202) in async mode, or
        ``422`` with the new failure reason on an inline re-fail. ``404``
        if the version doesn't exist, ``409`` if the version isn't in
        ``FAILED`` (review states stay frozen — retry never bypasses the
        gate), ``503`` if the async queue is full.
        """
        _route = "/documents/{document_id}/versions/{version_id}/retry-extraction"
        _req_hash = hash_json_body(
            None,
            path_params={"document_id": document_id, "version_id": version_id},
        )
        cached = _check_idempotency(
            store=services.idempotency,
            idempotency_key=idempotency_key,
            route=_route,
            request_hash=_req_hash,
        )
        if cached is not None:
            return cached
        if services.settings.extraction_inline:
            return _run_inline_retry(
                services=services,
                document_id=document_id,
                version_id=version_id,
                idempotency_key=idempotency_key,
                route=_route,
                request_hash=_req_hash,
                actor=current_user.id,
            )
        return _enqueue_retry(
            request=request,
            services=services,
            document_id=document_id,
            version_id=version_id,
            actor=current_user.id,
        )

    @router.get(
        "/documents/{document_id}/versions/{version_id}/extraction",
        operation_id="get_extraction",
        response_model=RawExtraction,
    )
    def get_extraction(
        request: Request,
        document_id: str,
        version_id: str,
        current_user: User = Depends(require_viewer),
    ) -> Any:
        # ADR-027 §3 / slice 6: 410 Gone for purged versions. Check
        # the version's status before reading the extraction so a
        # tombstoned version surfaces the same 410 envelope as the
        # raw-bytes route — consistent client experience.
        try:
            version = _get_version_including_archived(
                services=services,
                document_id=document_id,
                version_id=version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if version.status is DocumentVersionStatus.PURGED:
            # ADR-027 §3 / slice 6: purge tombstones are operator-visible
            # by design (cascade flows + audit consumers). The 410
            # surface beats hidden-existence here; the actual content
            # fetch below is still gated by the scope check.
            raise _purged_version_error(document_id=document_id, version=version)
        # #83 slice 3 (D.5 hidden-existence): scope check after PURGED
        # so the actual extraction payload stays hidden from callers
        # without scope — they get the same 404 envelope ``GET
        # /documents/{id}`` returns.
        assert_can_access_document(request=request, document_id=document_id, user=current_user)
        try:
            return services.extraction_jobs.get_raw_extraction(
                document_id=document_id,
                version_id=version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/documents/{document_id}/versions/{version_id}/semantic",
        operation_id="generate_semantic",
        response_model=SemanticDocument,
        responses={
            400: {
                "description": (
                    "Requested ``method`` is not registered for this "
                    "deployment. ``GET /admin/config`` exposes the live "
                    "list under ``semantic_methods``."
                ),
            },
            502: {
                "description": (
                    "The LLM-backed semantic generator failed (network, "
                    "rate-limit, or upstream error). Retry or fall back "
                    "to the deterministic method."
                ),
            },
        },
    )
    def generate_semantic_document(
        request: Request,
        document_id: str,
        version_id: str,
        method: str | None = Query(
            default=None,
            description=(
                "Semantic-generation method id. Omit for the runtime "
                "default (``structure_first`` — Method 1). Pass "
                "``semantic_intelligence`` (Method 2) or "
                "``knowledge_graph`` (Method 3) to run an LLM-driven "
                "strategy when a provider key is configured. An "
                "unknown id returns 400; see "
                "https://github.com/SofianeBENHELLI/KW-Pipeline/issues/453 "
                "for the spec."
            ),
        ),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        current_user: User = Depends(require_contributor),
    ) -> Any:
        # D.5: hidden-existence — refuse before any catalog work happens.
        assert_can_access_document(request=request, document_id=document_id, user=current_user)
        _route = "/documents/{document_id}/versions/{version_id}/semantic"
        # Include the chosen method in the idempotency hash so the
        # same key replayed with a different method does NOT serve a
        # stale row generated by the other strategy. ``hash_json_body``
        # merges its kwargs into the canonical body dict; the route
        # coordinates dict here doubles as the "what is being addressed"
        # fingerprint.
        _path_params: dict = {
            "document_id": document_id,
            "version_id": version_id,
        }
        if method:
            _path_params["method"] = method
        _req_hash = hash_json_body(None, path_params=_path_params)
        cached = _check_idempotency(
            store=services.idempotency,
            idempotency_key=idempotency_key,
            route=_route,
            request_hash=_req_hash,
        )
        if cached is not None:
            return cached
        try:
            result = services.semantic_outputs.generate(
                document_id=document_id,
                version_id=version_id,
                method=method,
            )
            _store_idempotency(
                store=services.idempotency,
                idempotency_key=idempotency_key,
                route=_route,
                request_hash=_req_hash,
                result=result.model_dump(mode="json"),
            )
            return result
        except UnknownSemanticMethod as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown semantic method {str(exc)!r}. Available: "
                    f"{services.semantic_outputs.available_methods}."
                ),
            ) from exc
        except SemanticGenerationFailed as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/documents/{document_id}/versions/{version_id}/semantic",
        operation_id="get_semantic",
        response_model=SemanticDocument,
    )
    def get_semantic_document(
        request: Request,
        document_id: str,
        version_id: str,
        current_user: User = Depends(require_viewer),
    ) -> Any:
        # ADR-027 §3 / slice 6: 410 Gone for purged versions.
        try:
            version = _get_version_including_archived(
                services=services,
                document_id=document_id,
                version_id=version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if version.status is DocumentVersionStatus.PURGED:
            # ADR-027 §3 / slice 6: purge tombstones are operator-visible.
            raise _purged_version_error(document_id=document_id, version=version)
        # #83 slice 3 (D.5 hidden-existence): scope check after PURGED.
        assert_can_access_document(request=request, document_id=document_id, user=current_user)
        try:
            return services.semantic_outputs.get(document_id=document_id, version_id=version_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/documents/{document_id}/versions/{version_id}/markdown",
        operation_id="get_markdown",
        responses={
            200: {
                "content": {"text/markdown": {"schema": {"type": "string"}}},
                "description": "Generated Markdown for the version.",
            },
            410: {"description": "Version artifacts were purged (ADR-027 §3)."},
        },
    )
    def get_markdown(
        request: Request,
        document_id: str,
        version_id: str,
        current_user: User = Depends(require_viewer),
    ) -> Response:
        # ADR-027 §3 / slice 6: 410 Gone for purged versions.
        try:
            version = _get_version_including_archived(
                services=services,
                document_id=document_id,
                version_id=version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if version.status is DocumentVersionStatus.PURGED:
            # ADR-027 §3 / slice 6: a purged version surfaces 410 to
            # every actor that asks for it — the tombstone is
            # operator-visible by design so audit / cascade flows can
            # see the purge. The 410 leak is intentional and bounded;
            # IDs are random UUIDs, so an attacker who can't already
            # see the catalog can't enumerate this surface.
            raise _purged_version_error(document_id=document_id, version=version)
        # #83 slice 3 (D.5 hidden-existence): scope check after PURGED
        # so operators still see tombstones, but the actual content
        # fetch is hidden from callers without scope — they get the
        # same 404 envelope ``GET /documents/{id}`` returns.
        assert_can_access_document(request=request, document_id=document_id, user=current_user)
        try:
            markdown = services.semantic_outputs.get_markdown(
                document_id=document_id,
                version_id=version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(content=markdown, media_type="text/markdown")

    @router.get(
        "/documents/{document_id}/versions/{version_id}/raw",
        operation_id="get_raw_file",
        responses={
            200: {
                "content": {"application/octet-stream": {}},
                "description": "Original uploaded binary for the version.",
            },
            404: {"description": "Document or version not found."},
            410: {"description": "Version artifacts were purged (ADR-027 §3)."},
        },
    )
    def get_raw_file(
        request: Request,
        document_id: str,
        version_id: str,
        current_user: User = Depends(require_viewer),
    ) -> Response:
        """Stream the originally-uploaded binary back to the caller.

        Powers the Knowledge Explorer's per-type viewers (PDF/DOCX/PPTX/
        text/wiki). The Content-Type mirrors what the uploader declared
        at ingest time, and ``Content-Disposition: inline`` lets browsers
        render PDFs and images natively instead of forcing a download.

        Returns HTTP 410 Gone when the version's status is
        :data:`DocumentVersionStatus.PURGED` per ADR-027 §3 — the
        bytes were intentionally deleted via ``purge_artifacts`` and
        the storage URI is now a tombstone marker. Distinguishing
        410 from 404 lets clients render a tombstone card with the
        purge timestamp instead of a generic "not found" message.
        """
        try:
            version = _get_version_including_archived(
                services=services,
                document_id=document_id,
                version_id=version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if version.status is DocumentVersionStatus.PURGED:
            # ADR-027 §3 / slice 6: a purged version surfaces 410 to
            # every actor that asks for it — the tombstone is
            # operator-visible by design so audit / cascade flows can
            # see the purge. The 410 leak is intentional and bounded;
            # IDs are random UUIDs, so an attacker who can't already
            # see the catalog can't enumerate this surface.
            raise _purged_version_error(document_id=document_id, version=version)
        # #83 slice 3 (D.5 hidden-existence): scope check after PURGED
        # so operators still see tombstones, but the actual content
        # fetch is hidden from callers without scope — they get the
        # same 404 envelope ``GET /documents/{id}`` returns.
        assert_can_access_document(request=request, document_id=document_id, user=current_user)
        try:
            payload = services.documents.storage.get(version.storage_uri)
        except (KeyError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(
                status_code=404, detail="Raw bytes are no longer available."
            ) from exc
        media_type = version.content_type or "application/octet-stream"
        # Quote the filename per RFC 5987 so non-ASCII names don't break
        # the header. ``filename*`` is the modern form; the legacy
        # ``filename=`` falls back to a sanitized ASCII version.
        ascii_name = "".join(c if ord(c) < 128 else "_" for c in version.filename)
        encoded_name = urlquote(version.filename, safe="")
        disposition = f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
        return Response(
            content=payload,
            media_type=media_type,
            headers={
                "Content-Disposition": disposition,
                "Content-Length": str(len(payload)),
                "Cache-Control": "private, max-age=300",
            },
        )

    @router.get(
        "/documents/{document_id}/versions/{version_id}/chunks",
        operation_id="list_document_chunks",
        response_model=ChunkLocationsResponse,
    )
    def list_document_chunks(
        request: Request,
        document_id: str,
        version_id: str,
        limit: int = Query(
            default=MAX_CHUNK_LOCATIONS_LIMIT,
            ge=1,
            le=MAX_CHUNK_LOCATIONS_LIMIT,
        ),
        page: int | None = Query(default=None, ge=1),
        source: ChunkSource | None = Query(default=None),
        min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
        current_user: User = Depends(require_viewer),
    ) -> Any:
        """List chunk locations (rects + summary) for the PDF viewer.

        Powers the Phase 2 split-pane viewer in Orbital: one
        :class:`ChunkLocation` per parser-emitted section, carrying the
        :class:`NormalizedRect` overlays the viewer draws on top of
        EmbedPDF plus the LLM-derived summary signal the side panel
        renders next to each chunk row.

        Aggregation rules:

        * One row per section in the persisted :class:`RawExtraction`,
          in reading order.
        * ``rects`` is flattened from every :class:`SourceReference`
          the section owns (today's parser emits one ref per section
          but the API is shape-compatible with a future N:1 split).
        * Document-topic citations are used as the summary signal —
          for each section we pick the highest-confidence topic that
          lists the section in its ``supporting_chunk_ids``. Claims
          and entities are intentionally not joined in v1 (their
          subject-predicate-object shape does not map cleanly to a
          single human-readable summary line; topics do).
        * ``source = "ai_extraction"`` when at least one topic cites
          the section; otherwise ``"parser"`` and ``confidence = 1.0``
          — the parser is deterministic and its presence is not in
          question, only its meaning.

        D.5 hidden-existence: the scope check fires before any
        catalog read so a caller without scope sees the same 404
        envelope a missing document would return. Purged versions
        surface 410 like every other per-version route.
        """
        try:
            version = _get_version_including_archived(
                services=services,
                document_id=document_id,
                version_id=version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if version.status is DocumentVersionStatus.PURGED:
            raise _purged_version_error(document_id=document_id, version=version)
        assert_can_access_document(request=request, document_id=document_id, user=current_user)

        try:
            extraction = services.documents.catalog.get_raw_extraction(version_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        topics_for_version = _topics_for_version(
            store=services.document_topic_store,
            document_id=document_id,
            version_id=version_id,
        )
        # Map section_id → highest-confidence topic citing that section.
        # Sorting once up front lets the per-section lookup pick the
        # best topic in O(1) without re-sorting per chunk.
        topics_for_version.sort(key=lambda t: t.confidence, reverse=True)
        topic_by_chunk: dict[str, Any] = {}
        for topic in topics_for_version:
            for chunk_id in topic.supporting_chunk_ids:
                topic_by_chunk.setdefault(chunk_id, topic)

        # Group source references by section_id once; sections always
        # carry their refs through ``section.source_reference_ids``.
        refs_by_id = {ref.id: ref for ref in extraction.source_references}

        pipeline_version = (
            f"parser={extraction.parser_version};topic={DOCUMENT_TOPIC_SCHEMA_VERSION}"
        )

        items: list[ChunkLocation] = []
        for section in extraction.sections:
            section_rects: list[NormalizedRect] = []
            section_page: int | None = None
            for ref_id in section.source_reference_ids:
                ref = refs_by_id.get(ref_id)
                if ref is None:
                    continue
                section_rects.extend(ref.rects)
                if section_page is None and ref.page_number is not None:
                    section_page = ref.page_number
            # Fall back to the first rect's page when the source
            # reference has no scalar page_number (older parsers
            # populated only one or the other).
            if section_page is None and section_rects:
                section_page = section_rects[0].page
            if section_page is None:
                section_page = 1

            topic = topic_by_chunk.get(section.id)
            if topic is not None:
                summary = topic.summary
                topic_id: str | None = topic.id
                topic_label: str | None = topic.label
                row_source: ChunkSource = "ai_extraction"
                confidence = topic.confidence
            else:
                summary = None
                topic_id = None
                topic_label = None
                row_source = "parser"
                # Parser output is deterministic; surfacing 1.0 keeps
                # the side panel from rendering a misleading "low
                # confidence" badge on plain text.
                confidence = 1.0

            if page is not None and section_page != page:
                continue
            if source is not None and row_source != source:
                continue
            if min_confidence is not None and confidence < min_confidence:
                continue

            items.append(
                ChunkLocation(
                    chunk_id=section.id,
                    document_id=document_id,
                    document_version_id=version_id,
                    document_hash=version.sha256,
                    page=section_page,
                    rects=section_rects,
                    heading=section.heading,
                    snippet=section.text[:240],
                    summary=summary,
                    topic_id=topic_id,
                    topic_label=topic_label,
                    source=row_source,
                    confidence=confidence,
                    pipeline_version=pipeline_version,
                )
            )
            if len(items) >= limit:
                break

        return ChunkLocationsResponse(
            schema_version=CHUNK_LOCATION_SCHEMA_VERSION,  # explicit for OpenAPI clarity
            document_id=document_id,
            document_version_id=version_id,
            document_hash=version.sha256,
            parser_version=extraction.parser_version,
            items=items,
        )

    @router.get(
        "/documents/{document_id}/scopes",
        operation_id="list_document_scopes",
        response_model=DocumentScopesResponse,
    )
    def list_document_scopes(
        request: Request,
        document_id: str,
        current_user: User = Depends(require_viewer),
    ) -> Any:
        """Active workspace scope links for one document (#91, ADR-020 §2).

        Returns the list of :class:`Scope` rows the catalog persists for
        this document — ``(kind, ref, added_at, added_by)`` tuples
        identifying every active personal / Swym community / project
        link. Soft-removed rows are filtered out by
        :meth:`CatalogStore.list_scopes_for_document`, so the response
        reflects the **current** scope membership only.

        Returns ``404`` when the document does not exist OR when the
        caller's scope set does not include this document — D.5
        hidden-existence rule. The dedicated read surface lets clients
        inspect membership without inferring it from the
        ``GET /knowledge/catalog`` side-effect or from the upload
        response shape.
        """
        document = services.documents.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        assert_can_access_document(request=request, document_id=document_id, user=current_user)
        scopes = services.documents.catalog.list_scopes_for_document(document_id)
        return DocumentScopesResponse(scopes=scopes)

    @router.get(
        "/documents/{document_id}/lineage",
        operation_id="get_document_lineage",
        response_model=LineageResponse,
    )
    def get_document_lineage(
        request: Request,
        document_id: str,
        current_user: User = Depends(require_viewer),
    ) -> Any:
        """Version history for one document family (EPIC-C C.3, ADR-025).

        Returns a derived view of every :class:`DocumentVersion` in the
        family with the ``is_latest`` and ``superseded_by_version_id``
        fields the lineage modal needs filled in. Versions are sorted
        ASC by ``version_number`` so the modal renders v1 → vN
        top-to-bottom without re-sorting on the client.

        ``superseded_by_version_id`` is reconstructed from
        ``(version_number, status)`` ordering rather than read from a
        joined audit row — per ADR-025, the supersede chain is "the
        next-higher version-numbered sibling that exists in the
        family", not an arbitrary pointer.

        Returns ``404`` when the document does not exist OR when the
        caller's scope set does not include this document — D.5's
        hidden-existence rule: enumeration probes can't distinguish
        the two cases. Never raises on an empty family (a
        freshly-created family with one version is a valid response).
        """
        document = services.documents.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        assert_can_access_document(request=request, document_id=document_id, user=current_user)
        return _build_lineage_response(document)

    @router.get(
        "/documents/{document_id}/confidence",
        operation_id="get_document_confidence",
        response_model=DocumentConfidenceResponse,
    )
    def get_document_confidence(
        request: Request,
        document_id: str,
        version_id: str | None = Query(default=None, min_length=1, max_length=200),
        current_user: User = Depends(require_viewer),
    ) -> Any:
        """Confidence dashboard view for one document (converged plan §C.1).

        Returns the composite confidence score (overall + per-signal
        breakdown), the HITL routing outcome, and the auto-validate
        threshold this deployment is tuned to. The dashboard renders
        this as one panel on the document detail page; nothing here
        triggers scoring — the data has been produced by
        :class:`ConfidenceScorer` on the NEEDS_REVIEW transition since
        EPIC-A slice 1 (ADR-023).

        ``?version_id=`` is optional. Without it the route reports on
        ``document.latest_version_id`` — the natural default for the
        per-document panel. Operators inspecting drift between two
        passes pass the explicit version id; the response carries the
        resolved id so the frontend can confirm.

        ``has_score=false`` when the resolved version exists but no
        :class:`ConfidenceScore` was persisted (scorer disabled via
        ``KW_HITL_DISABLE_SCORER``, or the version predates scorer
        wiring). Routing / validation fields still surface whenever a
        :class:`ValidationMetadata` row exists; the UI renders an
        empty-state for the score itself while keeping the routing
        outcome visible.

        Tombstone semantics mirror the sibling per-version content
        routes (ADR-027 §3): a fully-purged document family surfaces
        as 410 Gone, an individual PURGED version surfaces as 410 with
        the per-version tombstone envelope. Hidden-existence (D.5)
        applies to non-purged invisibility: missing document or a
        version_id not in the family returns plain 404.
        """
        document = services.documents.get_document(document_id)
        if document is None:
            archived = services.documents.catalog._get_document_including_archived(  # type: ignore[attr-defined]
                document_id,
            )
            if archived is not None and _all_versions_purged(archived):
                raise _purged_document_error(document_id)
            raise HTTPException(status_code=404, detail="Document not found.")
        assert_can_access_document(request=request, document_id=document_id, user=current_user)

        target_version_id = document.latest_version_id if version_id is None else version_id

        try:
            version = _get_version_including_archived(
                services=services,
                document_id=document_id,
                version_id=target_version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Version not found in document.") from exc

        if version.status is DocumentVersionStatus.PURGED:
            raise _purged_version_error(document_id=document_id, version=version)

        metadata = services.validation_metadata.get(version.id)
        threshold = Settings().hitl_auto_validate_threshold

        confidence_score = metadata.confidence_score if metadata is not None else None
        return DocumentConfidenceResponse(
            document_id=document.id,
            version_id=version.id,
            version_number=version.version_number,
            has_score=confidence_score is not None,
            confidence_score=confidence_score,
            routing_decision=metadata.routing_decision if metadata is not None else None,
            validation_method=metadata.validation_method if metadata is not None else None,
            validation_actor=metadata.validation_actor if metadata is not None else None,
            auto_validate_threshold=threshold,
        )

    @router.get(
        "/documents/{document_id}/high-value-chunks",
        operation_id="get_document_high_value_chunks",
        response_model=HighValueChunksResponse,
    )
    def get_document_high_value_chunks(
        request: Request,
        document_id: str,
        version_id: str | None = Query(default=None, min_length=1, max_length=200),
        limit: int = Query(default=20, ge=1, le=100),
        current_user: User = Depends(require_viewer),
    ) -> Any:
        """Rank the chunks of one version by a composite importance
        score (converged plan §C.2).

        The score is a weighted sum of four normalised signals —
        claim count, process-step count, chunk-relation graph
        degree, and entity-mention density — surfaced on the wire
        per-chunk so the UI can explain *why* a chunk ranks high.
        Defaults to ``document.latest_version_id``; operators
        inspecting drift between two passes pass the explicit
        version id.

        Cold-start documents (extraction has not run yet, or the
        version produced no semantic output) return an empty
        ``items`` list with HTTP 200 — the UI renders that as a
        friendly "no chunks yet" state. Tombstone semantics mirror
        the sibling per-version content routes (ADR-027 §3): a
        fully-purged document family surfaces as 410 Gone, an
        individual PURGED version surfaces as 410 with the
        per-version tombstone envelope. Hidden-existence (D.5)
        applies to non-purged invisibility: missing document or a
        version_id not in the family returns plain 404.
        """
        document = services.documents.get_document(document_id)
        if document is None:
            archived = services.documents.catalog._get_document_including_archived(  # type: ignore[attr-defined]
                document_id,
            )
            if archived is not None and _all_versions_purged(archived):
                raise _purged_document_error(document_id)
            raise HTTPException(status_code=404, detail="Document not found.")
        assert_can_access_document(request=request, document_id=document_id, user=current_user)

        target_version_id = document.latest_version_id if version_id is None else version_id

        try:
            version = _get_version_including_archived(
                services=services,
                document_id=document_id,
                version_id=target_version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Version not found in document.") from exc

        if version.status is DocumentVersionStatus.PURGED:
            raise _purged_version_error(document_id=document_id, version=version)

        # Cold-start cases collapse to an empty items list with HTTP
        # 200. The semantic document is the chunk pool the ranker
        # operates over; if extraction hasn't produced one yet
        # (UPLOADED / EXTRACTING / FAILED), there's nothing to rank.
        try:
            semantic = services.semantic_outputs.get(
                document_id=document.id,
                version_id=version.id,
            )
        except (FileNotFoundError, KeyError):
            return HighValueChunksResponse(
                document_id=document.id,
                version_id=version.id,
                version_number=version.version_number,
                total_chunks=0,
                weights=HighValueChunksService().weights,
                items=[],
            )

        # The ranker is stateless and cheap to instantiate per call
        # — same posture as the sibling EPIC-C similarity service.
        ranker = HighValueChunksService()
        claims = services.claim_store.list_for_version(version.id)
        processes = services.process_store.list_for_version(version.id)
        items = ranker.rank(
            semantic=semantic,
            claims=claims,
            processes=processes,
            limit=limit,
        )
        return HighValueChunksResponse(
            document_id=document.id,
            version_id=version.id,
            version_number=version.version_number,
            total_chunks=len(semantic.sections),
            weights=ranker.weights,
            items=items,
        )

    @router.get(
        "/documents/{document_id}/similar",
        operation_id="get_similar_documents",
        response_model=SimilarDocumentsResponse,
    )
    def get_similar_documents(
        request: Request,
        document_id: str,
        k: int = Query(default=5, ge=1, le=50),
        current_user: User = Depends(require_viewer),
    ) -> Any:
        """Top-K similar documents by topic-Jaccard (EPIC-C C.3, ADR-025 §3).

        Uses :class:`DocumentSimilarityService` over the wired
        ``DocumentTopicProvider`` adapter. Cold-start tolerance: when
        the query document has no projected topics yet (knowledge layer
        disabled, pre-validation, or no topic clusters of size ≥ 2),
        returns ``results: []`` with HTTP 200 rather than a 5xx — the
        frontend renders "no similar documents yet" gracefully.

        ``k`` is clamped to ``[1, 50]`` by FastAPI's ``Query`` validator;
        out-of-range values produce a 422 from FastAPI itself.

        D.5: 404 when the base document is hidden from the caller, AND
        neighbour rows are filtered down to documents in the caller's
        scope set so we don't surface "you have a similar doc you
        can't actually open".
        """
        document = services.documents.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        assert_can_access_document(request=request, document_id=document_id, user=current_user)
        ranked = services.document_similarity.top_k(document_id, k=k)
        settings = Settings()
        results: list[SimilarDocument] = []
        for neighbor_id, score in ranked:
            # Filter neighbours to scopes the caller can read. Cheap on
            # the in-memory store (set lookup per neighbour) and
            # acceptable on SQLite (one ``list_scopes_for_document``
            # round-trip per neighbour, bounded by ``k <= 50``).
            if not user_can_access(
                user=current_user,
                document_id=neighbor_id,
                catalog=services.documents.catalog,
                settings=settings,
            ):
                continue
            row = _build_similar_row(
                neighbor_id=neighbor_id,
                similarity=score,
                catalog=services.documents,
            )
            # Drop neighbors whose ``Document`` row vanished between
            # ``top_k`` and the per-row catalog read (extremely
            # unlikely but keeps the response shape honest if a
            # deletion races us).
            if row is not None:
                results.append(row)
        return SimilarDocumentsResponse(document_id=document_id, results=results)

    @router.post(
        "/documents/{document_id}/versions/{version_id}/validate",
        operation_id="validate_version",
        response_model=SemanticDocument,
    )
    async def validate_version(
        http_request: Request,
        document_id: str,
        version_id: str,
        request: ReviewRequest = Body(default_factory=ReviewRequest),
        current_user: User = Depends(require_reviewer),
    ) -> Any:
        # D.5: refuse before any review work happens. A scope-blocked
        # caller must not be able to flip another user's doc status.
        assert_can_access_document(request=http_request, document_id=document_id, user=current_user)

        # Build the async-side-effects dispatcher when the operator has
        # opted in. None disables the indirection and ReviewService runs
        # the projection inline (the historical contract; what every
        # existing test asserts).
        dispatcher: Callable[[Callable[[], None]], None] | None = None
        if services.settings.knowledge_projection_async:
            dispatcher = _make_background_dispatcher(http_request.app)

        # The handler chain is sync. ``asyncio.to_thread`` keeps the
        # event loop free while gunicorn's single uvicorn worker handles
        # other requests. Without this, the ``async def`` would block
        # the loop on every catalog write.
        return await asyncio.to_thread(
            _dispatch_review,
            handler=services.review.handle_validation,
            document_id=document_id,
            version_id=version_id,
            reviewer_note=request.reviewer_note,
            actor=current_user.id,
            extra_handler_kwargs={"side_effect_dispatcher": dispatcher},
        )

    @router.post(
        "/documents/{document_id}/versions/{version_id}/reject",
        operation_id="reject_version",
        response_model=SemanticDocument,
    )
    def reject_version(
        http_request: Request,
        document_id: str,
        version_id: str,
        request: ReviewRequest = Body(default_factory=ReviewRequest),
        current_user: User = Depends(require_reviewer),
    ) -> Any:
        # D.5: refuse before any review work happens.
        assert_can_access_document(request=http_request, document_id=document_id, user=current_user)
        return _dispatch_review(
            handler=services.review.handle_rejection,
            document_id=document_id,
            version_id=version_id,
            reviewer_note=request.reviewer_note,
            actor=current_user.id,
        )

    @router.post(
        "/documents/{document_id}/versions/{version_id}/reset_to_review",
        operation_id="reset_version_to_review",
        response_model=SemanticDocument,
    )
    def reset_version_to_review(
        http_request: Request,
        document_id: str,
        version_id: str,
        request: ReviewRequest = Body(default_factory=ReviewRequest),
        current_user: User = Depends(require_reviewer),
    ) -> Any:
        """Demote a VALIDATED or REJECTED version back to NEEDS_REVIEW.

        Manual reviewer-override path so an operator can re-open a
        previously-validated or previously-rejected version when new
        information surfaces. The FSM edges
        (VALIDATED → NEEDS_REVIEW, REJECTED → NEEDS_REVIEW) live in
        :data:`ALLOWED_TRANSITIONS`; the audit event
        ``review.demoted`` records the actor + note.
        """
        # D.5: refuse before any review work happens.
        assert_can_access_document(request=http_request, document_id=document_id, user=current_user)
        return _dispatch_review(
            handler=services.review.handle_demote_to_review,
            document_id=document_id,
            version_id=version_id,
            reviewer_note=request.reviewer_note,
            actor=current_user.id,
        )

    return router


def _all_versions_purged(document: Document) -> bool:
    """Return True when every version in the family is ``PURGED``.

    ADR-027 §3 / slice 6: a document whose versions are all purged
    surfaces as HTTP 410 Gone instead of 404 — consumers can render a
    tombstone card. A doc with at least one non-purged version is
    treated as a normal hidden-archived row (404 to standard reads
    via the catalog filter; admin tool can still reach it).
    """
    if not document.versions:
        return False
    return all(v.status is DocumentVersionStatus.PURGED for v in document.versions)


def _purged_document_error(document_id: str) -> ApiError:
    """Build the ADR-027 §3 410 Gone envelope for a fully-purged document."""
    return ApiError(
        status_code=410,
        code=ErrorCode.PURGED,
        message=(
            f"Document {document_id!r} was purged; the source artifacts are no longer available."
        ),
        retryable=False,
        remediation=(
            "Contact your admin to recover from audit log if needed; "
            "the catalog row is preserved as an audit trace."
        ),
    )


def _purged_version_error(*, document_id: str, version: DocumentVersion) -> ApiError:
    """Build the ADR-027 §3 410 Gone envelope for a purged version.

    Surfaces the tombstone URI on ``error.detail`` so audit consumers
    can correlate without joining against the audit log; the URI is
    parseable per ADR-027 §3 (``tombstone:purged:<doc>:<ver>:<iso>``).
    Standard ``Document.storage_uri`` reads do NOT return the
    tombstone — the 410 envelope is the only sanctioned surface for
    it.
    """
    return ApiError(
        status_code=410,
        code=ErrorCode.PURGED,
        message=(
            f"Version {version.id!r} of document {document_id!r} was "
            "purged; the source artifacts are no longer available."
        ),
        retryable=False,
        remediation=("Contact your admin to recover from audit log if needed."),
        detail={
            "code": ErrorCode.PURGED,
            "document_id": document_id,
            "version_id": version.id,
            "tombstone_uri": version.storage_uri,
        },
    )


def _get_version_including_archived(
    *,
    services: PipelineServices,
    document_id: str,
    version_id: str,
) -> DocumentVersion:
    """Resolve a version even when its parent document is archived.

    Slice 6: PURGED versions live on archived documents (the §1.3
    archive-then-purge precondition guarantees that), so the standard
    :meth:`DocumentService.get_version` path — which delegates to
    :meth:`CatalogStore.get_version` — would still see them, but the
    catalog's archived filter makes the document fetch return None.
    Reach into ``_get_document_including_archived`` so the route can
    surface a 410 instead of a 404 for purged content.
    """
    archived = services.documents.catalog._get_document_including_archived(  # type: ignore[attr-defined]
        document_id,
    )
    if archived is None:
        raise KeyError("Document not found.")
    for candidate in archived.versions:
        if candidate.id == version_id:
            return candidate
    raise KeyError("Document version not found.")


def _topics_for_version(
    *,
    store: Any,
    document_id: str,
    version_id: str,
) -> list[Any]:
    """Fetch every persisted topic for a given (document, version) pair.

    The store's :meth:`list_for_document` is cursor-paginated; we walk
    the cursor here because the chunk-locations route needs the full
    set to join against sections. Per-version filtering is done after
    fetch — the store's read API takes ``document_id`` only, and a
    version-aware index would be a follow-up if this ever shows up in
    a flame graph.
    """
    collected: list[Any] = []
    cursor: str | None = None
    while True:
        page, cursor = store.list_for_document(document_id, cursor=cursor)
        collected.extend(t for t in page if t.version_id == version_id)
        if cursor is None:
            break
    return collected


def _build_lineage_response(document: Document) -> LineageResponse:
    """Project a :class:`Document` into the lineage modal's response shape.

    The supersede chain is reconstructed from ``(version_number,
    status)`` ordering: any ``SUPERSEDED`` row is annotated with the
    id of its next-higher version-numbered sibling. ADR-025 documents
    why we don't read ``superseded_by_version_id`` from the audit
    table — the chain *is* the version sequence, and any other pointer
    would diverge if a future migration replays validation events.
    """
    sorted_versions = sorted(document.versions, key=lambda v: v.version_number)
    if not sorted_versions:
        return LineageResponse(
            document_id=document.id,
            family_filename=document.original_filename,
            versions=[],
        )
    latest_version_number = max(v.version_number for v in sorted_versions)
    family_filename = next(
        (v.filename for v in sorted_versions if v.version_number == latest_version_number),
        document.original_filename,
    )
    by_number: dict[int, DocumentVersion] = {v.version_number: v for v in sorted_versions}
    rows: list[LineageVersion] = []
    for version in sorted_versions:
        superseded_by: str | None = None
        if version.status == DocumentVersionStatus.SUPERSEDED:
            successor = by_number.get(version.version_number + 1)
            if successor is not None:
                superseded_by = successor.id
        rows.append(
            LineageVersion(
                id=version.id,
                version_number=version.version_number,
                filename=version.filename,
                status=version.status,
                sha256=version.sha256,
                file_size=version.file_size,
                is_latest=(version.version_number == latest_version_number),
                duplicate_of_version_id=version.duplicate_of_version_id,
                superseded_by_version_id=superseded_by,
                ingested_at=version.created_at,
            )
        )
    return LineageResponse(
        document_id=document.id,
        family_filename=family_filename,
        versions=rows,
    )


def _build_similar_row(
    *,
    neighbor_id: str,
    similarity: float,
    catalog: DocumentService,
) -> SimilarDocument | None:
    """Build one :class:`SimilarDocument` row for the similar-docs response.

    Returns ``None`` if the neighbor's catalog row vanished between
    the similarity ranking and this read. The caller filters those
    out so the wire shape stays consistent.

    ``family_filename`` mirrors the lineage convention — the *latest*
    version's filename, which is what the modal labels the row by.
    ``latest_version_status`` deliberately reports the actual latest,
    including ``SUPERSEDED`` if the family is in a stale state; the
    catalog-view route is the surface that filters those out.
    """
    document = catalog.get_document(neighbor_id)
    if document is None or not document.versions:
        return None
    latest = max(document.versions, key=lambda v: v.version_number)
    return SimilarDocument(
        document_id=neighbor_id,
        family_filename=latest.filename,
        similarity=similarity,
        latest_version_status=latest.status,
    )


def _list_documents_with_scope(
    *,
    services: PipelineServices,
    caller_scopes: tuple[ScopeRef, ...],
    limit: int,
    cursor: str | None,
    status_filter: frozenset[DocumentVersionStatus] | None,
    filename_query: str | None,
) -> tuple[list[Document], str | None]:
    """Paginate ``GET /documents`` honouring the caller's scope set.

    Two paths:

    - :data:`ALL_SCOPES_SENTINEL` (legacy ``KW_AUTH_MODE=disabled``)
      → fall back to the unscoped ``list_documents_page``. Same shape,
      same cursor codec, every document visible.
    - Scoped path → for the strict default (a single
      ``personal:<user.id>``) we delegate to
      :meth:`CatalogStore.list_documents_in_scope` so the predicate
      runs at the SQL layer. The status / filename filters are applied
      in-memory because the scoped store method doesn't index them
      yet — at the catalog sizes D.5 covers (a single user's personal
      scope), this is a tiny set.

    Multi-scope merges (the future case where the caller's scope set
    has both ``personal:*`` and ``swym_community:*``) are not wired
    yet — D.3 will add the membership lookup and this helper will
    iterate the scope set and merge cursor-comparable. The strict
    "personal-only" default keeps that follow-up small.
    """
    if caller_scopes == ALL_SCOPES_SENTINEL:
        # Legacy disabled-mode bypass: behaviour matches the pre-D.5
        # route. Documented in :mod:`app.services.auth.disabled` /
        # :func:`scope_filter.resolve_caller_scopes`.
        return services.documents.list_documents_page(
            limit=limit,
            cursor=cursor,
            status_filter=status_filter,
            filename_query=filename_query,
        )

    if len(caller_scopes) == 1:
        scope = caller_scopes[0]
        page, _store_cursor = services.documents.catalog.list_documents_in_scope(
            scope.kind,
            scope.ref,
            cursor=cursor,
            limit=limit,
        )
        # Apply the post-fetch filters in-memory. The scope-indexed
        # path doesn't accept ``status_filter`` / ``filename_query``
        # today — adding them is a follow-up once the SQLite reverse
        # index proves out under heavier scope membership.
        if status_filter is not None or filename_query is not None:
            page = _filter_scoped_page_in_memory(
                services=services,
                scope=scope,
                page=page,
                limit=limit,
                status_filter=status_filter,
                filename_query=filename_query,
                seed_cursor=cursor,
            )
        # Mirror the legacy ``list_documents_page`` cursor contract: a
        # full page (``len(items) == limit``) always emits a cursor
        # even when nothing follows it, so the caller's "walk until
        # next_cursor is None" loop terminates with one extra empty
        # page rather than mid-stream. A short page signals end-of-
        # stream by emitting ``None``.
        if len(page) < limit:
            return page, None
        last = page[-1]
        return page, _encode_cursor((last.created_at, last.id))

    # Multi-scope merge — placeholder for D.3 community + project
    # membership. Intentionally raises so we don't silently degrade to
    # "no filter" if a future caller path forgets to widen this branch.
    raise NotImplementedError("Multi-scope reads ship with EPIC-D D.3 (Swym membership client).")


def _filter_scoped_page_in_memory(
    *,
    services: PipelineServices,
    scope: ScopeRef,
    page: list[Document],
    limit: int,
    status_filter: frozenset[DocumentVersionStatus] | None,
    filename_query: str | None,
    seed_cursor: str | None,
) -> list[Document]:
    """Apply status / filename filters on top of a scoped page.

    The scoped store method already paginated, so a filter that drops
    rows from the page would silently shorten it. Walk forward inside
    the same scope until we either fill ``limit`` matches or run out
    of data. Returns the (possibly trimmed) match list; the caller
    derives the next cursor from the last returned doc, mirroring the
    legacy ``list_documents_page`` contract.
    """

    def _matches(doc: Document) -> bool:
        if (
            filename_query is not None
            and filename_query.lower() not in doc.original_filename.lower()
        ):
            return False
        if status_filter is not None:
            if not doc.versions:
                return False
            latest = next(
                (v for v in doc.versions if v.id == doc.latest_version_id),
                doc.versions[-1],
            )
            if latest.status not in status_filter:
                return False
        return True

    matches: list[Document] = [d for d in page if _matches(d)]
    if len(matches) >= limit:
        return matches[:limit]

    # Walk forward inside this scope until we fill ``limit`` matches.
    walk_cursor = seed_cursor
    while len(matches) < limit:
        if not page:
            break
        # Use the last doc of the previous fetch to seed the next page.
        walk_cursor = _encode_cursor((page[-1].created_at, page[-1].id))
        page, _ = services.documents.catalog.list_documents_in_scope(
            scope.kind,
            scope.ref,
            cursor=walk_cursor,
            limit=limit,
        )
        if not page:
            break
        for doc in page:
            if _matches(doc):
                matches.append(doc)
                if len(matches) >= limit:
                    break

    return matches[:limit]


def _job_id_for(version_id: str) -> str:
    """Opaque job-id derivation for the async extraction queue.

    Scoped to ``(document_id, version_id)`` per the ADR-006 PR-2
    contract; the version_id is already globally unique inside the
    catalog so prefixing with ``ext-`` is enough to give clients a
    stable string they can log without colliding with raw version
    UUIDs.
    """
    return f"ext-{version_id}"


def _queue_full_error() -> ApiError:
    """Build the ADR-006 PR-2 503 envelope for ``QueueFull``.

    ``Retry-After: 5`` matches the queue-size bound (16 jobs, single
    worker by default — at typical pdfplumber wall-time the head-of-line
    drains within seconds). ``retryable=True`` so frontends can render
    a "try again" hint instead of a hard error banner.
    """
    return ApiError(
        status_code=503,
        code=ErrorCode.QUEUE_FULL,
        message="Extraction queue is at capacity. Please retry shortly.",
        retryable=True,
        remediation=(
            "Wait a few seconds and resubmit. If the queue stays full "
            "for more than a minute, your operator may need to raise "
            "KW_EXTRACTION_QUEUE_SIZE or KW_EXTRACTION_WORKERS."
        ),
        headers={"Retry-After": "5"},
    )


def _run_inline_extract(
    *,
    services: PipelineServices,
    document_id: str,
    version_id: str,
    idempotency_key: str | None,
    route: str,
    request_hash: str,
    actor: str | None = None,
) -> Any:
    """Inline (synchronous) extract — the pre-ADR-006 behaviour.

    Runs the parser on the request thread and returns the persisted
    :class:`RawExtraction`. Idempotency cache is populated on success
    so a replay of the same key returns the same payload.
    """
    try:
        result = services.extraction_jobs.extract(
            document_id=document_id, version_id=version_id, actor=actor
        )
        _store_idempotency(
            store=services.idempotency,
            idempotency_key=idempotency_key,
            route=route,
            request_hash=request_hash,
            result=result.model_dump(mode="json"),
        )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExtractionFailed as exc:
        raise HTTPException(status_code=422, detail=exc.reason) from exc


def _run_inline_retry(
    *,
    services: PipelineServices,
    document_id: str,
    version_id: str,
    idempotency_key: str | None,
    route: str,
    request_hash: str,
    actor: str | None = None,
) -> Any:
    """Inline (synchronous) retry — mirrors :func:`_run_inline_extract`."""
    try:
        result = services.extraction_jobs.retry_extract(
            document_id=document_id, version_id=version_id, actor=actor
        )
        _store_idempotency(
            store=services.idempotency,
            idempotency_key=idempotency_key,
            route=route,
            request_hash=request_hash,
            result=result.model_dump(mode="json"),
        )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExtractionFailed as exc:
        raise HTTPException(status_code=422, detail=exc.reason) from exc


def _enqueue_extract(
    *,
    request: Request,
    services: PipelineServices,
    document_id: str,
    version_id: str,
    actor: str | None = None,
) -> Response:
    """Async-mode extract: ``STORED → QUEUED_FOR_EXTRACTION`` then enqueue.

    The FSM transition runs first so a concurrent caller racing two
    extract submissions doesn't enqueue twice — the second
    ``update_status`` call raises ``IllegalTransition`` (translated to
    409) because the predecessor predicate no longer matches. The
    queue ``put`` is awaited synchronously: ``InMemoryExtractionQueue``
    raises :class:`QueueFull` immediately without blocking, so the
    request thread isn't held hostage when the worker is overloaded.
    """
    queue = request.app.state.extraction_queue
    if queue is None:  # defence-in-depth — lifespan invariant violated
        raise HTTPException(status_code=503, detail="Extraction queue not initialised.")
    try:
        services.documents.update_status(
            document_id,
            version_id,
            DocumentVersionStatus.QUEUED_FOR_EXTRACTION,
            actor=actor,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _put_and_build_snapshot(
        queue=queue,
        document_id=document_id,
        version_id=version_id,
        actor=actor,
    )


def _enqueue_retry(
    *,
    request: Request,
    services: PipelineServices,
    document_id: str,
    version_id: str,
    actor: str | None = None,
) -> Response:
    """Async-mode retry: ``FAILED → QUEUED_FOR_EXTRACTION`` then enqueue.

    Mirrors :class:`ExtractionJobService.retry_extract` semantics —
    refuses with 409 from anything other than ``FAILED`` so the review
    gate stays intact. The actual ``extraction.retried`` audit event
    is emitted by the worker when it dequeues, not here, because the
    audit timestamp should reflect the retry attempt's *start* of work,
    not its enqueue.
    """
    queue = request.app.state.extraction_queue
    if queue is None:
        raise HTTPException(status_code=503, detail="Extraction queue not initialised.")
    try:
        version = services.documents.get_version(document_id=document_id, version_id=version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if version.status is not DocumentVersionStatus.FAILED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Retry only allowed from FAILED; version is currently {version.status.value}."
            ),
        )
    try:
        services.documents.update_status(
            document_id,
            version_id,
            DocumentVersionStatus.QUEUED_FOR_EXTRACTION,
            actor=actor,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _put_and_build_snapshot(
        queue=queue,
        document_id=document_id,
        version_id=version_id,
        actor=actor,
    )


def _put_and_build_snapshot(
    *,
    queue: Any,
    document_id: str,
    version_id: str,
    actor: str | None = None,
) -> Response:
    """Enqueue an :class:`ExtractionRequest` and return the 202 receipt.

    ``put`` is a coroutine on the queue protocol; the
    :class:`InMemoryExtractionQueue` impl never actually awaits — it
    calls ``put_nowait`` and raises :class:`QueueFull` when the bound
    is hit. We still ``async``-call it through ``asyncio.run`` to keep
    the route handler synchronous. Wrapping in a fresh event-loop call
    would deadlock under FastAPI's running loop, so we instead use the
    queue's underlying non-blocking put through ``_queue.put_nowait``
    when available; the protocol's ``put`` raises immediately so we
    can call it directly via the synchronous helper.

    ``actor`` rides the :class:`ExtractionRequest` from enqueue to
    dequeue so the worker can attribute the ``extraction.*`` audit
    events to the human who pressed the button.
    """
    extraction_request = ExtractionRequest(
        document_id=document_id,
        version_id=version_id,
        actor=actor,
    )
    try:
        # ``InMemoryExtractionQueue.put`` is declared ``async`` but its
        # body is synchronous (``put_nowait`` either succeeds or raises
        # ``QueueFull``). Reach through to the underlying ``asyncio.Queue``
        # via ``put_nowait`` so this synchronous route doesn't need a
        # running event loop to enqueue.
        queue._queue.put_nowait(extraction_request)
    except Exception as exc:  # noqa: BLE001 — translate every failure mode
        # asyncio.QueueFull is the only documented failure; bubble up
        # via a 503 with retry guidance regardless of the concrete type
        # so a future durable queue can swap in without route changes.
        from asyncio import QueueFull as _AsyncQueueFull

        if isinstance(exc, (QueueFull, _AsyncQueueFull)):
            raise _queue_full_error() from exc
        raise
    # #40 / 2026-05-14 progress plan: queue-depth gauge after every
    # enqueue. Operators watching the structured-log feed (or piping it
    # into Prometheus via a log-to-metric exporter) get a live
    # depth-over-time signal without us coupling to a metrics framework.
    # Measured route-side after the put: that's the operator-relevant
    # value ("queue is now N deep; is N approaching the bound?").
    qsize = queue.qsize()
    log.info(
        "extraction.queue_depth",
        extra={
            "qsize": qsize,
            "maxsize": queue.maxsize,
            "is_full": queue.is_full(),
            "document_id": document_id,
            "version_id": version_id,
        },
    )
    snapshot = ExtractionJobSnapshot(
        job_id=_job_id_for(version_id),
        document_id=document_id,
        version_id=version_id,
        status=DocumentVersionStatus.QUEUED_FOR_EXTRACTION,
        queue_position=qsize,
    )
    return Response(
        status_code=202,
        content=snapshot.model_dump_json(),
        media_type="application/json",
    )


def _dispatch_review(
    *,
    handler: Any,
    document_id: str,
    version_id: str,
    reviewer_note: str | None,
    actor: str,
    extra_handler_kwargs: dict[str, Any] | None = None,
) -> Any:
    """Translate :class:`ReviewService` domain exceptions into HTTP envelopes.

    The service raises plain ``KeyError`` (missing entity → 404) and
    ``ValueError`` (FSM precondition failure → 409 with the structured
    ``LIFECYCLE_CONFLICT`` envelope). Side-effect failures (projector,
    entity extractor) are caught and logged inside the service — they
    never reach this layer.

    ``extra_handler_kwargs`` lets the validate route forward an opt-in
    ``side_effect_dispatcher`` to ``handle_validation`` without
    widening this helper's positional API for the reject path that
    doesn't need one.
    """
    kwargs: dict[str, Any] = {
        "document_id": document_id,
        "version_id": version_id,
        "reviewer_note": reviewer_note,
        "actor": actor,
    }
    if extra_handler_kwargs:
        kwargs.update(extra_handler_kwargs)
    try:
        return handler(**kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=409,
            code=ErrorCode.LIFECYCLE_CONFLICT,
            message=str(exc),
            retryable=False,
            remediation=(
                "The version's lifecycle status doesn't permit this "
                "transition. Refresh the document and re-evaluate the "
                "available actions."
            ),
        ) from exc


def _make_background_dispatcher(
    app: Any,
) -> Callable[[Callable[[], None]], None]:
    """Return a dispatcher that schedules ``fn`` as a background asyncio task.

    Captured from inside an ``async def`` route, so ``get_running_loop``
    succeeds. The dispatcher itself is invoked from the threadpool
    inside ``ReviewService.handle_validation`` (``asyncio.to_thread``
    in the route hands the sync handler off to a worker thread). It
    schedules a coroutine onto the loop with
    ``run_coroutine_threadsafe`` and stores the resulting task in
    ``app.state.background_tasks`` so the GC can't reap it mid-flight.
    The lifespan drains this set on shutdown with a bounded timeout.
    """
    loop = asyncio.get_running_loop()
    background_tasks: set[asyncio.Task[None]] = app.state.background_tasks

    async def _spawn(fn: Callable[[], None]) -> None:
        # Side-effects are sync (they call into the projector / entity
        # extractor which use blocking SDK clients). Push them off the
        # loop so concurrent validations don't queue head-of-line.
        task = asyncio.create_task(asyncio.to_thread(fn))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    def dispatch(fn: Callable[[], None]) -> None:
        # Called from the worker thread that's running handle_validation.
        # ``run_coroutine_threadsafe`` is the canonical loop-from-thread
        # bridge; ``.result()`` is on the scheduling, not the side-effect
        # itself, so we don't actually wait for the projection here.
        future = asyncio.run_coroutine_threadsafe(_spawn(fn), loop)
        try:
            future.result(timeout=5)
        except Exception:  # noqa: BLE001  # pragma: no cover - schedule failure
            # Defensive: ``run_coroutine_threadsafe`` only fails if the
            # loop is closed, which means shutdown is already underway
            # and the validate response is moot. Log and let validate
            # return; the projection is lost but the catalog state is
            # already committed.
            log.exception("knowledge.projection.background_schedule_failed")

    return dispatch
