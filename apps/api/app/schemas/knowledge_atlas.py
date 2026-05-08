"""Wire-shape models for the corpus atlas API (#312, ADR-028).

The Explorer's default home (#316) opens on this atlas instead of
the catalog-wide graph. The response carries five summary blocks —
top topics, validation coverage, recent documents, bridge documents,
and outlier relations — bounded so the payload stays renderable
without pagination.

ADR-028's "Information Architecture" section defines the contract;
this module is the typed surface the route returns and the typed
client consumes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel


class AtlasTopicSummary(BaseModel):
    """One topic on the atlas's "top topics" tile.

    ``document_count`` and ``chunk_count`` are the coverage stats
    used to rank topics; ``keywords`` give the UI a quick label
    when the topic's own ``label`` is sparse.
    """

    topic_id: str
    label: str
    keywords: list[str] = Field(default_factory=list)
    document_count: int = 0
    chunk_count: int = 0


class AtlasValidationCoverage(BaseModel):
    """Catalog-wide coverage by document validation state.

    All counts are filtered to documents the caller can access (D.5
    hidden-existence applies). The Explorer surfaces these as a
    summary card on the atlas home.
    """

    total_documents: int = 0
    validated_count: int = 0
    needs_review_count: int = 0
    rejected_count: int = 0
    other_count: int = 0


class AtlasRecentDocument(BaseModel):
    """One document on the "recent imports" tile.

    Sorted by ``created_at`` descending in the response. The
    ``validation_status`` is the latest version's status so the UI
    can colour-code the row.
    """

    document_id: str
    title: str
    created_at: datetime
    validation_status: str | None = None


class AtlasBridgeDocument(BaseModel):
    """One bridge document — a doc whose chunks span topics that are
    mutually distant per #314's :func:`bridge_document_score`.

    ``score`` is the mean pairwise topic distance over the doc's
    topics; higher = more bridge-y.
    """

    document_id: str
    title: str
    score: float
    topic_count: int = 0


class AtlasOutlierRelation(BaseModel):
    """One candidate-outlier relation — an edge that's strong AND
    crosses a wide topic gap per #314's outlier classification.

    The label "candidate" is deliberate (#314 notes): the policy
    surfaces these as suggestions, never as facts.
    """

    relation_id: str
    kind: str
    source_id: str
    target_id: str
    score: float
    reason: str | None = None
    shared_keywords: list[str] = Field(default_factory=list)


class AtlasResponse(BaseModel):
    """Wire shape for ``GET /knowledge/atlas``.

    Schema-versioned for forward compatibility — bumping ``v0.1`` is a
    breaking change requiring the typed client to re-read the OpenAPI
    snapshot. All five blocks ride together; an empty corpus simply
    returns empty lists with zero counts.
    """

    schema_version: Literal["v0.1"] = "v0.1"
    top_topics: list[AtlasTopicSummary] = Field(default_factory=list)
    validation_coverage: AtlasValidationCoverage = Field(default_factory=AtlasValidationCoverage)
    recent_documents: list[AtlasRecentDocument] = Field(default_factory=list)
    bridge_documents: list[AtlasBridgeDocument] = Field(default_factory=list)
    outlier_relations: list[AtlasOutlierRelation] = Field(default_factory=list)


__all__ = [
    "AtlasBridgeDocument",
    "AtlasOutlierRelation",
    "AtlasRecentDocument",
    "AtlasResponse",
    "AtlasTopicSummary",
    "AtlasValidationCoverage",
]
