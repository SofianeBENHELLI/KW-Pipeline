from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field

from app.models.document import DocumentVersionStatus
from app.schemas import APISchemaModel as BaseModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class SourceReference(BaseModel):
    """Pointer from extracted or semantic content back to source text."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    document_version_id: str
    section_id: str
    page_number: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    snippet: str


class RawSection(BaseModel):
    """Typed parser-produced section.

    Carries the minimum fields needed for semantic extraction plus optional
    placeholders for fields that future parsers (e.g. Docling for PDFs) will
    populate. Keeping the schema explicit avoids the historical
    ``list[dict]`` shape and the ``.get()``-driven access pattern that came
    with it.
    """

    id: str
    heading: str = "Extracted Text"
    text: str
    source_reference_ids: list[str] = Field(default_factory=list)
    page_number: int | None = None  # populated by future PDF parser
    bbox: tuple[float, float, float, float] | None = None  # Docling region, future
    parser_metadata: dict[str, str] = Field(default_factory=dict)


class RawExtraction(BaseModel):
    """Inspectable parser output stored before semantic extraction."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    document_version_id: str
    parser_name: str
    parser_version: str
    text: str
    sections: list[RawSection] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ExtractionJobSnapshot(BaseModel):
    """Receipt for an enqueued async extraction job (ADR-006, #40 PR-2).

    Returned with HTTP 202 from ``POST /documents/{document_id}/versions/
    {version_id}/extract`` (and the equivalent retry-extraction route)
    when ``KW_EXTRACTION_INLINE=false``. Inline mode keeps returning
    :class:`RawExtraction` with HTTP 200 — the union response model on
    the route documents both shapes.

    The ``job_id`` is opaque to clients and scoped to
    ``(document_id, version_id)``. The MVP value is ``f"ext-{version_id}"``;
    a future durable queue (ADR-022 trajectory) can swap in a UUID
    without rotating the field name.

    ``status`` always carries the canonical
    :class:`DocumentVersionStatus.QUEUED_FOR_EXTRACTION` value at
    submission time — clients poll ``GET /documents/{id}`` to observe
    the version's progression through ``QUEUED_FOR_EXTRACTION →
    EXTRACTING → EXTRACTED|FAILED``.

    ``queue_position`` is best-effort — an in-memory ``asyncio.Queue``
    has no atomic "position" primitive, so the value reported is the
    queue depth right after the put. ``None`` when inline mode is on
    (no queue exists) for forward-compatibility with the same shape
    being reused as a synchronous receipt by future tooling.
    """

    job_id: str
    document_id: str
    version_id: str
    status: DocumentVersionStatus
    submitted_at: datetime = Field(default_factory=utc_now)
    queue_position: int | None = None
