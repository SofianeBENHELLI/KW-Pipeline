"""Pydantic schemas for the knowledge layer (ADR-012).

These models describe the projection of a validated ``SemanticDocument``
into a graph of ``Document``/``Version``/``Section`` nodes connected by
``PART_OF`` edges. Phase 2 (entity extraction) will add ``Entity`` nodes
and ``HAS_ENTITY`` edges that carry source-reference citations.

All models inherit from :class:`APISchemaModel` so list defaults appear
as required in the serialization-mode JSON Schema (PR #107 / #80) — the
generated TypeScript on the Orbital side then sees ``T[]`` instead of
``T[] | undefined``.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel

# Bump this when the wire shape of nodes/edges changes. Keep additive
# changes additive (per ADR-008): the Orbital frontend reads any v0.x
# payload, the projector writes the latest minor.
KNOWLEDGE_GRAPH_SCHEMA_VERSION = "v0.1"

GraphNodeKind = Literal["document", "version", "section", "entity"]
GraphEdgeKind = Literal["part_of", "has_entity"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GraphNode(BaseModel):
    """One node in the knowledge graph projection.

    ``id`` is stable across projections — for ``document`` and
    ``version`` nodes it matches the catalog row ID; for ``section``
    nodes it matches ``SemanticSection.id``; for ``entity`` nodes
    (Phase 2) it is a deterministic hash of (text, type) so re-runs
    converge on the same node.
    """

    id: str
    kind: GraphNodeKind
    label: str
    properties: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """One directed edge in the knowledge graph projection.

    ``source_id`` and ``target_id`` reference :class:`GraphNode.id`
    values. Phase 2 ``has_entity`` edges additionally carry a
    ``source_reference_id`` in ``properties`` pointing at a row in the
    catalog's ``source_references`` table; ``part_of`` edges have no
    such citation requirement.
    """

    id: str
    kind: GraphEdgeKind
    source_id: str
    target_id: str
    properties: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class KnowledgeGraphProjection(BaseModel):
    """Subgraph for one document family — nodes and edges that the
    projector wrote on the most recent ``VALIDATED`` transition.

    The projection is deterministic with respect to its inputs: the
    same ``Document`` + ``DocumentVersion`` + ``SemanticDocument`` will
    always produce the same nodes and edges (modulo ``generated_at``).
    Re-projecting is safe — upserts are idempotent.
    """

    schema_version: Literal["v0.1"] = "v0.1"
    document_id: str
    version_id: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utc_now)


class EntityTriple(BaseModel):
    """One ``(subject, predicate, object)`` triple emitted by the LLM.

    Phase 2 (ADR-012 §4 + ADR-013) populates the knowledge graph by
    asking the model to read a validated ``SemanticSection`` and emit
    triples with citations. The triple lands as two ``(:Entity)`` nodes
    plus a ``HAS_ENTITY``-style relation only if ``source_reference_ids``
    is non-empty — the equivalent of ADR-009's "force needs_review"
    audit gate, applied to graph edges. No edge enters the graph
    without provenance.
    """

    subject: str
    subject_type: str
    predicate: str
    object: str
    object_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_section_id: str
    # `min_length=1` enforces the "no edge without a citation" gate at
    # the schema level. Triples missing citations are appended to
    # ``EntityExtractionResult.warnings`` by the extractor instead of
    # being constructed at all.
    source_reference_ids: list[str] = Field(min_length=1)


class EntityExtractionResult(BaseModel):
    """Aggregated output of one entity-extraction pass over a version.

    Carries the validated triples plus warnings (for triples the
    extractor dropped — missing citations, citations not in the
    section's source-reference set, prompt-injection lines stripped
    from input) and per-pass token usage so the caller can log a cost
    line per validation.
    """

    schema_version: Literal["v0.1"] = "v0.1"
    document_id: str
    version_id: str
    triples: list[EntityTriple] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=_utc_now)


class KnowledgeGraphPage(BaseModel):
    """Cursor-paginated page across all projected documents.

    Used by ``GET /knowledge/graph`` to walk the catalog's projection
    in deterministic order. ``next_cursor`` follows the same opaque
    convention as :class:`DocumentListResponse` — clients pass it
    back verbatim to advance.
    """

    schema_version: Literal["v0.1"] = "v0.1"
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    next_cursor: str | None = None
