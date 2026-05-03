"""HTTP-level tests for ``POST /chat/rag`` (Phase 3 chat — RAG mode)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import PipelineServices, build_services
from app.main import create_app
from app.schemas.knowledge import GraphNode
from app.services.knowledge import (
    ChatService,
    FakeEmbeddingClient,
    FakeLLMClient,
    InMemoryGraphStore,
    KnowledgeSearchService,
)


def _seed_chunks(store: InMemoryGraphStore, *texts: str) -> FakeEmbeddingClient:
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
    return embedder


@pytest.fixture
def services_with_chat() -> PipelineServices:
    base = build_services()
    store = InMemoryGraphStore()
    embedder = _seed_chunks(store, "ISO 9001 compliance", "Boiler maintenance", "Annual report")
    search = KnowledgeSearchService(embedding_client=embedder, graph_store=store)
    llm = FakeLLMClient()
    # Pre-load enough chat responses so multi-test fixtures can drive
    # several requests off one client. The route only consumes one per
    # call; extras are harmless because each test gets a fresh fixture.
    llm.enqueue_chat(
        "Per [chunk-1] ISO 9001 covers quality management systems.",
        {"input_tokens": 250, "output_tokens": 25},
    )
    chat = ChatService(search=search, llm=llm, llm_model="fake-claude")
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
        chat=chat,
    )


@pytest.fixture
def client_with_chat(services_with_chat) -> TestClient:
    return TestClient(create_app(services_with_chat))


@pytest.fixture
def client_without_chat() -> TestClient:
    """Default services (no Phase 3 wiring) → ``chat`` is None."""
    return TestClient(create_app(build_services()))


def test_chat_rag_returns_answer_and_citations(client_with_chat):
    resp = client_with_chat.post(
        "/chat/rag",
        json={"query": "What does ISO 9001 cover?", "top_k": 3},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "rag"
    assert body["query"] == "What does ISO 9001 cover?"
    assert body["embedding_model"] == "fake-embedding"
    assert body["llm_model"] == "fake-claude"
    assert body["answer"].startswith("Per [chunk-1]")
    assert len(body["citations"]) == 3
    for c in body["citations"]:
        assert c["chunk_id"].startswith("chunk-")
        assert c["document_id"] == "doc-A"
        assert -1.0 <= c["score"] <= 1.0


def test_chat_rag_returns_503_when_disabled(client_without_chat):
    resp = client_without_chat.post(
        "/chat/rag",
        json={"query": "anything"},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "KW_CHAT_DISABLED"
    assert body["error"]["retryable"] is False
    # Remediation should mention all three required env vars so the
    # frontend can show one actionable banner.
    remediation = body["error"]["remediation"]
    assert "KW_KNOWLEDGE_LAYER_ENABLED" in remediation
    assert "VOYAGE_API_KEY" in remediation
    assert "ANTHROPIC_API_KEY" in remediation


def test_chat_rag_validates_empty_query(client_with_chat):
    """FastAPI's ``Field(min_length=1)`` rejects empty before the
    service is even called → 422 from request validation."""
    resp = client_with_chat.post("/chat/rag", json={"query": ""})
    assert resp.status_code == 422


def test_chat_rag_validates_top_k_bounds(client_with_chat):
    """``Field(le=20)`` rejects oversize top_k → 422."""
    resp = client_with_chat.post(
        "/chat/rag",
        json={"query": "x", "top_k": 999},
    )
    assert resp.status_code == 422


def test_chat_rag_default_top_k_is_5(client_with_chat):
    """No top_k supplied → defaults to 5; with 3 chunks indexed we
    get all 3 back."""
    resp = client_with_chat.post("/chat/rag", json={"query": "x"})
    assert resp.status_code == 200
    assert len(resp.json()["citations"]) == 3
