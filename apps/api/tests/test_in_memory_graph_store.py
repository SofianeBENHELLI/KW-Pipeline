"""Behavioural tests for ``InMemoryGraphStore``.

The same tests must hold for any future ``GraphStore`` implementation
(e.g. ``Neo4jGraphStore`` exercised behind ``-m integration``); the
in-memory store is the authoritative behavioural reference for the
Protocol.
"""

from __future__ import annotations

import pytest

from app.schemas.knowledge import GraphEdge, GraphNode
from app.services.knowledge.graph_store import (
    DEFAULT_GRAPH_PAGE_LIMIT,
    MAX_GRAPH_PAGE_LIMIT,
    InMemoryGraphStore,
)


def _node(node_id: str, *, kind="section", document_id="doc-A", version_id="ver-A"):
    return GraphNode(
        id=node_id,
        kind=kind,
        label=f"label-{node_id}",
        properties={"document_id": document_id, "version_id": version_id},
    )


def _edge(edge_id: str, *, source: str, target: str, version_id="ver-A"):
    return GraphEdge(
        id=edge_id,
        kind="part_of",
        source_id=source,
        target_id=target,
        properties={"version_id": version_id},
    )


def test_upsert_is_idempotent():
    store = InMemoryGraphStore()
    node = _node("sec-1")
    store.upsert_nodes([node])
    store.upsert_nodes([node])  # second call must not duplicate

    page = store.find_subgraph(limit=10)
    assert len([n for n in page.nodes if n.id == "sec-1"]) == 1


def test_find_subgraph_for_document_collects_all_kinds():
    store = InMemoryGraphStore()
    store.upsert_nodes(
        [
            _node("doc-A", kind="document", document_id="doc-A", version_id=""),
            _node("ver-A", kind="version", document_id="doc-A", version_id="ver-A"),
            _node("sec-1", kind="section", document_id="doc-A", version_id="ver-A"),
            _node("sec-2", kind="section", document_id="doc-A", version_id="ver-A"),
            # An unrelated document; must not show up in doc-A's projection.
            _node("doc-B", kind="document", document_id="doc-B", version_id=""),
        ]
    )
    store.upsert_edges(
        [
            _edge("e1", source="ver-A", target="doc-A"),
            _edge("e2", source="sec-1", target="ver-A"),
            _edge("e3", source="sec-2", target="ver-A"),
        ]
    )

    proj = store.find_subgraph_for_document("doc-A")
    node_ids = {n.id for n in proj.nodes}
    assert node_ids == {"doc-A", "ver-A", "sec-1", "sec-2"}
    assert {e.id for e in proj.edges} == {"e1", "e2", "e3"}
    assert proj.version_id == "ver-A"
    assert proj.document_id == "doc-A"


def test_find_subgraph_for_unknown_document_is_empty():
    store = InMemoryGraphStore()
    proj = store.find_subgraph_for_document("nope")
    assert proj.nodes == []
    assert proj.edges == []


def test_delete_subgraph_for_version_removes_only_that_versions_nodes():
    store = InMemoryGraphStore()
    store.upsert_nodes(
        [
            _node("doc-A", kind="document", document_id="doc-A", version_id=""),
            _node("ver-A", kind="version", document_id="doc-A", version_id="ver-A"),
            _node("sec-1", kind="section", document_id="doc-A", version_id="ver-A"),
            _node("ver-B", kind="version", document_id="doc-A", version_id="ver-B"),
            _node("sec-2", kind="section", document_id="doc-A", version_id="ver-B"),
        ]
    )
    store.upsert_edges(
        [
            _edge("e1", source="ver-A", target="doc-A", version_id="ver-A"),
            _edge("e2", source="sec-1", target="ver-A", version_id="ver-A"),
            _edge("e3", source="ver-B", target="doc-A", version_id="ver-B"),
            _edge("e4", source="sec-2", target="ver-B", version_id="ver-B"),
        ]
    )

    store.delete_subgraph_for_version(document_id="doc-A", version_id="ver-A")

    page = store.find_subgraph(limit=20)
    surviving_node_ids = {n.id for n in page.nodes}
    assert surviving_node_ids == {"doc-A", "ver-B", "sec-2"}
    surviving_edge_ids = {e.id for e in page.edges}
    assert "e1" not in surviving_edge_ids
    assert "e2" not in surviving_edge_ids
    assert {"e3", "e4"} <= surviving_edge_ids


def test_find_subgraph_pagination_walks_deterministically():
    store = InMemoryGraphStore()
    # 5 nodes, page size 2 — expect three pages: 2 + 2 + 1.
    store.upsert_nodes([_node(f"sec-{i}") for i in range(5)])

    page_1 = store.find_subgraph(limit=2)
    assert len(page_1.nodes) == 2
    assert page_1.next_cursor is not None

    page_2 = store.find_subgraph(limit=2, cursor=page_1.next_cursor)
    assert len(page_2.nodes) == 2
    assert page_2.next_cursor is not None

    page_3 = store.find_subgraph(limit=2, cursor=page_2.next_cursor)
    assert len(page_3.nodes) == 1
    assert page_3.next_cursor is None

    # Pages are disjoint and union to the full set.
    seen = {n.id for n in page_1.nodes + page_2.nodes + page_3.nodes}
    assert seen == {f"sec-{i}" for i in range(5)}


def test_find_subgraph_rejects_out_of_range_limit():
    store = InMemoryGraphStore()
    with pytest.raises(ValueError):
        store.find_subgraph(limit=0)
    with pytest.raises(ValueError):
        store.find_subgraph(limit=MAX_GRAPH_PAGE_LIMIT + 1)


def test_find_subgraph_default_limit_returns_everything_when_small():
    store = InMemoryGraphStore()
    store.upsert_nodes([_node(f"sec-{i}") for i in range(5)])
    page = store.find_subgraph(limit=DEFAULT_GRAPH_PAGE_LIMIT)
    assert len(page.nodes) == 5
    assert page.next_cursor is None


# ─── Phase 3 vector primitives (ADR-015) ─────────────────────────────────


def _chunk_node(chunk_id: str, *, version_id="ver-A", section_id=None, snippet=None):
    return GraphNode(
        id=chunk_id,
        kind="chunk",
        label=f"label-{chunk_id}",
        properties={
            "document_id": "doc-A",
            "version_id": version_id,
            "section_id": section_id or chunk_id,
            "text_preview": snippet,
        },
    )


def test_ensure_vector_index_is_no_op_for_in_memory():
    store = InMemoryGraphStore()
    # Both the first call and a re-call must succeed (idempotent contract).
    store.ensure_vector_index(name="chunk_embedding", dim=16)
    store.ensure_vector_index(name="chunk_embedding", dim=16)


def test_ensure_vector_index_rejects_non_positive_dim():
    store = InMemoryGraphStore()
    with pytest.raises(ValueError):
        store.ensure_vector_index(name="chunk_embedding", dim=0)
    with pytest.raises(ValueError):
        store.ensure_vector_index(name="chunk_embedding", dim=-3)


def test_find_chunks_by_similarity_ranks_by_cosine():
    store = InMemoryGraphStore()
    store.upsert_nodes(
        [
            _chunk_node("c1", snippet="alpha"),
            _chunk_node("c2", snippet="beta"),
            _chunk_node("c3", snippet="gamma"),
        ]
    )
    # Hand-crafted vectors so the order is unambiguous.
    store.set_chunk_embedding(chunk_id="c1", embedding=[1.0, 0.0])
    store.set_chunk_embedding(chunk_id="c2", embedding=[0.0, 1.0])
    store.set_chunk_embedding(chunk_id="c3", embedding=[0.5, 0.5])

    hits = store.find_chunks_by_similarity([1.0, 0.0], limit=3)

    assert [h.chunk_id for h in hits] == ["c1", "c3", "c2"]
    # Score for c1 is exactly 1.0 (identical direction).
    assert hits[0].score == pytest.approx(1.0)
    # Snippet round-trips from text_preview.
    assert hits[0].snippet == "alpha"
    assert hits[0].document_id == "doc-A"
    assert hits[0].version_id == "ver-A"


def test_find_chunks_by_similarity_respects_limit():
    store = InMemoryGraphStore()
    for i in range(5):
        store.upsert_nodes([_chunk_node(f"c{i}")])
        store.set_chunk_embedding(chunk_id=f"c{i}", embedding=[float(i), 1.0])
    hits = store.find_chunks_by_similarity([1.0, 1.0], limit=2)
    assert len(hits) == 2


def test_find_chunks_by_similarity_skips_dim_mismatch():
    """A stale vector from a prior model is silently ignored, not raised."""
    store = InMemoryGraphStore()
    store.upsert_nodes([_chunk_node("c1"), _chunk_node("c2")])
    store.set_chunk_embedding(chunk_id="c1", embedding=[1.0, 0.0])
    store.set_chunk_embedding(chunk_id="c2", embedding=[1.0, 0.0, 0.0])  # wrong dim

    hits = store.find_chunks_by_similarity([1.0, 0.0], limit=10)

    # Only c1 should be ranked; c2 is silently skipped.
    assert {h.chunk_id for h in hits} == {"c1"}


def test_find_chunks_by_similarity_returns_empty_when_no_embeddings():
    store = InMemoryGraphStore()
    store.upsert_nodes([_chunk_node("c1")])
    hits = store.find_chunks_by_similarity([1.0, 0.0], limit=5)
    assert hits == []


def test_find_chunks_by_similarity_rejects_invalid_limit():
    store = InMemoryGraphStore()
    with pytest.raises(ValueError):
        store.find_chunks_by_similarity([1.0], limit=0)
    with pytest.raises(ValueError):
        store.find_chunks_by_similarity([1.0], limit=10_000)


def test_set_chunk_embedding_is_overwritable():
    store = InMemoryGraphStore()
    store.upsert_nodes([_chunk_node("c1")])
    store.set_chunk_embedding(chunk_id="c1", embedding=[1.0, 0.0])
    store.set_chunk_embedding(chunk_id="c1", embedding=[0.0, 1.0])
    hits = store.find_chunks_by_similarity([0.0, 1.0], limit=1)
    assert hits[0].chunk_id == "c1"
    assert hits[0].score == pytest.approx(1.0)


def test_delete_subgraph_for_version_drops_chunk_embedding():
    """Re-projecting a version must invalidate stale embeddings."""
    store = InMemoryGraphStore()
    store.upsert_nodes([_chunk_node("c1", version_id="v1")])
    store.set_chunk_embedding(chunk_id="c1", embedding=[1.0, 0.0])

    store.delete_subgraph_for_version(document_id="doc-A", version_id="v1")

    hits = store.find_chunks_by_similarity([1.0, 0.0], limit=5)
    assert hits == []
