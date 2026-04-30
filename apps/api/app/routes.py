import os
import tempfile
from collections.abc import Iterator

from fastapi import APIRouter, Body, File, HTTPException, Response, UploadFile
from pydantic import BaseModel

from app.dependencies import PipelineServices
from app.models.document import DocumentVersionStatus
from app.services.catalog_store import InvalidCursor
from app.services.extraction_job_service import ExtractionFailed

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


def build_router(services: PipelineServices) -> APIRouter:
    """Register Harvester HTTP routes against a concrete service container."""
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/documents/upload")
    async def upload_document(
        file: UploadFile = File(...),
        document_id: str | None = None,
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
                spool.write(chunk)
            if total == 0:
                raise HTTPException(status_code=400, detail="Uploaded file is empty.")
            spool.seek(0)

            def _iter_chunks() -> Iterator[bytes]:
                while True:
                    block = spool.read(_UPLOAD_READ_CHUNK_SIZE)
                    if not block:
                        return
                    yield block

            try:
                return services.documents.upload_stream(
                    filename=file.filename or "untitled",
                    content_type=raw_content_type,
                    chunks=_iter_chunks(),
                    document_id=document_id,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/documents")
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

    @router.get("/documents/{document_id}")
    def get_document(document_id: str):
        document = services.documents.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return document

    @router.post("/documents/{document_id}/versions/{version_id}/extract")
    def extract_document(document_id: str, version_id: str):
        try:
            return services.extraction_jobs.extract(document_id=document_id, version_id=version_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ExtractionFailed as exc:
            raise HTTPException(status_code=422, detail=exc.reason) from exc

    @router.get("/documents/{document_id}/versions/{version_id}/extraction")
    def get_extraction(document_id: str, version_id: str):
        try:
            return services.extraction_jobs.get_raw_extraction(
                document_id=document_id,
                version_id=version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/documents/{document_id}/versions/{version_id}/semantic")
    def generate_semantic_document(document_id: str, version_id: str):
        try:
            return services.semantic_outputs.generate(
                document_id=document_id, version_id=version_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/documents/{document_id}/versions/{version_id}/semantic")
    def get_semantic_document(document_id: str, version_id: str):
        try:
            return services.semantic_outputs.get(document_id=document_id, version_id=version_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/documents/{document_id}/versions/{version_id}/markdown")
    def get_markdown(document_id: str, version_id: str):
        try:
            markdown = services.semantic_outputs.get_markdown(
                document_id=document_id,
                version_id=version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(content=markdown, media_type="text/markdown")

    @router.post("/documents/{document_id}/versions/{version_id}/validate")
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

    @router.post("/documents/{document_id}/versions/{version_id}/reject")
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
