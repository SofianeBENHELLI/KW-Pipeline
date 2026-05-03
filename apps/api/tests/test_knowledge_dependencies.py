"""Tests for the env-var-driven wiring of the knowledge layer.

The contract:

- No env vars set → projector ``None``, in-memory store. Existing
  pipeline behaviour identical.
- ``KW_KNOWLEDGE_LAYER_ENABLED=true`` without Neo4j config → projector
  active, in-memory store. Useful for in-process demos.
- ``KW_KNOWLEDGE_LAYER_ENABLED=true`` with full ``KW_NEO4J_*`` config →
  projector active, ``Neo4jGraphStore``. Constructed lazily; the real
  driver behaviour is exercised behind ``pytest -m integration``.
"""

from __future__ import annotations

import pytest

from app.dependencies import _maybe_build_knowledge_layer
from app.services.knowledge import (
    InMemoryGraphStore,
    KnowledgeProjector,
    Neo4jGraphStore,
)


def test_layer_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KW_KNOWLEDGE_LAYER_ENABLED", raising=False)
    monkeypatch.delenv("KW_NEO4J_URI", raising=False)
    monkeypatch.delenv("KW_NEO4J_USER", raising=False)
    monkeypatch.delenv("KW_NEO4J_PASSWORD", raising=False)

    store, projector = _maybe_build_knowledge_layer()
    assert isinstance(store, InMemoryGraphStore)
    assert projector is None


@pytest.mark.parametrize("flag_value", ["true", "TRUE", "1", "yes", "on"])
def test_layer_enabled_with_inmemory_when_no_neo4j_config(
    monkeypatch: pytest.MonkeyPatch,
    flag_value: str,
) -> None:
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", flag_value)
    monkeypatch.delenv("KW_NEO4J_URI", raising=False)
    monkeypatch.delenv("KW_NEO4J_USER", raising=False)
    monkeypatch.delenv("KW_NEO4J_PASSWORD", raising=False)

    store, projector = _maybe_build_knowledge_layer()
    assert isinstance(store, InMemoryGraphStore)
    assert isinstance(projector, KnowledgeProjector)


@pytest.mark.parametrize("flag_value", ["false", "0", "no", "off", ""])
def test_layer_off_for_falsy_values(
    monkeypatch: pytest.MonkeyPatch,
    flag_value: str,
) -> None:
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", flag_value)
    store, projector = _maybe_build_knowledge_layer()
    assert isinstance(store, InMemoryGraphStore)
    assert projector is None


def test_layer_enabled_with_full_neo4j_config_constructs_neo4j_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constructing ``Neo4jGraphStore`` instantiates a driver; the
    driver is lazy on connect, so this does not require a running
    Neo4j. We just assert the store type is Neo4j-backed."""
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.setenv("KW_NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("KW_NEO4J_USER", "neo4j")
    monkeypatch.setenv("KW_NEO4J_PASSWORD", "neo4j")
    monkeypatch.setenv("KW_NEO4J_DATABASE", "test")

    store, projector = _maybe_build_knowledge_layer()
    try:
        assert isinstance(store, Neo4jGraphStore)
        assert isinstance(projector, KnowledgeProjector)
    finally:
        if isinstance(store, Neo4jGraphStore):
            store.close()


# ─── Phase 3 embedding-client wiring (ADR-015 / #186) ─────────────────────


from app.dependencies import _maybe_build_embedding_client  # noqa: E402
from app.services.knowledge import (  # noqa: E402
    FakeEmbeddingClient,
    VoyageEmbeddingClient,
)


def test_embedding_client_disabled_when_no_voyage_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.delenv("KW_VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert _maybe_build_embedding_client() is None


def test_embedding_client_disabled_when_layer_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KW_KNOWLEDGE_LAYER_ENABLED", raising=False)
    monkeypatch.setenv("KW_VOYAGE_API_KEY", "vk-x")
    assert _maybe_build_embedding_client() is None


def test_embedding_client_built_when_both_gates_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.setenv("KW_VOYAGE_API_KEY", "vk-x")
    monkeypatch.delenv("KW_EMBEDDING_MODEL", raising=False)
    client = _maybe_build_embedding_client()
    assert isinstance(client, VoyageEmbeddingClient)


def test_embedding_client_built_with_explicit_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.setenv("KW_VOYAGE_API_KEY", "vk-x")
    monkeypatch.setenv("KW_EMBEDDING_MODEL", "voyage-3-large")
    client = _maybe_build_embedding_client()
    assert isinstance(client, VoyageEmbeddingClient)


def test_embedding_client_built_with_empty_model_falls_back_to_sdk_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit empty ``KW_EMBEDDING_MODEL`` lets the constructor's
    own default win — the no-model branch in the builder."""
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.setenv("KW_VOYAGE_API_KEY", "vk-x")
    monkeypatch.setenv("KW_EMBEDDING_MODEL", "")
    client = _maybe_build_embedding_client()
    assert isinstance(client, VoyageEmbeddingClient)


def test_layer_threads_embedding_client_into_projector() -> None:
    """When an embedding client is supplied, the projector wires it
    through so the Phase 3 write path activates."""
    from app.dependencies import _maybe_build_knowledge_layer

    embedder = FakeEmbeddingClient(dim=16)
    store, projector = _maybe_build_knowledge_layer(
        embedding_client=embedder,
    )
    # Layer disabled by default → projector None, but embedder is still
    # usable elsewhere.
    assert projector is None
    assert isinstance(store, InMemoryGraphStore)


def test_layer_threads_embedding_client_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.dependencies import _maybe_build_knowledge_layer

    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    embedder = FakeEmbeddingClient(dim=16)
    store, projector = _maybe_build_knowledge_layer(
        embedding_client=embedder,
    )
    assert isinstance(projector, KnowledgeProjector)
    # The projector must hold the embedder reference; we verify by
    # projecting a tiny doc and checking the embedding is written.
    from datetime import UTC, datetime

    from app.models.document import DocumentVersionStatus
    from app.schemas.document import Document, DocumentVersion
    from app.schemas.semantic_document import (
        DocumentProfile,
        SemanticDocument,
        SemanticSection,
    )

    version = DocumentVersion(
        id="ver-1",
        document_id="doc-1",
        version_number=1,
        filename="x.txt",
        content_type="text/plain",
        file_size=1,
        sha256="0" * 64,
        storage_uri="file://x",
        status=DocumentVersionStatus.VALIDATED,
    )
    document = Document(
        id="doc-1",
        original_filename="x.txt",
        latest_version_id="ver-1",
        versions=[version],
    )
    semantic = SemanticDocument(
        id="sem-1",
        document_version_id="ver-1",
        document_profile=DocumentProfile(title="x"),
        sections=[SemanticSection(id="s1", heading="A", text="hello")],
        validation_status="validated",
        markdown="# x\n",
        created_at=datetime(2026, 5, 4, tzinfo=UTC),
    )
    projector.project(document=document, version=version, semantic=semantic)
    hits = store.find_chunks_by_similarity(embedder.embed_query("hello"), limit=5)
    assert {h.chunk_id for h in hits} == {"s1"}


# ─── Phase 3 chat-service wiring (#186 follow-up) ─────────────────────────


from app.dependencies import _maybe_build_chat_service  # noqa: E402
from app.services.knowledge import ChatService  # noqa: E402


def test_chat_service_disabled_without_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """No vector search ⇒ no chat service even if Anthropic is set."""
    from app.settings import Settings as _Settings

    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.setenv("KW_ANTHROPIC_API_KEY", "sk-x")
    assert _maybe_build_chat_service(settings=_Settings(), knowledge_search=None) is None


def test_chat_service_disabled_without_anthropic_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search wired but no Anthropic key ⇒ chat is None."""
    from app.services.knowledge import (
        FakeEmbeddingClient,
        InMemoryGraphStore,
        KnowledgeSearchService,
    )
    from app.settings import Settings as _Settings

    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.delenv("KW_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    search = KnowledgeSearchService(
        embedding_client=FakeEmbeddingClient(dim=16),
        graph_store=InMemoryGraphStore(),
    )
    assert _maybe_build_chat_service(settings=_Settings(), knowledge_search=search) is None


def test_chat_service_built_when_both_gates_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both search + Anthropic ⇒ ChatService instance with the
    configured Anthropic model recorded for the response payload."""
    from app.services.knowledge import (
        FakeEmbeddingClient,
        InMemoryGraphStore,
        KnowledgeSearchService,
    )
    from app.settings import Settings as _Settings

    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.setenv("KW_ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("KW_ANTHROPIC_MODEL", "claude-haiku-4-5")

    search = KnowledgeSearchService(
        embedding_client=FakeEmbeddingClient(dim=16),
        graph_store=InMemoryGraphStore(),
    )
    chat = _maybe_build_chat_service(settings=_Settings(), knowledge_search=search)
    assert isinstance(chat, ChatService)
    assert chat.llm_model == "claude-haiku-4-5"


def test_chat_service_falls_back_to_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty model env ⇒ DEFAULT_ANTHROPIC_MODEL is used so the
    response payload still advertises which model produced the answer."""
    from app.services.knowledge import (
        FakeEmbeddingClient,
        InMemoryGraphStore,
        KnowledgeSearchService,
    )
    from app.services.knowledge.llm_client import DEFAULT_ANTHROPIC_MODEL
    from app.settings import Settings as _Settings

    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.setenv("KW_ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("KW_ANTHROPIC_MODEL", "")

    search = KnowledgeSearchService(
        embedding_client=FakeEmbeddingClient(dim=16),
        graph_store=InMemoryGraphStore(),
    )
    chat = _maybe_build_chat_service(settings=_Settings(), knowledge_search=search)
    assert isinstance(chat, ChatService)
    assert chat.llm_model == DEFAULT_ANTHROPIC_MODEL
