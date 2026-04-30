from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


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
