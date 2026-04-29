from datetime import datetime, timezone
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, Field

from app.models.document import DocumentVersionStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DocumentVersion(BaseModel):
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
    created_at: datetime = Field(default_factory=utc_now)


class Document(BaseModel):
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
