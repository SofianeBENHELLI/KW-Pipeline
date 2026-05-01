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
