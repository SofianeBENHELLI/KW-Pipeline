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
