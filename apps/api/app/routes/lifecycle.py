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

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response

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
from app.schemas.semantic_document import SemanticDocument
from app.services.auth import User, get_current_user
from app.services.catalog_store import InvalidCursor
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionFailed
from app.services.idempotency_store import hash_json_body

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
            items, next_cursor = services.documents.list_documents_page(
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
    def get_document(document_id: str) -> Any:
        document = services.documents.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return document

    @router.post(
        "/documents/{document_id}/versions/{version_id}/extract",
        operation_id="extract_version",
        response_model=RawExtraction,
    )
    def extract_document(
        document_id: str,
        version_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Any:
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
    def get_extraction(document_id: str, version_id: str) -> Any:
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
        document_id: str,
        version_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Any:
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
    def get_semantic_document(document_id: str, version_id: str) -> Any:
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
        },
    )
    def get_markdown(document_id: str, version_id: str) -> Response:
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
        },
    )
    def get_raw_file(document_id: str, version_id: str) -> Response:
        """Stream the originally-uploaded binary back to the caller.

        Powers the Knowledge Explorer's per-type viewers (PDF/DOCX/PPTX/
        text/wiki). The Content-Type mirrors what the uploader declared
        at ingest time, and ``Content-Disposition: inline`` lets browsers
        render PDFs and images natively instead of forcing a download.
        """
        try:
            version = services.documents.get_version(document_id=document_id, version_id=version_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
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
        "/documents/{document_id}/lineage",
        operation_id="get_document_lineage",
        response_model=LineageResponse,
    )
    def get_document_lineage(
        document_id: str,
        current_user: User = Depends(get_current_user),
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

        Returns ``404`` when the document does not exist; never raises
        on an empty family (a freshly-created family with one version
        is a valid response).
        """
        document = services.documents.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return _build_lineage_response(document)

    @router.get(
        "/documents/{document_id}/similar",
        operation_id="get_similar_documents",
        response_model=SimilarDocumentsResponse,
    )
    def get_similar_documents(
        document_id: str,
        k: int = Query(default=5, ge=1, le=50),
        current_user: User = Depends(get_current_user),
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
        """
        document = services.documents.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        ranked = services.document_similarity.top_k(document_id, k=k)
        results: list[SimilarDocument] = []
        for neighbor_id, score in ranked:
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
        document_id: str,
        version_id: str,
        request: ReviewRequest = Body(default_factory=ReviewRequest),
        current_user: User = Depends(get_current_user),
    ) -> Any:
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
        document_id: str,
        version_id: str,
        request: ReviewRequest = Body(default_factory=ReviewRequest),
        current_user: User = Depends(get_current_user),
    ) -> Any:
        return _dispatch_review(
            handler=services.review.handle_rejection,
            document_id=document_id,
            version_id=version_id,
            reviewer_note=request.reviewer_note,
            actor=current_user.id,
        )

    return router


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
