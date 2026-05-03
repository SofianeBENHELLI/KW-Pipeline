from datetime import UTC, datetime
from typing import Literal, Self
from uuid import uuid4

from pydantic import Field

from app.models.document import DocumentVersionStatus
from app.schemas import APISchemaModel as BaseModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class DocumentVersion(BaseModel):
    """One immutable binary upload in the catalog."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    document_id: str
    version_number: int
    filename: str
    content_type: str
    file_size: int
    sha256: str
    storage_uri: str
    status: DocumentVersionStatus
    duplicate_of_version_id: str | None = None
    failure_reason: str | None = None
    reviewer_note: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class Document(BaseModel):
    """Logical document family containing one or more versions."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    original_filename: str
    latest_version_id: str
    created_at: datetime = Field(default_factory=utc_now)
    versions: list[DocumentVersion] = Field(default_factory=list)

    @classmethod
    def with_first_version(cls, version: DocumentVersion) -> Self:
        return cls(
            id=version.document_id,
            original_filename=version.filename,
            latest_version_id=version.id,
            versions=[version],
        )


class DocumentListResponse(BaseModel):
    """Cursor-paginated page of documents returned by ``GET /documents``."""

    items: list[Document]
    next_cursor: str | None = None


class HealthResponse(BaseModel):
    status: str


# ─── Batch upload (#82) ─────────────────────────────────────────────


BatchUploadOutcomeStatus = Literal[
    "uploaded",
    "duplicate",
    "rejected_content_type",
    "too_large",
    "empty",
    "failed",
]


class BatchUploadOutcome(BaseModel):
    """Per-file result inside a :class:`BatchUploadResult`.

    Every file the client sent in a batch produces exactly one of
    these — partial-success batches keep the records for the failed
    files alongside the successful ones, and the route always returns
    HTTP 200 (the report itself describes failures). Clients route
    on the ``status`` discriminant.
    """

    filename: str
    content_type: str
    bytes: int
    status: BatchUploadOutcomeStatus
    # Set when ``status`` is ``"uploaded"`` or ``"duplicate"``;
    # ``None`` otherwise. Lets clients link back to the ingested row
    # without a second request.
    document_id: str | None = None
    version_id: str | None = None
    sha256: str | None = None
    # Set when ``status`` is anything other than ``"uploaded"`` /
    # ``"duplicate"``. ``error_code`` matches the public error
    # contract codes from ``app.errors.ErrorCode``
    # (e.g. ``KW_UPLOAD_TOO_LARGE``).
    error_code: str | None = None
    error_message: str | None = None


class BatchUploadSummary(BaseModel):
    """Aggregate counters for a :class:`BatchUploadResult`.

    The fields decompose ``total`` into mutually-exclusive buckets;
    summing every non-``total`` field equals ``total``. Saves clients
    from re-walking the per-file results to draw a banner.
    """

    total: int
    uploaded: int
    duplicate: int
    rejected_content_type: int
    too_large: int
    empty: int
    failed: int


class BatchUploadResult(BaseModel):
    """Response body for ``POST /documents/upload/batch`` (#82).

    Always returned with HTTP 200 — the report itself records per-file
    failures, so a single bad file never hides successful files.
    Idempotency-Key replay returns the original report unchanged.
    """

    results: list[BatchUploadOutcome]
    summary: BatchUploadSummary
