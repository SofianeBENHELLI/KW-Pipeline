"""Wire-shape models for the multi-kind Explorer search (#313, ADR-028).

The Phase-3 ``GET /knowledge/search`` route returns chunks only; the
Explorer's grouped semantic-search experience (#319) needs results
bucketed by kind so the UI can render section-by-section instead of
a flat list.

Today this module ships three groups (chunks / documents / topics).
Entity- and relation-group results are deferred — the service walks
``has_entity`` edges from matched chunks plus reason/keyword
matching on chunk-relation edges, and that surface needs a separate
design pass. Empty lists ride through so the wire shape stays
forward-compat.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel


class ExploreSearchChunk(BaseModel):
    """One chunk hit. Mirrors :class:`ChunkSearchResult` plus the trust
    flags the Explorer surfaces (validated / source-backed) so the UI
    can default to high-trust results without a second probe."""

    chunk_id: str
    document_id: str
    version_id: str
    section_id: str
    snippet: str | None
    score: float
    validation_status: str | None = None
    is_source_backed: bool = False


class ExploreSearchDocument(BaseModel):
    """One document hit aggregated from its contributing chunks.

    ``score`` is the maximum of the contributing-chunks' similarity —
    the doc's "best matching chunk" score. ``contributing_chunks`` is
    a deterministic list (top-N at the document level) for the UI to
    surface as evidence.
    """

    document_id: str
    title: str
    score: float
    validation_status: str | None = None
    is_source_backed: bool = False
    contributing_chunks: list[ExploreSearchChunk] = Field(default_factory=list)


class ExploreSearchTopic(BaseModel):
    """One topic hit. Topics are deterministic clusters of chunks; this
    group surfaces topics that contain at least one matching chunk
    plus the matching evidence chunks."""

    topic_id: str
    label: str
    keywords: list[str] = Field(default_factory=list)
    score: float
    evidence_chunks: list[ExploreSearchChunk] = Field(default_factory=list)


class ExploreSearchEntity(BaseModel):
    """One entity hit. Reserved for v0.2 — the v0.1 service returns an
    empty list so the wire shape can extend without breaking
    consumers."""

    entity_id: str
    label: str
    score: float
    mention_chunks: list[ExploreSearchChunk] = Field(default_factory=list)


class ExploreSearchRelation(BaseModel):
    """One relation hit. Reserved for v0.2."""

    relation_id: str
    kind: str
    score: float
    reason: str | None = None
    shared_keywords: list[str] = Field(default_factory=list)


class ExploreSearchResponse(BaseModel):
    """Wire shape for ``GET /knowledge/explore/search``.

    All five groups ride together — empty lists for the deferred
    groups (entities, relations) so consumers can render the shell
    immediately and light up new groups when they arrive in v0.2.
    """

    schema_version: Literal["v0.1"] = "v0.1"
    query: str
    embedding_model: str
    chunks: list[ExploreSearchChunk] = Field(default_factory=list)
    documents: list[ExploreSearchDocument] = Field(default_factory=list)
    topics: list[ExploreSearchTopic] = Field(default_factory=list)
    entities: list[ExploreSearchEntity] = Field(default_factory=list)
    relations: list[ExploreSearchRelation] = Field(default_factory=list)


__all__ = [
    "ExploreSearchChunk",
    "ExploreSearchDocument",
    "ExploreSearchEntity",
    "ExploreSearchRelation",
    "ExploreSearchResponse",
    "ExploreSearchTopic",
]
