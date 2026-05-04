"""Document lifecycle routes — list / get / extract / semantic / review.

Covers everything between an uploaded version and a validated /
rejected one:

- catalog reads (``GET /documents`` + filters, ``GET /documents/{id}``)
- extraction trigger / retry / read
- semantic-document trigger / read
- generated Markdown read
- raw bytes read (powers Knowledge Explorer's per-type viewers)
- validate / reject endpoints + the shared review side-effect chain.

The validate / reject side-effect chain (FSM transition, semantic
persistence, knowledge-graph projection, optional LLM entity
extraction) lives here in ``_record_review`` and will move into a
dedicated ``ReviewService`` in audit P0 #223 — see PR #223 for that
follow-up.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Body, Header, HTTPException, Query, Response

from app.dependencies import PipelineServices
from app.errors import ApiError, ErrorCode
from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentListResponse, DocumentVersion
from app.schemas.extraction import RawExtraction
from app.schemas.semantic_document import SemanticDocument
from app.services.catalog_store import InvalidCursor
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

log = logging.getLogger(__name__)


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

    @router.post(
        "/documents/{document_id}/versions/{version_id}/validate",
        operation_id="validate_version",
        response_model=SemanticDocument,
    )
    def validate_version(
        document_id: str,
        version_id: str,
        request: ReviewRequest = Body(default_factory=ReviewRequest),
    ) -> Any:
        return _record_review(
            document_id=document_id,
            version_id=version_id,
            request=request,
            mark=services.documents.mark_validated,
            cached_status="validated",
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
    ) -> Any:
        return _record_review(
            document_id=document_id,
            version_id=version_id,
            request=request,
            mark=services.documents.mark_rejected,
            cached_status="rejected",
        )

    def _record_review(
        *,
        document_id: str,
        version_id: str,
        request: ReviewRequest,
        mark: Callable[..., Any],
        cached_status: Literal["validated", "rejected"],
    ) -> Any:
        try:
            version = services.documents.get_version(
                document_id=document_id,
                version_id=version_id,
            )
            if version.status != DocumentVersionStatus.NEEDS_REVIEW:
                raise ValueError(
                    f"Version is in {version.status.value}, not NEEDS_REVIEW; "
                    f"cannot transition to {cached_status.upper()}."
                )
            services.semantic_outputs.get(document_id=document_id, version_id=version_id)
            mark(
                document_id=document_id,
                version_id=version_id,
                reviewer_note=request.reviewer_note,
            )
            result = services.semantic_outputs.record_validation(
                document_id=document_id,
                version_id=version_id,
                status=cached_status,
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

        # Knowledge layer side-effect (ADR-012). Fire-and-log: a graph
        # outage must not roll back validation. The catalog is already
        # the authoritative record; the graph catches up via
        # re-projection or out-of-band reconciliation.
        if cached_status == "validated" and services.knowledge_projector is not None:
            document_for_projection = None
            try:
                document_for_projection = services.documents.get_document(document_id)
                if document_for_projection is not None:
                    services.knowledge_projector.project(
                        document=document_for_projection,
                        version=version,
                        semantic=result,
                    )
            except Exception:
                log.exception(
                    "knowledge.projection.failed",
                    extra={"document_id": document_id, "version_id": version_id},
                )

            # Phase 2 (ADR-013): LLM-driven entity extraction. Same
            # fire-and-log discipline — extraction failures must not
            # roll back validation. Runs after projection so the
            # entity edges land in the same graph the projector just
            # primed; the projector's ``delete_subgraph_for_version``
            # already cleaned old entity edges, so the upserts are
            # against a fresh slate.
            if services.entity_extractor is not None and document_for_projection is not None:
                try:
                    extraction_result = services.entity_extractor.extract(
                        document=document_for_projection,
                        version=version,
                        semantic=result,
                    )
                    services.knowledge_projector.project_entities(extraction_result)
                    log.info(
                        "knowledge.entity_extraction.completed",
                        extra={
                            "document_id": document_id,
                            "version_id": version_id,
                            "triple_count": len(extraction_result.triples),
                            "warning_count": len(extraction_result.warnings),
                            "token_usage": extraction_result.token_usage,
                        },
                    )
                except Exception:
                    log.exception(
                        "knowledge.entity_extraction.failed",
                        extra={
                            "document_id": document_id,
                            "version_id": version_id,
                        },
                    )

        return result

    return router
