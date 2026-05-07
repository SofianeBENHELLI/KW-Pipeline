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

from typing import Any
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response

from app.dependencies import PipelineServices
from app.errors import ApiError, ErrorCode
from app.models.document import DocumentVersionStatus
from app.schemas.document import (
    Document,
    DocumentListResponse,
    DocumentVersion,
    LineageResponse,
    LineageVersion,
    SimilarDocument,
    SimilarDocumentsResponse,
)
from app.schemas.extraction import RawExtraction
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
from app.services.idempotency_store import hash_json_body
from app.settings import Settings

from ._helpers import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    MIN_PAGE_LIMIT,
    ReviewRequest,
    _check_idempotency,
    _store_idempotency,
)

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
        response_model=RawExtraction,
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
        try:
            result = services.extraction_jobs.extract(
                document_id=document_id, version_id=version_id
            )
            _store_idempotency(
                store=services.idempotency,
                idempotency_key=idempotency_key,
                route=_route,
                request_hash=_req_hash,
                result=result.model_dump(mode="json"),
            )
            return result
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ExtractionFailed as exc:
            raise HTTPException(status_code=422, detail=exc.reason) from exc

    @router.post(
        "/documents/{document_id}/versions/{version_id}/retry-extraction",
        operation_id="retry_extraction",
        response_model=RawExtraction,
    )
    def retry_extraction(
        document_id: str,
        version_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        _user: User = Depends(require_contributor),
    ) -> Any:
        """Retry extraction for a previously-FAILED version (#87).

        Returns the fresh ``RawExtraction`` on success, ``422`` with the
        new failure reason on a re-fail, ``404`` if the version doesn't
        exist, or ``409`` if the version isn't in ``FAILED`` (review
        states stay frozen — retry never bypasses the gate).
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
        try:
            result = services.extraction_jobs.retry_extract(
                document_id=document_id, version_id=version_id
            )
            _store_idempotency(
                store=services.idempotency,
                idempotency_key=idempotency_key,
                route=_route,
                request_hash=_req_hash,
                result=result.model_dump(mode="json"),
            )
            return result
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ExtractionFailed as exc:
            raise HTTPException(status_code=422, detail=exc.reason) from exc

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
    )
    def generate_semantic_document(
        request: Request,
        document_id: str,
        version_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        current_user: User = Depends(require_contributor),
    ) -> Any:
        # D.5: hidden-existence — refuse before any catalog work happens.
        assert_can_access_document(request=request, document_id=document_id, user=current_user)
        _route = "/documents/{document_id}/versions/{version_id}/semantic"
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
        try:
            result = services.semantic_outputs.generate(
                document_id=document_id, version_id=version_id
            )
            _store_idempotency(
                store=services.idempotency,
                idempotency_key=idempotency_key,
                route=_route,
                request_hash=_req_hash,
                result=result.model_dump(mode="json"),
            )
            return result
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
    def validate_version(
        http_request: Request,
        document_id: str,
        version_id: str,
        request: ReviewRequest = Body(default_factory=ReviewRequest),
        current_user: User = Depends(require_reviewer),
    ) -> Any:
        # D.5: refuse before any review work happens. A scope-blocked
        # caller must not be able to flip another user's doc status.
        assert_can_access_document(request=http_request, document_id=document_id, user=current_user)
        return _dispatch_review(
            handler=services.review.handle_validation,
            document_id=document_id,
            version_id=version_id,
            reviewer_note=request.reviewer_note,
            actor=current_user.id,
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


def _dispatch_review(
    *,
    handler: Any,
    document_id: str,
    version_id: str,
    reviewer_note: str | None,
    actor: str,
) -> Any:
    """Translate :class:`ReviewService` domain exceptions into HTTP envelopes.

    The service raises plain ``KeyError`` (missing entity → 404) and
    ``ValueError`` (FSM precondition failure → 409 with the structured
    ``LIFECYCLE_CONFLICT`` envelope). Side-effect failures (projector,
    entity extractor) are caught and logged inside the service — they
    never reach this layer.
    """
    try:
        return handler(
            document_id=document_id,
            version_id=version_id,
            reviewer_note=reviewer_note,
            actor=actor,
        )
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
