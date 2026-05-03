"""HTTP-level tests for ``GET /knowledge/search`` (Phase 3, ADR-015).

Default ``pytest`` runs against :class:`FakeEmbeddingClient` and
:class:`InMemoryGraphStore`. The 503 path covers the "Phase 3
disabled" branch where neither ``KW_KNOWLEDGE_LAYER_ENABLED`` nor
``VOYAGE_API_KEY`` is set; the happy + error paths exercise the
service when both gates are wired.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import PipelineServices, build_services
from app.main import create_app
from app.schemas.knowledge import GraphNode
from app.services.knowledge import (
    FakeEmbeddingClient,
    InMemoryGraphStore,
    KnowledgeSearchService,
)


def _seed_chunks(store: InMemoryGraphStore, *texts: str) -> None:
    embedder = FakeEmbeddingClient(dim=16)
    nodes = [
        GraphNode(
            id=f"chunk-{i}",
            kind="chunk",
            label=f"chunk-{i}",
            properties={
                "document_id": "doc-A",
                "version_id": "ver-A",
                "section_id": f"chunk-{i}",
                "text_preview": text,
            },
        )
        for i, text in enumerate(texts)
    ]
    store.upsert_nodes(nodes)
    vectors = embedder.embed_documents(list(texts))
    for i, vector in enumerate(vectors):
        store.set_chunk_embedding(chunk_id=f"chunk-{i}", embedding=vector)


@pytest.fixture
def services_with_search() -> PipelineServices:
    base = build_services()
    store = InMemoryGraphStore()
    _seed_chunks(store, "the quick brown fox", "ISO 9001 compliance", "lorem ipsum")
    embedder = FakeEmbeddingClient(dim=16)
    search = KnowledgeSearchService(embedding_client=embedder, graph_store=store)
    return PipelineServices(
        storage=base.storage,
        documents=base.documents,
        parsers=base.parsers,
        extraction_jobs=base.extraction_jobs,
        semantic_extractor=base.semantic_extractor,
        markdown_generator=base.markdown_generator,
        semantic_outputs=base.semantic_outputs,
        idempotency=base.idempotency,
        graph_store=store,
        knowledge_projector=None,
        embedding_client=embedder,
        knowledge_search=search,
    )


@pytest.fixture
def client_with_search(services_with_search) -> TestClient:
    return TestClient(create_app(services_with_search))


@pytest.fixture
def client_without_search() -> TestClient:
    """Default services with no Phase 3 wiring → ``knowledge_search`` is None."""
    return TestClient(create_app(build_services()))


def test_search_returns_results_when_phase_3_is_wired(client_with_search):
    resp = client_with_search.get("/knowledge/search", params={"q": "ISO", "limit": 2})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["query"] == "ISO"
    assert payload["embedding_model"] == "fake-embedding"
    assert payload["query_embedding_dim"] == 16
    assert len(payload["results"]) == 2
    for hit in payload["results"]:
        assert hit["chunk_id"].startswith("chunk-")
        assert hit["document_id"] == "doc-A"
        assert -1.0 <= hit["score"] <= 1.0


def test_search_returns_503_when_disabled(client_without_search):
    resp = client_without_search.get("/knowledge/search", params={"q": "anything"})
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "KW_VECTOR_SEARCH_DISABLED"
    assert body["error"]["retryable"] is False
    assert "VOYAGE_API_KEY" in body["error"]["remediation"]


def test_search_validates_empty_query(client_with_search):
    resp = client_with_search.get("/knowledge/search", params={"q": ""})
    # FastAPI's Query(min_length=1) rejects empty before we hit the
    # service, so this is a 422 from request validation.
    assert resp.status_code == 422


def test_search_rejects_oversize_limit(client_with_search):
    resp = client_with_search.get("/knowledge/search", params={"q": "x", "limit": 9999})
    assert resp.status_code == 400
    assert "limit" in resp.json()["detail"]


def test_search_default_limit(client_with_search):
    resp = client_with_search.get("/knowledge/search", params={"q": "x"})
    assert resp.status_code == 200
    # Three chunks seeded, default limit is 10 → all three returned.
    assert len(resp.json()["results"]) == 3
