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
