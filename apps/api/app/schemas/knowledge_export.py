"""Pydantic schemas for the deterministic knowledge export package
(see ``docs/architecture/knowledge_export_contract.md``, closes #23).

The export package is a portable, content-addressed JSON artefact that
downstream RAG indexers / data warehouses / customer handoff bundles
read. The schemas here are the wire shape; the deterministic-ID and
checksum derivation lives in :mod:`app.services.knowledge_exporter`
because it walks a :class:`SemanticDocument` rather than just shaping
an inbound payload.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel
from app.schemas.semantic_document import ReviewStatus, ValidationStatus


def utc_now() -> datetime:
    return datetime.now(UTC)


# Bumped when the package wire shape changes. Consumers compare this
# field on the manifest before parsing so a v0.1 reader doesn't try
# to interpret a v0.2 payload.
EXPORT_SCHEMA_VERSION: Literal["v0.1"] = "v0.1"


class ExportedChunk(BaseModel):
    """One chunk in the export package.

    ``chunk_id`` is the deterministic, content-addressed handle the
    exporter computes from
    ``(document_id, version_id, normalized_text)``. Stable across
    re-exports of the same version; changes when the chunk text
    changes (the correct invariant for a content-addressed cache key).

    ``section_id`` preserves the in-system :class:`SemanticSection.id`
    so consumers can correlate an exported chunk back to the knowledge
    graph node, the review workspace, and the source-reference table.

    ``validation_status`` mirrors the version-level FSM state — chunks
    are atomic with their version under the current contract.
    ``content_sha256`` is the full sha256 of the normalized text (the
    16-hex prefix is reused as the chunk_id suffix; the full digest is
    here for consumers that prefer a 256-bit handle).
    """

    chunk_id: str
    section_id: str
    document_id: str
    document_version_id: str
    heading: str
    text: str
    char_count: int
    content_sha256: str
    source_reference_ids: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus


class ExportedAsset(BaseModel):
    """One typed semantic claim in the export package.

    ``review_status`` is the 4-state field on
    :class:`SemanticAsset` — finer-grained than the version-level
    ``ValidationStatus``. The exporter does not drop ``needs_review``
    / ``rejected`` assets; consumers filter by status when indexing.
    """

    asset_id: str
    asset_type: str
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    review_status: ReviewStatus
    source_reference_ids: list[str] = Field(default_factory=list)
    content_sha256: str


class ExportManifest(BaseModel):
    """Top-level metadata for the export package.

    ``package_sha256`` is the canonical-JSON sha256 of the
    ``(chunks, assets)`` pair (see the contract doc §"Package-level
    checksum"). Consumers compare this against their last-seen value
    to short-circuit re-indexing. Sorting on chunk_id / asset_id makes
    the hash reorder-stable.
    """

    schema_version: Literal["v0.1"] = EXPORT_SCHEMA_VERSION
    document_id: str
    document_version_id: str
    document_version_number: int
    original_filename: str
    version_filename: str
    document_sha256: str
    content_type: str
    semantic_schema_version: str
    validation_status: ValidationStatus
    document_type: str
    chunk_count: int
    asset_count: int
    package_sha256: str
    exported_at: datetime = Field(default_factory=utc_now)


class KnowledgeExportPackage(BaseModel):
    """Full export package — manifest + chunks + assets + optional
    rendered markdown blob. One JSON object per document version.
    """

    manifest: ExportManifest
    chunks: list[ExportedChunk] = Field(default_factory=list)
    assets: list[ExportedAsset] = Field(default_factory=list)
    markdown: str | None = None


__all__ = [
    "EXPORT_SCHEMA_VERSION",
    "ExportManifest",
    "ExportedAsset",
    "ExportedChunk",
    "KnowledgeExportPackage",
]
