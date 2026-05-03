import hashlib
import json
import logging
import tempfile
from collections.abc import Callable, Iterator
from typing import Any, Literal

from fastapi import APIRouter, Body, File, Header, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel

from app.dependencies import PipelineServices
from app.errors import ApiError, ErrorCode
from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentListResponse, DocumentVersion, HealthResponse
from app.schemas.extraction import RawExtraction
from app.schemas.knowledge import KnowledgeGraphPage, KnowledgeGraphProjection
from app.schemas.semantic_document import SemanticDocument
from app.services.catalog_store import InvalidCursor
from app.services.extraction_job_service import ExtractionFailed
from app.services.idempotency_store import IdempotencyStore, hash_json_body
from app.services.knowledge.graph_store import (
    DEFAULT_GRAPH_PAGE_LIMIT,
    MAX_GRAPH_PAGE_LIMIT,
)
from app.settings import Settings

log = logging.getLogger(__name__)
MIN_GRAPH_PAGE_LIMIT = 1

# Cursor pagination guardrails for `GET /documents`. The default page size
# matches the in-memory store's typical working set; the max ceiling keeps
# a single response under a few hundred KB even with verbose versions.
DEFAULT_PAGE_LIMIT = 50
MIN_PAGE_LIMIT = 1
MAX_PAGE_LIMIT = 200

# Streaming read granularity for the upload route. Matches the storage
# service's write granularity so peak resident memory during upload is one
# chunk plus framing overhead, regardless of total payload size.
_UPLOAD_READ_CHUNK_SIZE = 8 * 1024 * 1024
# Threshold below which `SpooledTemporaryFile` keeps bytes in RAM. Chosen
# at 1 MiB so anything larger spills to a real file on disk; this keeps the
# resident set bounded for multi-GB uploads while still avoiding a syscall
# round-trip for small ones.
_SPOOL_ROLLOVER_BYTES = 1 * 1024 * 1024


def _request_settings() -> Settings:
    """Construct a fresh :class:`Settings` for one request.

    Settings are read per-request rather than cached at app startup so a
    test that calls ``monkeypatch.setenv("MAX_UPLOAD_BYTES", ...)`` and
    issues a request immediately afterwards observes the new value.
    Pydantic Settings construction is cheap (no I/O, just an env-var
    walk), so the overhead is negligible compared with the work the
    upload route already does per call.
    """
    return Settings()


class ReviewRequest(BaseModel):
    """Optional reviewer note attached to a validate or reject decision."""

    reviewer_note: str | None = None


def _check_idempotency(
    *,
    store: IdempotencyStore,
    idempotency_key: str | None,
    route: str,
    request_hash: str,
) -> Response | None:
    """Check the idempotency store for a cached response.

    Returns a ``Response`` object if the request is a replay (caller should
    return it directly), or ``None`` if the request should proceed normally.

    Raises ``HTTPException(422)`` when the key is reused with a different
    request body.
    """
    if idempotency_key is None:
        return None

    stored = store.get(idempotency_key, route)
    if stored is None:
        return None

    if stored.request_hash != request_hash:
        raise ApiError(
            status_code=422,
            code=ErrorCode.IDEMPOTENCY_REPLAY,
            message="Idempotency-Key reused with different request body",
            retryable=False,
            remediation=(
                "Pick a fresh Idempotency-Key for the new request, or "
                "re-send exactly the same body to replay the cached "
                "response."
            ),
        )

    log.info(
        "idempotency.replayed",
        extra={
            "route": route,
            "idempotency_key": idempotency_key,
            "response_status": stored.response_status,
        },
    )
    # Return the cached response byte-identical to the original.
    return Response(
        content=stored.response_json,
        status_code=stored.response_status,
        media_type="application/json",
    )


def _store_idempotency(
    *,
    store: IdempotencyStore,
    idempotency_key: str | None,
    route: str,
    request_hash: str,
    result: object,
) -> None:
    """Persist a successful response in the idempotency store if a key is present."""
    if idempotency_key is None:
        return
    store.put(
        key=idempotency_key,
        route=route,
        request_hash=request_hash,
        response_status=200,
        response_json=json.dumps(result, default=str),
    )


def build_router(services: PipelineServices) -> APIRouter:
    """Register Harvester HTTP routes against a concrete service container."""
    router = APIRouter()

    @router.get("/health", operation_id="health", response_model=HealthResponse)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post(
        "/documents/upload",
        operation_id="upload_document",
        response_model=DocumentVersion,
    )
    async def upload_document(
        file: UploadFile = File(...),
        document_id: str | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Any:
        settings = _request_settings()
        max_bytes = settings.max_upload_bytes
        allowed = settings.allowed_content_types

        # Strip any media-type parameters (e.g. `; charset=utf-8`) before
        # comparing against the allowlist — RFC 7231 lets clients tack them
        # on freely, but the bare type is what we gate on.
        raw_content_type = file.content_type or "application/octet-stream"
        bare_content_type = raw_content_type.split(";")[0].strip()
        if bare_content_type not in allowed:
            allowed_list = ", ".join(sorted(allowed))
            raise ApiError(
                status_code=415,
                code=ErrorCode.UPLOAD_UNSUPPORTED_TYPE,
                message=(
                    f"Content type '{bare_content_type}' is not allowed. Allowed: {allowed_list}"
                ),
                retryable=False,
                remediation=(
                    "Re-upload the file with one of the allowed content "
                    "types, or ask an operator to widen the "
                    "KW_ALLOWED_CONTENT_TYPES allowlist."
                ),
            )

        # Spool the upload to a temp file in 8 MiB chunks so peak resident
        # memory tracks the chunk size, not the payload size. The size limit
        # is enforced incrementally — we stop reading the moment the running
        # total crosses ``max_bytes``, so a 51 MB body never materialises.
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_ROLLOVER_BYTES, mode="w+b") as spool:
            total = 0
            # Hash chunks as they stream in so the request fingerprint costs
            # nothing beyond the existing read loop — reading the spool back
            # into a `bytes` would defeat the streaming-memory budget.
            hasher = hashlib.sha256() if idempotency_key else None
            while True:
                chunk = await file.read(_UPLOAD_READ_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ApiError(
                        status_code=413,
                        code=ErrorCode.UPLOAD_TOO_LARGE,
                        message=f"Upload exceeds limit of {max_bytes} bytes",
                        retryable=False,
                        remediation=(
                            "Compress the file or split it into smaller "
                            "pieces before re-uploading. The current "
                            f"limit is {max_bytes} bytes (configurable via "
                            "MAX_UPLOAD_BYTES)."
                        ),
                    )
                if hasher is not None:
                    hasher.update(chunk)
                spool.write(chunk)
            if total == 0:
                raise ApiError(
                    status_code=400,
                    code=ErrorCode.UPLOAD_EMPTY,
                    message="Uploaded file is empty.",
                    retryable=False,
                    remediation=(
                        "Pick a file that has content and re-upload. The "
                        "byte stream we received was zero-length."
                    ),
                )
            spool.seek(0)

            _route = "/documents/upload"
            _req_hash = hasher.hexdigest() if hasher is not None else ""
            cached = _check_idempotency(
                store=services.idempotency,
                idempotency_key=idempotency_key,
                route=_route,
                request_hash=_req_hash,
            )
            if cached is not None:
                return cached

            def _iter_chunks() -> Iterator[bytes]:
                while True:
                    block = spool.read(_UPLOAD_READ_CHUNK_SIZE)
                    if not block:
                        return
                    yield block

            try:
                result = services.documents.upload_stream(
                    filename=file.filename or "untitled",
                    content_type=raw_content_type,
                    chunks=_iter_chunks(),
                    document_id=document_id,
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
        "/documents",
        operation_id="list_documents",
        response_model=DocumentListResponse,
    )
    def list_documents(limit: int = DEFAULT_PAGE_LIMIT, cursor: str | None = None) -> Any:
        if limit < MIN_PAGE_LIMIT or limit > MAX_PAGE_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"limit must be between {MIN_PAGE_LIMIT} and {MAX_PAGE_LIMIT}; got {limit}."
                ),
            )
        try:
            items, next_cursor = services.documents.list_documents_page(
                limit=limit,
                cursor=cursor,
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

    @router.get(
        "/documents/{document_id}/graph",
        operation_id="get_document_graph",
        response_model=KnowledgeGraphProjection,
    )
    def get_document_graph(document_id: str) -> Any:
        """Knowledge graph projection for one document family (ADR-012)."""
        return services.graph_store.find_subgraph_for_document(document_id)

    @router.get(
        "/knowledge/graph",
        operation_id="get_knowledge_graph",
        response_model=KnowledgeGraphPage,
    )
    def get_knowledge_graph(
        limit: int = Query(default=DEFAULT_GRAPH_PAGE_LIMIT, ge=MIN_GRAPH_PAGE_LIMIT),
        cursor: str | None = None,
    ) -> Any:
        """Cursor-paginated walk of the catalog-wide projection (ADR-012)."""
        if limit > MAX_GRAPH_PAGE_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"limit must be between {MIN_GRAPH_PAGE_LIMIT} "
                    f"and {MAX_GRAPH_PAGE_LIMIT}; got {limit}."
                ),
            )
        try:
            return services.graph_store.find_subgraph(limit=limit, cursor=cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
