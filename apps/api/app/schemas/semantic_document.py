from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from app.schemas import APISchemaModel as BaseModel

ReviewStatus = Literal["needs_review", "source_backed", "validated", "rejected"]
ValidationStatus = Literal["needs_review", "validated", "rejected"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class DocumentProfile(BaseModel):
    """High-level metadata used at the top of semantic Markdown output."""

    title: str
    document_type: str = "unknown"
    purpose: str | None = None
    audience: str | None = None
    executive_summary: str | None = None


class SemanticSection(BaseModel):
    """Reviewable semantic section with optional source lineage."""

    id: str
    heading: str
    text: str
    source_reference_ids: list[str] = Field(default_factory=list)


class SemanticAsset(BaseModel):
    """Typed semantic claim or asset extracted from the document."""

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
    """Governed semantic output for one document version."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    document_version_id: str
    schema_version: Literal["v0.1"] = "v0.1"
    document_profile: DocumentProfile
    sections: list[SemanticSection] = Field(default_factory=list)
    assets: list[SemanticAsset] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_references: list[dict] = Field(default_factory=list)
    validation_status: ValidationStatus = "needs_review"
    markdown: str | None = None
    # Identifier of the SemanticGenerator that produced this payload
    # (see :mod:`app.services.semantic_generators`). Optional + default
    # ``None`` so persisted v0.1 payloads written before the
    # method-dispatch lands keep loading unchanged.
    extraction_method: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
