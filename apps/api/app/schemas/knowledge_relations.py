"""Wire-shape models for the relation explanation API (#311, ADR-028).

The Explorer's relation inspector (#318) and the upcoming aggregated
doc-doc evidence drawer call into the new ``/knowledge/relations/...``
routes; this module defines the response shapes those routes return.

Single-edge evidence (:class:`RelationEvidence`) covers every stored
edge kind — structural / deterministic / LLM. Aggregated evidence
(:class:`AggregatedRelationEvidence`) projects every chunk-level edge
that crosses a (source_doc, target_doc) boundary, scores each via the
#314 policy, and returns the top contributing pairs.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal  # noqa: F401  (used for StrengthClassValue alias)

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel
from app.schemas.knowledge import GraphEdgeKind

# Mirror of :class:`app.services.knowledge.scoring.StrengthClass` as a
# bare Literal so this schemas module doesn't pull in the
# ``app.services.knowledge`` package — the package's ``__init__``
# imports the relations service which imports this module, which would
# create a circular import. The values are pinned in lockstep; if you
# change one you change both.
StrengthClassValue = Literal["strong", "medium", "weak"]


class ProvenanceClass(StrEnum):
    """Three-bucket classification of an edge's provenance type.

    Mirrors the ADR-012 §4 / `knowledge_graph_payload.md` distinction:

    - ``structural`` — ``part_of`` / ``has_version`` / ``has_chunk`` /
      ``belongs_to``. No citation needed; the parent-child relation
      itself is the provenance.
    - ``deterministic`` — ``related_to`` / ``shares_keyword`` /
      ``same_topic_as``. Carries ``source_chunk_ids`` + ``reason`` +
      ``shared_keywords`` (chunk pair as provenance).
    - ``llm`` — ``has_entity``. Must carry a ``source_reference_id``
      from the catalog's ``source_references`` table.
    """

    STRUCTURAL = "structural"
    DETERMINISTIC = "deterministic"
    LLM = "llm"


class RelationEvidence(BaseModel):
    """Wire shape for ``GET /knowledge/relations/{relation_id}``.

    All fields are optional except the routing-bones (``relation_id``,
    ``kind``, ``provenance_class``, ``source_id``, ``target_id``) so
    one model covers every edge kind. Unused fields stay at their
    Pydantic defaults — the typed client on the frontend pattern-matches
    on ``provenance_class`` to know which evidence fields to read.

    Scoring fields (``score`` / ``strength_class`` / ``is_bridge`` /
    ``is_outlier``) are populated for ``DETERMINISTIC`` edges via
    :func:`app.services.knowledge.scoring.score_edge`. They stay
    ``None`` for ``STRUCTURAL`` (a structural parent-child edge has no
    notion of "strength") and for ``LLM`` (the confidence field is the
    closer analogue there — see ``confidence`` below).
    """

    relation_id: str
    kind: GraphEdgeKind
    provenance_class: ProvenanceClass
    source_id: str
    target_id: str

    # Scoring (#314) — populated for DETERMINISTIC edges only.
    score: float | None = None
    strength_class: StrengthClassValue | None = None
    is_bridge: bool | None = None
    is_outlier: bool | None = None
    contributing_factors: dict[str, float] = Field(default_factory=dict)

    # Deterministic-edge evidence.
    reason: str | None = None
    shared_keywords: list[str] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(default_factory=list)

    # LLM-edge evidence.
    confidence: float | None = None
    predicate: str | None = None
    source_section_id: str | None = None
    source_reference_ids: list[str] = Field(default_factory=list)

    # Document context — populated for any edge whose properties carry
    # ``document_id`` / ``version_id``. The route layer uses
    # ``document_id`` to apply the D.5 hidden-existence check before
    # returning the response.
    document_id: str | None = None
    version_id: str | None = None


class ContributingChunkPair(BaseModel):
    """One chunk-level edge contributing to an aggregated doc-doc relation.

    The pair carries the edge's evidence (``reason``, ``shared_keywords``)
    plus the combined #314 score so the frontend can rank pairs without
    re-running the scoring policy.
    """

    relation_id: str
    kind: GraphEdgeKind
    source_chunk_id: str
    target_chunk_id: str
    score: float
    strength_class: StrengthClassValue
    reason: str
    shared_keywords: list[str] = Field(default_factory=list)


class AggregatedRelationEvidence(BaseModel):
    """Wire shape for ``GET /knowledge/relations/aggregate``.

    Synthesises a doc-doc relation from the chunk-level edges that
    cross the (source, target) document boundary. ``aggregate_score``
    is the **maximum** combined-pair score (the strongest single
    pair) — interpretation: "at least one of these chunk pairs is this
    strong." Mean would dilute the surfacing of high-strength bridges
    in documents with many weak overlaps.

    ``is_bridge`` / ``is_outlier`` are ``True`` when at least one
    contributing pair carries those flags, so the frontend can label
    the aggregated edge accordingly.
    """

    source_document_id: str
    target_document_id: str
    aggregate_score: float
    pair_count: int
    top_contributing_pairs: list[ContributingChunkPair]
    is_bridge: bool
    is_outlier: bool


__all__ = [
    "AggregatedRelationEvidence",
    "ContributingChunkPair",
    "ProvenanceClass",
    "RelationEvidence",
]
