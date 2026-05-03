"""Tests for ``ChatService`` (Phase 3 chat — RAG mode).

The default ``pytest`` invocation runs against ``FakeLLMClient`` +
``FakeEmbeddingClient`` + ``InMemoryGraphStore``'s cosine shim — no
network, no Neo4j, no Anthropic. Real Voyage / Anthropic paths stay
behind ``-m embedding_integration`` / ``-m llm_integration``.
"""

from __future__ import annotations

import pytest

from app.schemas.knowledge import GraphNode
from app.services.knowledge import (
    ChatService,
    FakeEmbeddingClient,
    FakeLLMClient,
    InMemoryGraphStore,
    KnowledgeSearchService,
)


def _chunk(chunk_id: str, *, snippet: str, document_id: str = "doc-A") -> GraphNode:
    return GraphNode(
        id=chunk_id,
        kind="chunk",
        label=chunk_id,
        properties={
            "document_id": document_id,
            "version_id": f"ver-{document_id}",
            "section_id": chunk_id,
            "text_preview": snippet,
        },
    )


def _populated_store(*chunks: tuple[str, str]) -> tuple[InMemoryGraphStore, FakeEmbeddingClient]:
    """Helper: write chunk nodes + their FakeEmbeddingClient document
    embeddings into an in-memory store. Returns the store + the embedder
    used to seed (so tests can reuse the *same* embedder for the
    KnowledgeSearchService and get matching query/document vector
    pairs)."""
    store = InMemoryGraphStore()
    embedder = FakeEmbeddingClient(dim=16)
    nodes = [_chunk(cid, snippet=text) for cid, text in chunks]
    store.upsert_nodes(nodes)
    vectors = embedder.embed_documents([text for _, text in chunks])
    for (cid, _), vector in zip(chunks, vectors, strict=True):
        store.set_chunk_embedding(chunk_id=cid, embedding=vector)
    return store, embedder


def _service(
    store: InMemoryGraphStore,
    embedder: FakeEmbeddingClient,
    llm: FakeLLMClient,
    *,
    llm_model: str = "fake-claude",
) -> ChatService:
    search = KnowledgeSearchService(embedding_client=embedder, graph_store=store)
    return ChatService(search=search, llm=llm, llm_model=llm_model)


def test_rag_returns_answer_and_citations():
    store, embedder = _populated_store(
        ("c1", "ISO 9001 quality management"),
        ("c2", "Boiler maintenance schedule"),
        ("c3", "Annual financial report"),
    )
    llm = FakeLLMClient()
    llm.enqueue_chat(
        "Per [chunk-1] ISO 9001 covers quality management.",
        {"input_tokens": 200, "output_tokens": 30},
    )
    svc = _service(store, embedder, llm)

    response = svc.chat_rag(query="What does ISO 9001 cover?", top_k=3)

    assert response.mode == "rag"
    assert response.answer.startswith("Per [chunk-1]")
    assert response.embedding_model == "fake-embedding"
    assert response.llm_model == "fake-claude"
    assert response.token_usage["input_tokens"] == 200
    assert response.token_usage["output_tokens"] == 30
    assert {c.chunk_id for c in response.citations} == {"c1", "c2", "c3"}


def test_rag_short_circuits_when_index_is_empty():
    """Empty retrieval ⇒ deterministic 'no relevant content' answer.
    The LLM is NOT called so we don't burn tokens on noise."""
    store = InMemoryGraphStore()
    embedder = FakeEmbeddingClient(dim=16)
    llm = FakeLLMClient()  # no enqueued chat → would raise if called
    svc = _service(store, embedder, llm)

    response = svc.chat_rag(query="anything", top_k=5)

    assert response.citations == []
    assert "No relevant passages" in response.answer
    # No LLM call recorded.
    assert not any(c.get("method") == "complete_chat" for c in llm.calls)


def test_rag_top_k_bounds_the_retrieval():
    """``top_k`` is forwarded to the search service as the limit."""
    store, embedder = _populated_store(
        *((f"c{i}", f"chunk text {i}") for i in range(8)),
    )
    llm = FakeLLMClient()
    llm.enqueue_chat("Answer.")
    svc = _service(store, embedder, llm)

    response = svc.chat_rag(query="x", top_k=2)

    assert len(response.citations) == 2


def test_rag_rejects_empty_query():
    store, embedder = _populated_store(("c1", "x"))
    svc = _service(store, embedder, FakeLLMClient())
    with pytest.raises(ValueError):
        svc.chat_rag(query="", top_k=3)
    with pytest.raises(ValueError):
        svc.chat_rag(query="   ", top_k=3)


def test_rag_rejects_invalid_top_k():
    store, embedder = _populated_store(("c1", "x"))
    svc = _service(store, embedder, FakeLLMClient())
    with pytest.raises(ValueError):
        svc.chat_rag(query="q", top_k=0)
    with pytest.raises(ValueError):
        svc.chat_rag(query="q", top_k=999)


def test_rag_user_prompt_contains_chunk_markers_and_query():
    """The user prompt must pin each chunk to a stable bracketed
    marker the model can cite back, and place the query last."""
    store, embedder = _populated_store(
        ("c1", "ISO 9001 compliance"),
        ("c2", "Maintenance schedule"),
    )
    llm = FakeLLMClient()
    llm.enqueue_chat("Answer.")
    svc = _service(store, embedder, llm)

    svc.chat_rag(query="What does ISO cover?", top_k=2)

    chat_calls = [c for c in llm.calls if c.get("method") == "complete_chat"]
    assert len(chat_calls) == 1
    user = chat_calls[0]["user"]
    # Both chunk markers present.
    assert "[chunk-1]" in user
    assert "[chunk-2]" in user
    # Snippets included.
    assert "ISO 9001 compliance" in user
    assert "Maintenance schedule" in user
    # Query appears AFTER the passages.
    chunk_pos = user.rfind("[chunk-2]")
    query_pos = user.find("What does ISO cover?")
    assert chunk_pos < query_pos
