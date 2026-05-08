"""Focused neighborhood service (#310, ADR-028).

Builds a bounded subgraph around a single root node — document,
topic, or chunk — by BFS-walking incident edges up to the requested
depth, scoring each candidate via #314, applying the strength
threshold, and clipping to the edge budget.

The service is read-only and stateless; one instance serves every
concurrent request. Walks are bounded by:

- :data:`NEIGHBORHOOD_MAX_DEPTH` on the BFS depth (default 1, ceiling 3).
- ``edge_limit`` on the visible edge count (default 20, ceiling 200).
- ``min_strength`` on the combined relation strength (default 0).

Together those bound the worst-case work to ``O(MAX_DEPTH × MAX_LIMIT)``
graph-store probes per request — well below the catalog-wide walk a
naive client would do otherwise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.schemas.knowledge import GraphEdge, GraphEdgeKind, GraphNode
from app.schemas.knowledge_neighborhood import (
    NEIGHBORHOOD_MAX_DEPTH,
    NEIGHBORHOOD_MAX_LIMIT,
    NEIGHBORHOOD_MIN_DEPTH,
    NEIGHBORHOOD_MIN_LIMIT,
    FocusedNeighborhood,
    NeighborhoodEdge,
    NeighborhoodRootKind,
)
from app.services.knowledge.scoring import score_edge

if TYPE_CHECKING:
    from app.services.knowledge.graph_store import GraphStore

# Edge kinds where the #314 scoring policy applies. Structural edges
# (``part_of`` / ``has_version`` / ``has_chunk`` / ``belongs_to``) and
# LLM edges (``has_entity``) skip scoring — their score field stays
# ``None`` on the response.
_SCOREABLE_KINDS: frozenset[GraphEdgeKind] = frozenset(
    {"related_to", "shares_keyword", "same_topic_as"}
)


class NeighborhoodNotFound(LookupError):
    """Raised when the requested root node doesn't exist in the graph
    store, or its kind doesn't match ``root_kind``. The route layer
    maps this to a 404 ``KW_NOT_FOUND`` envelope."""


def _coerce_float_property(value: object) -> float:
    """Pull a numeric out of the union-typed property map.

    Property values are ``str | int | float | bool | list[str] | None``
    so a bare ``float(...)`` doesn't typecheck. We coerce defensively
    rather than annotate-cast: malformed values land at ``0.0`` rather
    than throwing in production.
    """
    if value is None or isinstance(value, list):
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0  # pragma: no cover - GraphPropertyValue covers every reachable type


def _coerce_str_list_property(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _project_edge(edge: GraphEdge) -> NeighborhoodEdge:
    """Build the wire-shape edge from a stored ``GraphEdge`` and its
    #314 score (when applicable)."""
    if edge.kind in _SCOREABLE_KINDS:
        raw_score = _coerce_float_property(edge.properties.get("score"))
        shared_keywords = _coerce_str_list_property(edge.properties.get("shared_keywords"))
        scored = score_edge(
            edge_id=edge.id,
            raw_score=raw_score,
            shared_keyword_count=len(shared_keywords),
            source_chunk_count=1,
            validation_bonus=0.0,
            source_topic_keywords=(),
            target_topic_keywords=(),
        )
        return NeighborhoodEdge(
            id=edge.id,
            kind=edge.kind,
            source_id=edge.source_id,
            target_id=edge.target_id,
            properties=edge.properties,
            score=scored.score,
            strength_class=scored.strength_class.value,
            is_bridge=scored.is_bridge,
            is_outlier=scored.is_outlier,
        )
    return NeighborhoodEdge(
        id=edge.id,
        kind=edge.kind,
        source_id=edge.source_id,
        target_id=edge.target_id,
        properties=edge.properties,
    )


class KnowledgeNeighborhoodService:
    """Read service for the focused neighborhood API (#310).

    Stateless. The ``neighborhood`` method is the only public entry
    point; routes call it with already-validated query params (depth /
    limit / min_strength bounds enforced at the FastAPI layer).
    """

    def __init__(self, *, graph_store: GraphStore) -> None:
        self._graph_store = graph_store

    def neighborhood(
        self,
        *,
        root_kind: NeighborhoodRootKind,
        root_id: str,
        depth: int,
        edge_limit: int,
        min_strength: float = 0.0,
    ) -> FocusedNeighborhood:
        """Build a focused neighborhood around ``root_id``.

        Raises:
            NeighborhoodNotFound: ``root_id`` doesn't exist in the
                graph or its kind doesn't match ``root_kind``.
            ValueError: ``depth`` / ``edge_limit`` / ``min_strength``
                fall outside their documented bounds. The route layer
                normally enforces these via FastAPI's ``Query``
                validators; this is a defensive belt-and-braces check.
        """
        if not NEIGHBORHOOD_MIN_DEPTH <= depth <= NEIGHBORHOOD_MAX_DEPTH:
            raise ValueError(
                f"depth must be in [{NEIGHBORHOOD_MIN_DEPTH}, "
                f"{NEIGHBORHOOD_MAX_DEPTH}]; got {depth}."
            )
        if not NEIGHBORHOOD_MIN_LIMIT <= edge_limit <= NEIGHBORHOOD_MAX_LIMIT:
            raise ValueError(
                f"edge_limit must be in [{NEIGHBORHOOD_MIN_LIMIT}, "
                f"{NEIGHBORHOOD_MAX_LIMIT}]; got {edge_limit}."
            )
        if not 0.0 <= min_strength <= 1.0:
            raise ValueError(f"min_strength must be in [0, 1]; got {min_strength}.")

        root = self._graph_store.find_node_by_id(root_id)
        if root is None or root.kind != root_kind:
            raise NeighborhoodNotFound(f"No {root_kind!r} node with id {root_id!r}.")

        # BFS up to ``depth`` levels, expanding via incident edges.
        # Track candidate edges by id (dedupe) and visited node ids
        # for the next-frontier computation.
        candidate_edges: dict[str, GraphEdge] = {}
        visited_node_ids: set[str] = {root_id}
        frontier: set[str] = {root_id}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for node_id in frontier:
                for edge in self._graph_store.find_edges_incident_to_node(node_id):
                    if edge.id in candidate_edges:
                        continue
                    candidate_edges[edge.id] = edge
                    other_id = edge.target_id if edge.source_id == node_id else edge.source_id
                    if other_id not in visited_node_ids:
                        next_frontier.add(other_id)
            visited_node_ids.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                # No more nodes to expand into; stop early.
                break

        # Project + score every candidate edge.
        scored_edges = [_project_edge(raw) for raw in candidate_edges.values()]

        # Apply the strength filter. ``min_strength=0.0`` keeps every
        # edge; non-zero filters out weak deterministic ones (and
        # leaves un-scored edges in place — they have no strength
        # signal so we can't reject them).
        kept: list[NeighborhoodEdge] = []
        hidden_by_threshold = 0
        for projected in scored_edges:
            if projected.score is not None and projected.score < min_strength:
                hidden_by_threshold += 1
                continue
            kept.append(projected)

        # Deterministic sort: score desc (None last), then edge id asc.
        kept.sort(key=lambda e: (-(e.score or 0.0), e.id))

        # Apply edge budget.
        truncated = len(kept) > edge_limit
        visible_edges = kept[:edge_limit]
        hidden_by_budget = max(0, len(kept) - edge_limit)

        # Compute the visible node set: root plus every endpoint of a
        # visible edge. Nodes reachable by the BFS but only via hidden
        # edges contribute to ``hidden_node_count``.
        visible_node_ids: set[str] = {root_id}
        for visible in visible_edges:
            visible_node_ids.add(visible.source_id)
            visible_node_ids.add(visible.target_id)

        nodes: list[GraphNode] = []
        for node_id in visible_node_ids:
            node = self._graph_store.find_node_by_id(node_id)
            if node is not None:
                nodes.append(node)
        nodes.sort(key=lambda n: (n.kind, n.id))

        hidden_node_count = max(0, len(visited_node_ids) - len(visible_node_ids))
        hidden_edge_count = hidden_by_threshold + hidden_by_budget

        return FocusedNeighborhood(
            root_kind=root_kind,
            root_id=root_id,
            depth=depth,
            nodes=nodes,
            edges=visible_edges,
            hidden_node_count=hidden_node_count,
            hidden_edge_count=hidden_edge_count,
            truncated=truncated or hidden_edge_count > 0,
        )


__all__ = [
    "KnowledgeNeighborhoodService",
    "NeighborhoodNotFound",
]
