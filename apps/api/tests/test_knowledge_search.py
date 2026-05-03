"""Tests for ``KnowledgeSearchService`` (Phase 3, ADR-015 / #186).

Default ``pytest`` runs against :class:`FakeEmbeddingClient` and
:class:`InMemoryGraphStore`'s brute-force cosine shim — no network, no
Neo4j. Real Voyage + Neo4j vector index are exercised in
``tests/integration/test_voyage_neo4j_search.py`` behind
``pytest -m embedding_integration``.
"""

from __future__ import annotations

import pytest

from app.schemas.knowledge import GraphNode
from app.services.knowledge import (
    FakeEmbeddingClient,
    InMemoryGraphStore,
    KnowledgeSearchService,
)


def _chunk(chunk_id: str, *, snippet: str | None = None) -> GraphNode:
    return GraphNode(
        id=chunk_id,
        kind="chunk",
        label=chunk_id,
        properties={
            "document_id": "doc-A",
            "version_id": "ver-A",
            "section_id": chunk_id,
            "text_preview": snippet,
        },
    )


def _populated_store(*chunks: tuple[str, str]) -> InMemoryGraphStore:
    """Helper: write chunk nodes + their FakeEmbeddingClient document
    embeddings into an in-memory store. ``chunks`` is a sequence of
    ``(chunk_id, text)`` tuples."""
    store = InMemoryGraphStore()
    embedder = FakeEmbeddingClient(dim=16)
    nodes = [_chunk(cid, snippet=text) for cid, text in chunks]
    store.upsert_nodes(nodes)
    vectors = embedder.embed_documents([text for _, text in chunks])
    for (cid, _), vector in zip(chunks, vectors, strict=True):
        store.set_chunk_embedding(chunk_id=cid, embedding=vector)
    return store


def test_search_returns_top_k_ranked_results():
    store = _populated_store(
        ("c1", "the quick brown fox"),
        ("c2", "lorem ipsum dolor sit amet"),
        ("c3", "compliance with ISO 9001 standard"),
    )
    embedder = FakeEmbeddingClient(dim=16)
    svc = KnowledgeSearchService(embedding_client=embedder, graph_store=store)

    response = svc.search("ISO 9001 audit", limit=2)

    assert response.embedding_model == "fake-embedding"
    assert response.query_embedding_dim == 16
    assert len(response.results) == 2
    # Order is deterministic for the fake; we don't pin which chunk
    # comes first because the fake's cosine values aren't semantic, but
    # we *do* assert every result is one of the indexed chunks.
    assert {r.chunk_id for r in response.results}.issubset({"c1", "c2", "c3"})


def test_search_returns_empty_results_for_empty_index():
    """No chunks embedded yet ⇒ 200 with empty results, not 404 / 500."""
    store = InMemoryGraphStore()
    svc = KnowledgeSearchService(
        embedding_client=FakeEmbeddingClient(dim=16),
        graph_store=store,
    )
    response = svc.search("anything", limit=10)
    assert response.results == []
    assert response.query == "anything"


def test_search_rejects_empty_query():
    svc = KnowledgeSearchService(
        embedding_client=FakeEmbeddingClient(dim=16),
        graph_store=InMemoryGraphStore(),
    )
    with pytest.raises(ValueError):
        svc.search("", limit=5)
    with pytest.raises(ValueError):
        svc.search("   ", limit=5)


def test_search_rejects_invalid_limit():
    svc = KnowledgeSearchService(
        embedding_client=FakeEmbeddingClient(dim=16),
        graph_store=InMemoryGraphStore(),
    )
    with pytest.raises(ValueError):
        svc.search("q", limit=0)
    with pytest.raises(ValueError):
        svc.search("q", limit=999)


def test_search_carries_chunk_locator_metadata():
    store = _populated_store(("c1", "alpha"), ("c2", "beta"))
    svc = KnowledgeSearchService(
        embedding_client=FakeEmbeddingClient(dim=16),
        graph_store=store,
    )
    response = svc.search("alpha", limit=2)
    for r in response.results:
        assert r.document_id == "doc-A"
        assert r.version_id == "ver-A"
        assert r.section_id  # chunk_id == section_id in the test fixture
        assert r.score >= -1.0 and r.score <= 1.0


def test_search_uses_query_embedding_not_document_embedding():
    """Asymmetric encoder: a query for a known doc text should NOT score
    1.0 (the fake salts queries vs documents differently)."""
    store = _populated_store(("c1", "exact match"))
    svc = KnowledgeSearchService(
        embedding_client=FakeEmbeddingClient(dim=16, asymmetric=True),
        graph_store=store,
    )
    response = svc.search("exact match", limit=1)
    # Asymmetric: similar but not 1.0.
    assert response.results[0].score < 1.0
