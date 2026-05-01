from datetime import UTC, datetime
from typing import Self
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
