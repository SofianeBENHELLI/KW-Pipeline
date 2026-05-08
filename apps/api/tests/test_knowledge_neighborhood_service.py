"""Service-layer tests for the focused neighborhood API (#310, ADR-028).

Drives :class:`KnowledgeNeighborhoodService` directly against an
:class:`InMemoryGraphStore` so the BFS / scoring / truncation logic
is exercised in isolation. The HTTP layer (auth, scope, 404 envelope)
lives in ``test_routes_knowledge_neighborhood.py``.
"""

from __future__ import annotations

import pytest

from app.schemas.knowledge import GraphEdge, GraphNode
from app.services.knowledge.graph_store import InMemoryGraphStore
from app.services.knowledge.neighborhood import (
    KnowledgeNeighborhoodService,
    NeighborhoodNotFound,
)


def _service() -> tuple[KnowledgeNeighborhoodService, InMemoryGraphStore]:
    store = InMemoryGraphStore()
    return KnowledgeNeighborhoodService(graph_store=store), store


def _seed_chunk(store: InMemoryGraphStore, chunk_id: str, document_id: str = "doc-1") -> None:
    store.upsert_nodes(
        [
            GraphNode(
                id=chunk_id,
                kind="chunk",
                label=chunk_id,
                properties={
                    "document_id": document_id,
                    "version_id": f"ver-{document_id}",
                    "chunk_id": chunk_id,
                },
            )
        ]
    )


def _seed_related(
    store: InMemoryGraphStore,
    *,
    src: str,
    tgt: str,
    score: float = 0.5,
    document_id: str = "doc-1",
    shared_keywords: list[str] | None = None,
) -> str:
    """Seed a deterministic ``related_to`` edge between two chunks.
    Returns the edge id so the test can reference it."""
    edge_id = f"ver-{document_id}:{src}->related_to->{tgt}"
    store.upsert_edges(
        [
            GraphEdge(
                id=edge_id,
                kind="related_to",
                source_id=src,
                target_id=tgt,
                properties={
                    "document_id": document_id,
                    "version_id": f"ver-{document_id}",
                    "source_chunk_id": src,
                    "target_chunk_id": tgt,
                    "score": score,
                    "reason": f"shared overlap {src}-{tgt}",
                    "shared_keywords": shared_keywords or ["safety", "policy"],
                },
            )
        ]
    )
    return edge_id


# ── Empty / unknown root ──────────────────────────────────────────────


class TestUnknownRoot:
    def test_unknown_root_raises(self) -> None:
        service, _ = _service()
        with pytest.raises(NeighborhoodNotFound):
            service.neighborhood(root_kind="chunk", root_id="missing", depth=1, edge_limit=10)

    def test_root_kind_mismatch_raises(self) -> None:
        service, store = _service()
        _seed_chunk(store, "c1")
        # The node IS in the graph, but as kind=chunk, not topic.
        with pytest.raises(NeighborhoodNotFound):
            service.neighborhood(root_kind="topic", root_id="c1", depth=1, edge_limit=10)


# ── Single-node neighborhood (no incident edges) ──────────────────────


class TestEmptyNeighborhood:
    def test_chunk_with_no_edges_returns_lonely_node(self) -> None:
        service, store = _service()
        _seed_chunk(store, "c1")
        out = service.neighborhood(root_kind="chunk", root_id="c1", depth=1, edge_limit=10)
        assert out.root_id == "c1"
        assert len(out.nodes) == 1
        assert out.nodes[0].id == "c1"
        assert out.edges == []
        assert out.hidden_edge_count == 0
        assert out.truncated is False


# ── Depth-1 neighborhood ──────────────────────────────────────────────


class TestDepthOne:
    def test_returns_direct_neighbors_only(self) -> None:
        service, store = _service()
        _seed_chunk(store, "c1")
        _seed_chunk(store, "c2")
        _seed_chunk(store, "c3")
        _seed_related(store, src="c1", tgt="c2", score=0.8)
        # c2 → c3 is two hops from c1, so depth=1 must NOT include it.
        _seed_related(store, src="c2", tgt="c3", score=0.7)

        out = service.neighborhood(root_kind="chunk", root_id="c1", depth=1, edge_limit=10)
        edge_ids = {e.id for e in out.edges}
        assert any("c1->related_to->c2" in eid for eid in edge_ids)
        assert not any("c2->related_to->c3" in eid for eid in edge_ids)

    def test_score_attached_to_deterministic_edges(self) -> None:
        service, store = _service()
        _seed_chunk(store, "c1")
        _seed_chunk(store, "c2")
        _seed_related(store, src="c1", tgt="c2", score=0.8)

        out = service.neighborhood(root_kind="chunk", root_id="c1", depth=1, edge_limit=10)
        assert len(out.edges) == 1
        edge = out.edges[0]
        assert edge.score is not None
        assert edge.score >= 0.8  # raw + bonus
        assert edge.strength_class == "strong"

    def test_structural_edges_skip_scoring(self) -> None:
        service, store = _service()
        _seed_chunk(store, "c1")
        store.upsert_nodes(
            [
                GraphNode(
                    id="topic-1",
                    kind="topic",
                    label="t",
                    properties={"document_id": "doc-1", "version_id": "ver-1"},
                )
            ]
        )
        store.upsert_edges(
            [
                GraphEdge(
                    id="e-belongs",
                    kind="belongs_to",
                    source_id="c1",
                    target_id="topic-1",
                    properties={"document_id": "doc-1", "version_id": "ver-1"},
                )
            ]
        )
        out = service.neighborhood(root_kind="chunk", root_id="c1", depth=1, edge_limit=10)
        belongs = next(e for e in out.edges if e.id == "e-belongs")
        assert belongs.score is None
        assert belongs.strength_class is None


# ── Depth >1 expansion ────────────────────────────────────────────────


class TestDepthExpansion:
    def test_depth_two_walks_two_hops(self) -> None:
        service, store = _service()
        _seed_chunk(store, "c1")
        _seed_chunk(store, "c2")
        _seed_chunk(store, "c3")
        _seed_related(store, src="c1", tgt="c2", score=0.8)
        _seed_related(store, src="c2", tgt="c3", score=0.7)

        out = service.neighborhood(root_kind="chunk", root_id="c1", depth=2, edge_limit=10)
        node_ids = {n.id for n in out.nodes}
        # All three chunks should be visible.
        assert {"c1", "c2", "c3"} <= node_ids


# ── Edge budget + truncation ──────────────────────────────────────────


class TestEdgeBudget:
    def test_edge_budget_truncates_with_hidden_count(self) -> None:
        service, store = _service()
        _seed_chunk(store, "c0")  # the root
        # Five direct neighbors with descending scores.
        for i, score in enumerate([0.9, 0.8, 0.7, 0.6, 0.5], start=1):
            _seed_chunk(store, f"c{i}")
            _seed_related(store, src="c0", tgt=f"c{i}", score=score)

        out = service.neighborhood(root_kind="chunk", root_id="c0", depth=1, edge_limit=2)
        assert len(out.edges) == 2
        assert out.truncated is True
        assert out.hidden_edge_count == 3
        # The two visible edges must be the strongest two (deterministic ordering).
        scores = [e.score for e in out.edges if e.score is not None]
        assert scores == sorted(scores, reverse=True)


# ── Strength threshold ────────────────────────────────────────────────


class TestStrengthThreshold:
    def test_min_strength_drops_weak_edges(self) -> None:
        service, store = _service()
        _seed_chunk(store, "c0")
        _seed_chunk(store, "c1")
        _seed_chunk(store, "c2")
        _seed_related(store, src="c0", tgt="c1", score=0.8)
        _seed_related(store, src="c0", tgt="c2", score=0.1)

        out = service.neighborhood(
            root_kind="chunk", root_id="c0", depth=1, edge_limit=10, min_strength=0.5
        )
        assert len(out.edges) == 1
        assert out.edges[0].source_id == "c0"
        # The weak edge contributes to ``hidden_edge_count``.
        assert out.hidden_edge_count == 1


# ── Validation ────────────────────────────────────────────────────────


class TestValidation:
    @pytest.mark.parametrize("bad_depth", [0, 4, -1])
    def test_invalid_depth_raises(self, bad_depth: int) -> None:
        service, store = _service()
        _seed_chunk(store, "c1")
        with pytest.raises(ValueError):
            service.neighborhood(root_kind="chunk", root_id="c1", depth=bad_depth, edge_limit=10)

    @pytest.mark.parametrize("bad_limit", [0, 201, -1])
    def test_invalid_limit_raises(self, bad_limit: int) -> None:
        service, store = _service()
        _seed_chunk(store, "c1")
        with pytest.raises(ValueError):
            service.neighborhood(root_kind="chunk", root_id="c1", depth=1, edge_limit=bad_limit)

    @pytest.mark.parametrize("bad_threshold", [-0.1, 1.1])
    def test_invalid_strength_raises(self, bad_threshold: float) -> None:
        service, store = _service()
        _seed_chunk(store, "c1")
        with pytest.raises(ValueError):
            service.neighborhood(
                root_kind="chunk",
                root_id="c1",
                depth=1,
                edge_limit=10,
                min_strength=bad_threshold,
            )


# ── Schema version ───────────────────────────────────────────────────


class TestSchemaVersion:
    def test_response_carries_v0_1(self) -> None:
        service, store = _service()
        _seed_chunk(store, "c1")
        out = service.neighborhood(root_kind="chunk", root_id="c1", depth=1, edge_limit=10)
        assert out.schema_version == "v0.1"
