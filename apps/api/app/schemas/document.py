from datetime import UTC, datetime
from typing import Literal, Self
from uuid import uuid4

from pydantic import Field

from app.models.document import DocumentVersionStatus
from app.schemas import APISchemaModel as BaseModel
from app.schemas.scope import Scope


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


class UploadDocumentResponse(DocumentVersion):
    """Response body for ``POST /documents/upload`` (EPIC-D D.1, #218).

    Extends :class:`DocumentVersion` with the workspace-scope links
    that were created at upload time. ``scopes`` is the read-side
    counterpart to the optional ``scope_kind`` / ``scope_ref`` query
    params on the upload route — it surfaces every scope this upload
    landed in so the client can refresh its workspace picker without
    a follow-up ``list_scopes_for_document`` call.

    Defaults to a single ``personal:<user_id>`` link via the
    ``current_user`` resolved by :func:`get_current_user` when neither
    query param is provided. Pre-D.1 callers ignore ``scopes`` because
    every other field is unchanged from :class:`DocumentVersion`.
    """

    scopes: list[Scope] = Field(default_factory=list)


class DocumentListResponse(BaseModel):
    """Cursor-paginated page of documents returned by ``GET /documents``."""

    items: list[Document]
    next_cursor: str | None = None


# ─── Lineage (EPIC-C C.3, ADR-025) ────────────────────────────────────


class LineageVersion(BaseModel):
    """One row of a document family's version history (EPIC-C C.3).

    Returned by ``GET /documents/{id}/lineage``. Mirrors a subset of
    :class:`DocumentVersion` plus two derived fields the modal needs:

    - ``is_latest`` — ``True`` for the version with the highest
      ``version_number`` in the family. Lets the frontend render the
      "current" badge without a second request.
    - ``superseded_by_version_id`` — when this row's ``status`` is
      :data:`DocumentVersionStatus.SUPERSEDED`, points to the id of the
      next-higher version-numbered sibling that replaced it. ``None``
      otherwise. Derived from ``(version_number, status)`` ordering at
      response-build time per ADR-025 (the audit row that records the
      transition is not joined into :class:`DocumentVersion` itself).
    """

    id: str
    version_number: int
    filename: str
    status: DocumentVersionStatus
    sha256: str
    file_size: int
    is_latest: bool
    duplicate_of_version_id: str | None = None
    superseded_by_version_id: str | None = None
    ingested_at: datetime | None = None


class LineageResponse(BaseModel):
    """Response body for ``GET /documents/{id}/lineage`` (EPIC-C C.3).

    ``family_filename`` is the filename of the latest version in the
    family — that's the label the lineage modal hangs at the top of
    the version tree. ``versions`` is sorted ASC by ``version_number``
    so v1 → vN renders top-to-bottom in the UI.
    """

    document_id: str
    family_filename: str
    versions: list[LineageVersion]


# ─── Similar documents (EPIC-C C.3, ADR-025 §3) ───────────────────────


class SimilarDocument(BaseModel):
    """One ranking row from ``GET /documents/{id}/similar``.

    ``similarity`` is the Jaccard score in ``[0.0, 1.0]`` produced by
    :class:`app.services.document_similarity_service.DocumentSimilarityService`.
    ``family_filename`` and ``latest_version_status`` are surfaced so
    the lineage modal can render the row without a follow-up
    ``GET /documents/{id}`` per neighbor.
    """

    document_id: str
    family_filename: str
    similarity: float
    latest_version_status: DocumentVersionStatus


class SimilarDocumentsResponse(BaseModel):
    """Response body for ``GET /documents/{id}/similar?k=N`` (EPIC-C C.3).

    ``results`` is sorted by ``similarity`` descending; ties broken by
    ``document_id`` ascending. An empty list with HTTP 200 is the
    correct response when the query document has no topics yet (cold-
    start) — see :class:`DocumentSimilarityService` for the contract.
    """

    document_id: str
    results: list[SimilarDocument]


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
