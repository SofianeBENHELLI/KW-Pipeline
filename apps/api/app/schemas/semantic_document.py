from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

ReviewStatus = Literal["needs_review", "source_backed", "validated", "rejected"]
ValidationStatus = Literal["needs_review", "validated", "rejected"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DocumentProfile(BaseModel):
    title: str
    document_type: str = "unknown"
    purpose: str | None = None
    audience: str | None = None
    executive_summary: str | None = None


class SemanticSection(BaseModel):
    id: str
    heading: str
    text: str
    source_reference_ids: list[str] = Field(default_factory=list)


class SemanticAsset(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    type: str
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    review_status: ReviewStatus = "needs_review"
    source_reference_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_lineage_for_source_backed(self):
        if self.review_status == "source_backed" and not self.source_reference_ids:
            raise ValueError("source_backed assets require source references.")
        return self


class SemanticDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    document_version_id: str
    schema_version: str = "v0.1"
    document_profile: DocumentProfile
    sections: list[SemanticSection] = Field(default_factory=list)
    assets: list[SemanticAsset] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_references: list[dict] = Field(default_factory=list)
    validation_status: ValidationStatus = "needs_review"
    markdown: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
