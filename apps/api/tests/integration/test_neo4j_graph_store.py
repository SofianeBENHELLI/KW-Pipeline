"""Integration tests for ``Neo4jGraphStore`` against a real Neo4j.

These mirror the behavioural contract asserted by
``tests/test_in_memory_graph_store.py`` but execute against a live
``bolt://`` endpoint. They are opt-in:

- The default ``pytest`` invocation excludes them (the ``addopts`` in
  ``pyproject.toml`` carries ``-m 'not integration'``).
- The tests skip themselves if the ``KW_NEO4J_URI`` / ``KW_NEO4J_USER``
  / ``KW_NEO4J_PASSWORD`` env vars are missing, so a developer who
  runs ``pytest -m integration`` without first starting Neo4j gets a
  clean skip rather than a connection error.

Run via:

    docker compose -f docker/docker-compose.yml up -d neo4j
    KW_NEO4J_URI=bolt://localhost:7687 \\
    KW_NEO4J_USER=neo4j \\
    KW_NEO4J_PASSWORD=test_password_change_me \\
        pytest -m integration tests/integration/

Each test runs in its own UUID-prefixed id namespace so a stray
``DETACH DELETE`` failure can't leak state into the next test.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

from app.schemas.knowledge import GraphEdge, GraphNode
from app.services.knowledge.graph_store import (
    MAX_GRAPH_PAGE_LIMIT,
    Neo4jGraphStore,
)

pytestmark = pytest.mark.integration


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def neo4j_config() -> dict[str, str]:
    """Read connection settings from env; skip the module if absent."""
    uri = os.environ.get("KW_NEO4J_URI")
    user = os.environ.get("KW_NEO4J_USER")
    password = os.environ.get("KW_NEO4J_PASSWORD")
    missing = [
        name
        for name, value in (
            ("KW_NEO4J_URI", uri),
            ("KW_NEO4J_USER", user),
            ("KW_NEO4J_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        pytest.skip(
            "Neo4j integration tests require "
            f"{', '.join(missing)}; set them or run "
            "`docker compose -f docker/docker-compose.yml up -d neo4j`."
        )
    assert uri and user and password  # for type checkers
    return {"uri": uri, "user": user, "password": password}


@pytest.fixture()
def store(neo4j_config: dict[str, str]) -> Iterator[Neo4jGraphStore]:
    s = Neo4jGraphStore(
        uri=neo4j_config["uri"],
        user=neo4j_config["user"],
        password=neo4j_config["password"],
    )
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def clean_graph(neo4j_config: dict[str, str]) -> Iterator[None]:
    """Wipe :KnowledgeNode before AND after each test.

    The ``before`` clean keeps a previous failure from poisoning the
    next run; the ``after`` clean keeps the database tidy for the next
    test. Both run via a fresh driver session so they aren't entangled
    with the per-test ``store`` fixture.
    """
    from neo4j import GraphDatabase  # noqa: PLC0415

    driver = GraphDatabase.driver(
        neo4j_config["uri"],
        auth=(neo4j_config["user"], neo4j_config["password"]),
    )

    def _wipe() -> None:
        with driver.session() as session:
            session.run("MATCH (n:KnowledgeNode) DETACH DELETE n")

    try:
        _wipe()
        yield
        _wipe()
    finally:
        driver.close()


@pytest.fixture()
def ns() -> str:
    """A unique id namespace per test so cleanup failures don't collide."""
    return f"it-{uuid.uuid4().hex[:8]}"


# ─── Helpers ───────────────────────────────────────────────────────────


def _node(
    ns: str,
    node_id: str,
    *,
    kind: str = "section",
    document_id: str | None = None,
    version_id: str | None = None,
) -> GraphNode:
    full_id = f"{ns}-{node_id}"
    doc = f"{ns}-{document_id}" if document_id else f"{ns}-doc-A"
    ver = f"{ns}-{version_id}" if version_id else f"{ns}-ver-A"
    return GraphNode(
        id=full_id,
        kind=kind,
        label=f"label-{full_id}",
        properties={"document_id": doc, "version_id": ver},
    )


def _edge(
    ns: str,
    edge_id: str,
    *,
    source: str,
    target: str,
    version_id: str | None = None,
) -> GraphEdge:
    return GraphEdge(
        id=f"{ns}-{edge_id}",
        kind="part_of",
        source_id=f"{ns}-{source}",
        target_id=f"{ns}-{target}",
        properties={"version_id": f"{ns}-{version_id}" if version_id else f"{ns}-ver-A"},
    )


def _ids(ns: str, items: list[str]) -> set[str]:
    return {f"{ns}-{item}" for item in items}


# ─── Tests ─────────────────────────────────────────────────────────────


def test_upsert_is_idempotent(store: Neo4jGraphStore, ns: str) -> None:
    node = _node(ns, "sec-1")
    store.upsert_nodes([node])
    store.upsert_nodes([node])  # second call must not duplicate

    page = store.find_subgraph(limit=MAX_GRAPH_PAGE_LIMIT)
    matches = [n for n in page.nodes if n.id == f"{ns}-sec-1"]
    assert len(matches) == 1


def test_find_subgraph_for_document_collects_all_kinds(store: Neo4jGraphStore, ns: str) -> None:
    store.upsert_nodes(
        [
            _node(ns, "doc-A", kind="document", document_id="doc-A", version_id=""),
            _node(ns, "ver-A", kind="version", document_id="doc-A", version_id="ver-A"),
            _node(ns, "sec-1", kind="section", document_id="doc-A", version_id="ver-A"),
            _node(ns, "sec-2", kind="section", document_id="doc-A", version_id="ver-A"),
            # Unrelated document; must not show up in doc-A's projection.
            _node(ns, "doc-B", kind="document", document_id="doc-B", version_id=""),
        ]
    )
    store.upsert_edges(
        [
            _edge(ns, "e1", source="ver-A", target="doc-A"),
            _edge(ns, "e2", source="sec-1", target="ver-A"),
            _edge(ns, "e3", source="sec-2", target="ver-A"),
        ]
    )

    proj = store.find_subgraph_for_document(f"{ns}-doc-A")
    node_ids = {n.id for n in proj.nodes}
    assert node_ids == _ids(ns, ["doc-A", "ver-A", "sec-1", "sec-2"])
    assert {e.id for e in proj.edges} == _ids(ns, ["e1", "e2", "e3"])
    assert proj.version_id == f"{ns}-ver-A"
    assert proj.document_id == f"{ns}-doc-A"


def test_delete_subgraph_for_version_removes_only_that_versions_nodes(
    store: Neo4jGraphStore, ns: str
) -> None:
    store.upsert_nodes(
        [
            _node(ns, "doc-A", kind="document", document_id="doc-A", version_id=""),
            _node(ns, "ver-A", kind="version", document_id="doc-A", version_id="ver-A"),
            _node(ns, "sec-1", kind="section", document_id="doc-A", version_id="ver-A"),
            _node(ns, "ver-B", kind="version", document_id="doc-A", version_id="ver-B"),
            _node(ns, "sec-2", kind="section", document_id="doc-A", version_id="ver-B"),
        ]
    )
    store.upsert_edges(
        [
            _edge(ns, "e1", source="ver-A", target="doc-A", version_id="ver-A"),
            _edge(ns, "e2", source="sec-1", target="ver-A", version_id="ver-A"),
            _edge(ns, "e3", source="ver-B", target="doc-A", version_id="ver-B"),
            _edge(ns, "e4", source="sec-2", target="ver-B", version_id="ver-B"),
        ]
    )

    store.delete_subgraph_for_version(document_id=f"{ns}-doc-A", version_id=f"{ns}-ver-A")

    page = store.find_subgraph(limit=MAX_GRAPH_PAGE_LIMIT)
    surviving_node_ids = {n.id for n in page.nodes}
    assert surviving_node_ids == _ids(ns, ["doc-A", "ver-B", "sec-2"])
    surviving_edge_ids = {e.id for e in page.edges}
    assert f"{ns}-e1" not in surviving_edge_ids
    assert f"{ns}-e2" not in surviving_edge_ids
    assert _ids(ns, ["e3", "e4"]) <= surviving_edge_ids


def test_find_subgraph_pagination_walks_deterministically(store: Neo4jGraphStore, ns: str) -> None:
    # 5 nodes, page size 2 — expect three pages: 2 + 2 + 1.
    store.upsert_nodes([_node(ns, f"sec-{i}") for i in range(5)])

    page_1 = store.find_subgraph(limit=2)
    assert len(page_1.nodes) == 2
    assert page_1.next_cursor is not None

    page_2 = store.find_subgraph(limit=2, cursor=page_1.next_cursor)
    assert len(page_2.nodes) == 2
    # The third page is exactly 1 node, which is < limit, so no further cursor.

    page_3 = store.find_subgraph(limit=2, cursor=page_2.next_cursor)
    assert len(page_3.nodes) == 1
    assert page_3.next_cursor is None

    # Pages are disjoint and union to the full set.
    seen = {n.id for n in page_1.nodes + page_2.nodes + page_3.nodes}
    assert seen == _ids(ns, [f"sec-{i}" for i in range(5)])


def test_find_subgraph_rejects_out_of_range_limit(store: Neo4jGraphStore) -> None:
    with pytest.raises(ValueError):
        store.find_subgraph(limit=0)
    with pytest.raises(ValueError):
        store.find_subgraph(limit=MAX_GRAPH_PAGE_LIMIT + 1)
