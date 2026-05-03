"""End-to-end smoke test for the Phase 3 search path against real
Voyage + Neo4j (ADR-015, #186).

Marked ``embedding_integration`` so it is excluded from the default
suite (see ``pyproject.toml``: ``addopts = "-m 'not integration and
not llm_integration and not embedding_integration'"``). Run with::

    pytest -m embedding_integration

Skipped automatically when ``VOYAGE_API_KEY`` or the ``KW_NEO4J_*``
env vars are not set so contributors who haven't opted into the
real-cloud path don't see spurious failures.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

from app.schemas.knowledge import GraphNode
from app.services.knowledge import (
    KnowledgeSearchService,
    Neo4jGraphStore,
    VoyageEmbeddingClient,
)
from app.services.knowledge.graph_store import VECTOR_INDEX_NAME

pytestmark = pytest.mark.embedding_integration


@pytest.fixture(scope="module")
def voyage_client() -> VoyageEmbeddingClient:
    api_key = (
        os.environ.get("VOYAGE_API_KEY", "").strip()
        or os.environ.get("KW_VOYAGE_API_KEY", "").strip()
    )
    if not api_key:
        pytest.skip("VOYAGE_API_KEY not set; skipping embedding_integration tests")
    model = os.environ.get("KW_EMBEDDING_MODEL", "").strip() or None
    if model:
        return VoyageEmbeddingClient(api_key=api_key, model=model)
    return VoyageEmbeddingClient(api_key=api_key)


@pytest.fixture(scope="module")
def neo4j_config() -> dict[str, str]:
    uri = os.environ.get("KW_NEO4J_URI")
    user = os.environ.get("KW_NEO4J_USER")
    password = os.environ.get("KW_NEO4J_PASSWORD")
    missing = [
        n
        for n, v in (
            ("KW_NEO4J_URI", uri),
            ("KW_NEO4J_USER", user),
            ("KW_NEO4J_PASSWORD", password),
        )
        if not v
    ]
    if missing:
        pytest.skip(
            "Neo4j env vars missing for Voyage embedding integration: " + ", ".join(missing)
        )
    assert uri and user and password
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
    return f"voy-{uuid.uuid4().hex[:8]}"


def test_voyage_query_finds_seeded_chunk(
    voyage_client: VoyageEmbeddingClient,
    store: Neo4jGraphStore,
    ns: str,
) -> None:
    """Embed three semantically-distinct chunks, query for one of
    them, assert the matching chunk wins. Uses the canonical
    ``chunk_embedding`` index name so the route's startup hook would
    re-use it in production."""
    store.ensure_vector_index(name=VECTOR_INDEX_NAME, dim=voyage_client.dim)

    chunks = [
        ("c1", "ISO 9001 quality management certification process"),
        ("c2", "Boiler maintenance schedule and inspection checklist"),
        ("c3", "Annual financial report for fiscal year 2026"),
    ]
    nodes = [
        GraphNode(
            id=f"{ns}-{cid}",
            kind="chunk",
            label=f"label-{cid}",
            properties={
                "document_id": f"{ns}-doc-A",
                "version_id": f"{ns}-ver-A",
                "section_id": f"{ns}-{cid}",
                "text_preview": text,
            },
        )
        for cid, text in chunks
    ]
    store.upsert_nodes(nodes)
    vectors = voyage_client.embed_documents([text for _, text in chunks])
    for (cid, _), vector in zip(chunks, vectors, strict=True):
        store.set_chunk_embedding(chunk_id=f"{ns}-{cid}", embedding=vector)

    svc = KnowledgeSearchService(embedding_client=voyage_client, graph_store=store)
    response = svc.search("ISO 9001 audit programme", limit=3)

    assert response.embedding_model == "voyage"
    assert response.query_embedding_dim == voyage_client.dim
    # Top result should be the ISO chunk.
    assert response.results[0].chunk_id == f"{ns}-c1", (
        f"expected c1 first; got {[r.chunk_id for r in response.results]}"
    )
    assert response.results[0].score > response.results[-1].score
