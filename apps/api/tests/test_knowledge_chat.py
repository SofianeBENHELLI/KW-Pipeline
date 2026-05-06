"""Tests for ``KnowledgeChatService`` + ``POST /knowledge/chat`` (Phase 3).

Default ``pytest`` runs against :class:`FakeEmbeddingClient`,
:class:`FakeLLMClient`, and :class:`InMemoryGraphStore` — no network,
no Anthropic key, no Voyage key. Real provider integration lives
behind ``pytest -m llm_integration`` / ``-m embedding_integration``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import PipelineServices, build_services
from app.main import create_app
from app.schemas.knowledge import GraphEdge, GraphNode
from app.services.knowledge import (
    FakeEmbeddingClient,
    FakeLLMClient,
    InMemoryGraphStore,
    KnowledgeChatService,
    KnowledgeSearchService,
)

# ─── Helpers ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _disable_scope_filter(monkeypatch):
    """Bypass the D.5 scope filter — these tests seed chunk graph
    nodes referencing synthetic ``doc-A`` ids that don't live in the
    catalog, so the scope predicate would drop every retrieval hit.
    Legacy ``KW_AUTH_MODE=disabled`` skips the predicate so the chat
    grounding contract under test remains reachable."""
    monkeypatch.setenv("KW_AUTH_MODE", "disabled")


def _chunk_node(chunk_id: str, *, snippet: str | None = None) -> GraphNode:
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
    """Mirror of the helper in ``test_knowledge_search.py``."""
    store = InMemoryGraphStore()
    embedder = FakeEmbeddingClient(dim=16)
    nodes = [_chunk_node(cid, snippet=text) for cid, text in chunks]
    store.upsert_nodes(nodes)
    vectors = embedder.embed_documents([text for _, text in chunks])
    for (cid, _), vector in zip(chunks, vectors, strict=True):
        store.set_chunk_embedding(chunk_id=cid, embedding=vector)
    return store


def _entity_pair(
    *,
    document_id: str,
    version_id: str,
    subject: str,
    predicate: str,
    obj: str,
) -> tuple[GraphNode, GraphNode, GraphEdge]:
    """Build a minimal ``(:Entity)-[:HAS_ENTITY]->(:Entity)`` triple.

    Only the fields the chat service reads for prompt construction are
    populated: ``label``, ``kind``, ``properties.predicate`` plus the
    ``document_id`` property the in-memory store uses to scope
    ``find_subgraph_for_document`` lookups.
    """
    subj_node = GraphNode(
        id=f"entity-{subject}",
        kind="entity",
        label=subject,
        properties={"document_id": document_id, "version_id": version_id},
    )
    obj_node = GraphNode(
        id=f"entity-{obj}",
        kind="entity",
        label=obj,
        properties={"document_id": document_id, "version_id": version_id},
    )
    edge = GraphEdge(
        id=f"edge-{subject}-{predicate}-{obj}",
        source_id=subj_node.id,
        target_id=obj_node.id,
        kind="has_entity",
        properties={
            "document_id": document_id,
            "version_id": version_id,
            "predicate": predicate,
            "source_reference_id": "ref-1",
        },
    )
    return subj_node, obj_node, edge


def _chat_service(
    *,
    store: InMemoryGraphStore,
    fake_llm: FakeLLMClient,
) -> KnowledgeChatService:
    embedder = FakeEmbeddingClient(dim=16)
    search = KnowledgeSearchService(embedding_client=embedder, graph_store=store)
    return KnowledgeChatService(
        search=search,
        graph_store=store,
        llm=fake_llm,
        llm_model="claude-test",
    )


# ─── Service-level tests ─────────────────────────────────────────────────


def test_rag_mode_grounds_prompt_in_chunk_excerpts():
    store = _populated_store(
        ("c1", "ISO 9001 audit calendar"),
        ("c2", "lorem ipsum dolor sit amet"),
    )
    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text(
        "The audit calendar is in [c1].",
        token_usage={"input_tokens": 42, "output_tokens": 8},
    )
    svc = _chat_service(store=store, fake_llm=fake_llm)

    response = svc.answer("When is the audit?", mode="rag", top_k=2)

    assert response.mode == "rag"
    assert response.answer == "The audit calendar is in [c1]."
    assert response.llm_model == "claude-test"
    assert response.embedding_model == "fake-embedding"
    assert response.token_usage == {"input_tokens": 42, "output_tokens": 8}
    assert {c.chunk_id for c in response.citations} <= {"c1", "c2"}

    # The prompt block was a chunk-context block, not a graph one.
    assert len(fake_llm.text_calls) == 1
    user_prompt = fake_llm.text_calls[0]["user"]
    assert "Chunk context:" in user_prompt
    assert "Graph context:" not in user_prompt
    assert "When is the audit?" in user_prompt


def test_graph_mode_grounds_prompt_in_projected_triples():
    store = _populated_store(("c1", "compliance text"))
    subj, obj, edge = _entity_pair(
        document_id="doc-A",
        version_id="ver-A",
        subject="ACME Corp",
        predicate="must_certify",
        obj="ISO 9001",
    )
    store.upsert_nodes([subj, obj])
    store.upsert_edges([edge])

    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("ACME must certify ISO 9001 [doc:doc-A].")
    svc = _chat_service(store=store, fake_llm=fake_llm)

    response = svc.answer("What standard does ACME need?", mode="graph", top_k=1)

    assert response.mode == "graph"
    user_prompt = fake_llm.text_calls[0]["user"]
    assert "Graph context:" in user_prompt
    assert "Chunk context:" not in user_prompt
    # Predicate from the edge property surfaces in the rendered triple,
    # not the raw ``has_entity`` kind.
    assert "must_certify" in user_prompt
    assert "ACME Corp" in user_prompt
    assert "ISO 9001" in user_prompt


def test_hybrid_mode_renders_both_context_blocks():
    store = _populated_store(("c1", "policy excerpt"))
    subj, obj, edge = _entity_pair(
        document_id="doc-A",
        version_id="ver-A",
        subject="Subj",
        predicate="rel",
        obj="Obj",
    )
    store.upsert_nodes([subj, obj])
    store.upsert_edges([edge])

    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("hybrid answer")
    svc = _chat_service(store=store, fake_llm=fake_llm)
    response = svc.answer("anything", mode="hybrid", top_k=1)

    user_prompt = fake_llm.text_calls[0]["user"]
    assert "Chunk context:" in user_prompt
    assert "Graph context:" in user_prompt
    assert response.mode == "hybrid"


def test_empty_question_is_rejected():
    svc = _chat_service(store=_populated_store(), fake_llm=FakeLLMClient())
    with pytest.raises(ValueError):
        svc.answer("   ", mode="rag")


def test_graph_mode_with_no_projected_entities_falls_back_gracefully():
    """No entity edges ⇒ context block names that explicitly + answer still produced."""
    store = _populated_store(("c1", "text without entities"))
    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("I don't have enough context to answer that.")
    svc = _chat_service(store=store, fake_llm=fake_llm)

    response = svc.answer("anything", mode="graph", top_k=1)
    user_prompt = fake_llm.text_calls[0]["user"]
    assert "no projected triples" in user_prompt
    assert response.answer.startswith("I don't have enough")


def test_empty_retrieval_short_circuits_without_calling_llm():
    """Zero hits ⇒ deterministic answer, no LLM round-trip, empty token_usage."""
    from app.services.knowledge.chat_service import EMPTY_RETRIEVAL_ANSWER

    store = InMemoryGraphStore()
    fake_llm = FakeLLMClient()
    # Intentionally do NOT enqueue a text response — if the service
    # called the LLM the FakeLLMClient would raise.
    svc = _chat_service(store=store, fake_llm=fake_llm)

    response = svc.answer("question", mode="rag", top_k=3)

    assert fake_llm.text_calls == []
    assert response.answer == EMPTY_RETRIEVAL_ANSWER
    assert response.citations == []
    assert response.token_usage == {}
    assert response.warnings == []


def test_empty_retrieval_short_circuits_for_every_mode():
    """Empty hits ⇒ short-circuit in every retrieval mode."""
    store = InMemoryGraphStore()
    for mode in ("rag", "graph", "hybrid"):
        fake_llm = FakeLLMClient()  # no enqueue_text — would raise on call
        svc = _chat_service(store=store, fake_llm=fake_llm)
        response = svc.answer("q", mode=mode, top_k=3)  # type: ignore[arg-type]
        assert fake_llm.text_calls == []
        assert response.mode == mode
        assert response.citations == []


def test_rag_mode_omits_snippet_line_for_chunks_without_text_preview():
    """text_preview unset ⇒ chunk block has no snippet line for that chunk."""
    store = InMemoryGraphStore()
    embedder = FakeEmbeddingClient(dim=16)
    # Chunk with no snippet (text_preview=None).
    bare = GraphNode(
        id="c0",
        kind="chunk",
        label="c0",
        properties={
            "document_id": "doc-A",
            "version_id": "ver-A",
            "section_id": "c0",
            "text_preview": None,
        },
    )
    store.upsert_nodes([bare])
    [vec] = embedder.embed_documents([""])
    store.set_chunk_embedding(chunk_id="c0", embedding=vec)

    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("ok")
    svc = _chat_service(store=store, fake_llm=fake_llm)
    svc.answer("anything", mode="rag", top_k=1)

    user_prompt = fake_llm.text_calls[0]["user"]
    assert "chunk_id=c0" in user_prompt
    assert "snippet=" not in user_prompt


def test_graph_mode_de_duplicates_documents_and_skips_non_entity_edges():
    """Dup document_ids ⇒ one walk; non-`has_entity` edges ignored."""
    store = _populated_store(
        ("c1", "first chunk for doc-A"),
        ("c2", "second chunk for doc-A"),
    )
    subj, obj, edge = _entity_pair(
        document_id="doc-A",
        version_id="ver-A",
        subject="ACME",
        predicate="part_of",
        obj="Group",
    )
    # Add a non-`has_entity` edge that should be ignored.
    structural = GraphEdge(
        id="edge-structural",
        source_id=subj.id,
        target_id=obj.id,
        kind="related_to",
        properties={"document_id": "doc-A", "version_id": "ver-A"},
    )
    store.upsert_nodes([subj, obj])
    store.upsert_edges([edge, structural])

    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("ok")
    svc = _chat_service(store=store, fake_llm=fake_llm)
    svc.answer("q", mode="graph", top_k=2)

    prompt = fake_llm.text_calls[0]["user"]
    # Exactly one rendered triple: dedup + non-has_entity skip.
    assert prompt.count("[doc:doc-A]") == 1
    assert "part_of" in prompt
    assert "related_to" not in prompt


def test_graph_mode_handles_dangling_edges_gracefully():
    """Edge whose source/target node is missing from the projection is skipped."""
    store = _populated_store(("c1", "anything"))
    # Insert a has_entity edge whose endpoints don't exist as nodes.
    dangling = GraphEdge(
        id="edge-dangling",
        source_id="entity-missing-subject",
        target_id="entity-missing-object",
        kind="has_entity",
        properties={
            "document_id": "doc-A",
            "version_id": "ver-A",
            "predicate": "ghost",
        },
    )
    store.upsert_edges([dangling])

    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("ok")
    svc = _chat_service(store=store, fake_llm=fake_llm)
    svc.answer("q", mode="graph", top_k=1)

    prompt = fake_llm.text_calls[0]["user"]
    assert "ghost" not in prompt
    assert "no projected triples" in prompt


def test_predicate_falls_back_to_edge_kind_when_property_missing():
    """Edge without `predicate` property ⇒ rendered triple uses `edge.kind`."""
    store = _populated_store(("c1", "anything"))
    subj = GraphNode(
        id="entity-A",
        kind="entity",
        label="A",
        properties={"document_id": "doc-A", "version_id": "ver-A"},
    )
    obj = GraphNode(
        id="entity-B",
        kind="entity",
        label="B",
        properties={"document_id": "doc-A", "version_id": "ver-A"},
    )
    edge = GraphEdge(
        id="edge-no-predicate",
        source_id=subj.id,
        target_id=obj.id,
        kind="has_entity",
        properties={"document_id": "doc-A", "version_id": "ver-A"},
    )
    store.upsert_nodes([subj, obj])
    store.upsert_edges([edge])

    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("ok")
    svc = _chat_service(store=store, fake_llm=fake_llm)
    svc.answer("q", mode="graph", top_k=1)

    prompt = fake_llm.text_calls[0]["user"]
    assert "-[has_entity]->" in prompt


def test_llm_model_property_exposes_constructor_value():
    svc = _chat_service(store=_populated_store(), fake_llm=FakeLLMClient())
    assert svc.llm_model == "claude-test"


# ─── Server-side citation validation ─────────────────────────────────────


def test_valid_citations_produce_no_warnings():
    """Answer cites only chunk_ids that match the returned citations."""
    store = _populated_store(
        ("c1", "ISO 9001 audit calendar"),
        ("c2", "compliance"),
    )
    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("The audit is in [c1] and the policy is in [c2].")
    svc = _chat_service(store=store, fake_llm=fake_llm)
    response = svc.answer("when is the audit?", mode="rag", top_k=2)
    assert response.warnings == []


def test_unresolved_chunk_citation_is_flagged():
    """Answer cites [c-fake] not in citations ⇒ flagged in warnings."""
    store = _populated_store(("c1", "audit calendar"))
    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("The audit is in [c-fake].")
    svc = _chat_service(store=store, fake_llm=fake_llm)
    response = svc.answer("question", mode="rag", top_k=1)
    assert "[c-fake]" in response.warnings
    # Real citation is still returned; the answer text is unchanged.
    assert response.answer == "The audit is in [c-fake]."
    assert {c.chunk_id for c in response.citations} == {"c1"}


def test_unresolved_doc_citation_is_flagged():
    """``[doc:X]`` where X isn't a returned document_id ⇒ flagged."""
    store = _populated_store(("c1", "policy"))
    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("ACME must comply [doc:doc-fake].")
    svc = _chat_service(store=store, fake_llm=fake_llm)
    response = svc.answer("question", mode="graph", top_k=1)
    assert "[doc:doc-fake]" in response.warnings


def test_natural_prose_brackets_are_not_flagged():
    """``[Section 1]`` / ``[see appendix]`` shouldn't be treated as citations."""
    store = _populated_store(("c1", "policy"))
    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("[See Appendix A] and [Section 1] are referenced.")
    svc = _chat_service(store=store, fake_llm=fake_llm)
    response = svc.answer("q", mode="rag", top_k=1)
    # Inner text contains spaces ⇒ regex doesn't match the citation
    # pattern; nothing flagged.
    assert response.warnings == []


def test_duplicate_unresolved_citation_is_reported_once():
    """Same hallucinated marker repeated in the answer ⇒ one warning, not many."""
    store = _populated_store(("c1", "policy"))
    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("First mention [c-bad]. Second mention [c-bad]. Third [c-bad].")
    svc = _chat_service(store=store, fake_llm=fake_llm)
    response = svc.answer("q", mode="rag", top_k=1)
    assert response.warnings.count("[c-bad]") == 1


def test_chunk_and_doc_unresolved_markers_are_both_reported():
    store = _populated_store(("c1", "policy"))
    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("Two ghosts: [c-fake] and [doc:doc-fake].")
    svc = _chat_service(store=store, fake_llm=fake_llm)
    response = svc.answer("q", mode="hybrid", top_k=1)
    assert "[c-fake]" in response.warnings
    assert "[doc:doc-fake]" in response.warnings


# ─── Direct unit tests for module-level helpers ──────────────────────────


def test_validate_citations_returns_empty_for_empty_answer():
    """Defensive guard — empty answer text yields no warnings."""
    from app.services.knowledge.chat_service import _validate_citations

    assert _validate_citations("", []) == []


def test_format_chunk_block_handles_empty_hits():
    """Defensive copy when callers exercise the helper directly.

    The :class:`KnowledgeChatService` short-circuits before calling
    this helper with empty hits today, but the helper is module-level
    and a future caller (e.g. an alternative orchestrator) may still
    pass an empty list.
    """
    from app.services.knowledge.chat_service import _format_chunk_block

    out = _format_chunk_block([])
    assert "no matching chunks" in out


# ─── Route-level tests ───────────────────────────────────────────────────


def _services_with_chat(
    fake_llm: FakeLLMClient,
    store: InMemoryGraphStore,
) -> PipelineServices:
    """Build a ``PipelineServices`` whose chat service is wired with fakes."""
    base = build_services()
    embedder = FakeEmbeddingClient(dim=16)
    search = KnowledgeSearchService(embedding_client=embedder, graph_store=store)
    chat = KnowledgeChatService(
        search=search,
        graph_store=store,
        llm=fake_llm,
        llm_model="claude-test",
    )
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
        knowledge_projector=base.knowledge_projector,
        entity_extractor=base.entity_extractor,
        embedding_client=embedder,
        knowledge_search=search,
        knowledge_chat=chat,
        settings=base.settings,
    )


def test_chat_route_returns_503_when_disabled():
    """Default ``build_services()`` ⇒ no LLM / Voyage key ⇒ chat is None."""
    client = TestClient(create_app(services=build_services()))

    response = client.post("/knowledge/chat", json={"question": "hi"})
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "KW_CHAT_DISABLED"
    assert body["error"]["retryable"] is False
    # The remediation copy ships the exact env-var hint operators need:
    # either LLM key (Gemini or Anthropic, ADR-013 §6) plus Voyage.
    remediation = body["error"]["remediation"]
    assert "GEMINI_API_KEY" in remediation
    assert "ANTHROPIC_API_KEY" in remediation
    assert "VOYAGE_API_KEY" in remediation


def test_chat_route_returns_grounded_answer():
    store = _populated_store(("c1", "audit calendar"))
    fake_llm = FakeLLMClient()
    fake_llm.enqueue_text("Grounded answer [c1].")
    services = _services_with_chat(fake_llm, store)
    client = TestClient(create_app(services=services))

    response = client.post(
        "/knowledge/chat",
        json={"question": "when is the audit?", "mode": "rag", "top_k": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Grounded answer [c1]."
    assert body["mode"] == "rag"
    assert body["llm_model"] == "claude-test"
    assert body["embedding_model"] == "fake-embedding"
    assert body["citations"][0]["chunk_id"] == "c1"


def test_chat_route_validates_question_length():
    store = _populated_store(("c1", "anything"))
    services = _services_with_chat(FakeLLMClient(), store)
    client = TestClient(create_app(services=services))

    # Empty string fails Pydantic min_length=1.
    response = client.post("/knowledge/chat", json={"question": ""})
    assert response.status_code == 422

    # Whitespace-only passes Pydantic but the service raises ValueError
    # which the route maps to 422.
    response = client.post("/knowledge/chat", json={"question": "   "})
    assert response.status_code == 422
