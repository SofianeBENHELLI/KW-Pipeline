from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceReference(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    document_version_id: str
    section_id: str
    page_number: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    snippet: str


class RawExtraction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    document_version_id: str
    parser_name: str
    parser_version: str
    text: str
    sections: list[dict] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
