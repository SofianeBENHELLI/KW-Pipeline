"""Wire-shape models for the focused neighborhood API (#310, ADR-028).

The Explorer's graph lens (#317) opens a bounded subgraph around a
selected node — document, topic, or chunk — instead of dumping the
catalog-wide graph onto the canvas. This module defines the response
shape that route returns: a schema-versioned envelope with the
visible nodes, the visible edges (each carrying its #314 score so the
canvas can rank without re-running the policy), and the truncation
metadata (``hidden_node_count`` / ``hidden_edge_count`` / ``truncated``)
so the operator knows when the lens has clipped.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel
from app.schemas.knowledge import GraphEdgeKind, GraphNode, GraphPropertyValue
from app.schemas.knowledge_relations import StrengthClassValue

#: Root kinds the v0.1 endpoint accepts. Entity-rooted neighborhoods
#: are deferred (entities are doc-agnostic, scope filtering needs a
#: separate design); relation-rooted is also deferred (relations are
#: edges, not nodes — they get their own surface in #311).
NeighborhoodRootKind = Literal["document", "topic", "chunk"]

#: Neighborhood depth bounds. Depth 1 means "direct neighbors only";
#: depth 3 caps the budget so an over-eager caller can't materialise a
#: catalog-sized BFS.
NEIGHBORHOOD_MIN_DEPTH = 1
NEIGHBORHOOD_MAX_DEPTH = 3
NEIGHBORHOOD_DEFAULT_DEPTH = 1

#: Edge-budget bounds. Default 20 keeps the canvas readable; ceiling
#: 200 is the same envelope ``GET /knowledge/graph`` uses.
NEIGHBORHOOD_DEFAULT_LIMIT = 20
NEIGHBORHOOD_MIN_LIMIT = 1
NEIGHBORHOOD_MAX_LIMIT = 200


class NeighborhoodEdge(BaseModel):
    """One edge in a focused neighborhood, carrying its #314 score.

    Mirrors the public :class:`GraphEdge` shape (id / kind / endpoints
    / properties) and adds the per-edge scoring fields the canvas
    consumes. Score-related fields stay ``None`` for non-deterministic
    edges (structural / topic-membership / has_entity) since the
    scoring policy doesn't apply.
    """

    id: str
    kind: GraphEdgeKind
    source_id: str
    target_id: str
    properties: dict[str, GraphPropertyValue] = Field(default_factory=dict)

    score: float | None = None
    strength_class: StrengthClassValue | None = None
    is_bridge: bool | None = None
    is_outlier: bool | None = None


class FocusedNeighborhood(BaseModel):
    """Wire shape for ``GET /knowledge/neighborhood``.

    ``schema_version`` is pinned to ``"v0.1"`` for now; bumping it
    is a breaking change that requires the typed client to re-read
    the OpenAPI snapshot. See ADR-028's "Information Architecture"
    section for the contract this satisfies.

    The ``edges`` list is deterministically ordered: combined-score
    descending, then ``edge_id`` ascending — paginated walks across
    the same neighborhood land edges in the same canvas position.

    Truncation metadata exists so the frontend can render a "+ N
    more" indicator on the canvas without re-querying:

    - ``hidden_node_count`` — nodes the BFS reached but the visible
      edge set doesn't connect (or that the limit clipped off).
    - ``hidden_edge_count`` — edges dropped by the strength filter or
      the budget. Sum of both contributors.
    - ``truncated`` — convenience boolean: ``hidden_edge_count > 0``.
    """

    schema_version: Literal["v0.1"] = "v0.1"
    root_kind: NeighborhoodRootKind
    root_id: str
    depth: int

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[NeighborhoodEdge] = Field(default_factory=list)

    hidden_node_count: int = 0
    hidden_edge_count: int = 0
    truncated: bool = False


__all__ = [
    "NEIGHBORHOOD_DEFAULT_DEPTH",
    "NEIGHBORHOOD_DEFAULT_LIMIT",
    "NEIGHBORHOOD_MAX_DEPTH",
    "NEIGHBORHOOD_MAX_LIMIT",
    "NEIGHBORHOOD_MIN_DEPTH",
    "NEIGHBORHOOD_MIN_LIMIT",
    "FocusedNeighborhood",
    "NeighborhoodEdge",
    "NeighborhoodRootKind",
]
