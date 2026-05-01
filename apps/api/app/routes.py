import hashlib
import json
import os
import tempfile
from collections.abc import Iterator

from fastapi import APIRouter, Body, File, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel

from app.dependencies import PipelineServices
from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentListResponse, DocumentVersion, HealthResponse
from app.schemas.extraction import RawExtraction
from app.schemas.semantic_document import SemanticDocument
from app.services.catalog_store import InvalidCursor
from app.services.extraction_job_service import ExtractionFailed
from app.services.idempotency_store import IdempotencyStore, hash_json_body

# Cursor pagination guardrails for `GET /documents`. The default page size
# matches the in-memory store's typical working set; the max ceiling keeps
# a single response under a few hundred KB even with verbose versions.
DEFAULT_PAGE_LIMIT = 50
MIN_PAGE_LIMIT = 1
MAX_PAGE_LIMIT = 200

# Default upload guardrails. These mirror the values used by the production
# deployment until Pydantic Settings (#43) lands and replaces the ad-hoc
# `os.environ.get` reads at request time.
DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB
DEFAULT_ALLOWED_CONTENT_TYPES = "text/plain"

# Streaming read granularity for the upload route. Matches the storage
# service's write granularity so peak resident memory during upload is one
# chunk plus framing overhead, regardless of total payload size.
_UPLOAD_READ_CHUNK_SIZE = 8 * 1024 * 1024
# Threshold below which `SpooledTemporaryFile` keeps bytes in RAM. Chosen
# at 1 MiB so anything larger spills to a real file on disk; this keeps the
# resident set bounded for multi-GB uploads while still avoiding a syscall
# round-trip for small ones.
_SPOOL_ROLLOVER_BYTES = 1 * 1024 * 1024


def _max_upload_bytes() -> int:
    """Read MAX_UPLOAD_BYTES from the environment at request time.

    Read on every request so tests can `monkeypatch.setenv` per case. Falls
    back to ``DEFAULT_MAX_UPLOAD_BYTES`` when the env var is unset.
    """
    raw = os.environ.get("MAX_UPLOAD_BYTES")
    if raw is None or raw == "":
        return DEFAULT_MAX_UPLOAD_BYTES
    return int(raw)


def _allowed_content_types() -> set[str]:
    """Read ALLOWED_CONTENT_TYPES from the environment at request time.

    The env var is a comma-separated list. Empty entries are dropped so a
    trailing comma does not silently allow ``""``.
    """
    raw = os.environ.get("ALLOWED_CONTENT_TYPES", DEFAULT_ALLOWED_CONTENT_TYPES)
    return {entry.strip() for entry in raw.split(",") if entry.strip()}


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
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key reused with different request body",
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
    ):
        max_bytes = _max_upload_bytes()
        allowed = _allowed_content_types()

        # Strip any media-type parameters (e.g. `; charset=utf-8`) before
        # comparing against the allowlist — RFC 7231 lets clients tack them
        # on freely, but the bare type is what we gate on.
        raw_content_type = file.content_type or "application/octet-stream"
        bare_content_type = raw_content_type.split(";")[0].strip()
        if bare_content_type not in allowed:
            allowed_list = ", ".join(sorted(allowed))
            raise HTTPException(
                status_code=415,
                detail=(
                    f"Content type '{bare_content_type}' is not allowed. Allowed: {allowed_list}"
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
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds limit of {max_bytes} bytes",
                    )
                if hasher is not None:
                    hasher.update(chunk)
                spool.write(chunk)
            if total == 0:
                raise HTTPException(status_code=400, detail="Uploaded file is empty.")
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
    def list_documents(limit: int = DEFAULT_PAGE_LIMIT, cursor: str | None = None):
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
    def get_document(document_id: str):
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
    ):
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

    @router.get(
        "/documents/{document_id}/versions/{version_id}/extraction",
        operation_id="get_extraction",
        response_model=RawExtraction,
    )
    def get_extraction(document_id: str, version_id: str):
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
    ):
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
    def get_semantic_document(document_id: str, version_id: str):
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
    def get_markdown(document_id: str, version_id: str):
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
    ):
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
    ):
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
        mark,
        cached_status,
    ):
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
            return services.semantic_outputs.record_validation(
                document_id=document_id,
                version_id=version_id,
                status=cached_status,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
