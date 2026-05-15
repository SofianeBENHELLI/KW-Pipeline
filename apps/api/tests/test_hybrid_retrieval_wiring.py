"""Tests for ``KW_HYBRID_RETRIEVAL_ENABLED`` DI wiring (EPIC-4 §4.3).

The settings flag is the seam: when truthy, ``build_services()``
wraps the vector-only ``KnowledgeSearchService`` in a
``HybridSearchService`` so ``GET /knowledge/search`` and
``POST /knowledge/chat`` consume fused (vector + BM25) results.
Default off — the MVP demo posture stays vector-only.

This file pins the three flag → service-shape transitions and the
properties operators rely on (the chat service still reads
``embedding_model`` consistently in both modes).
"""

from __future__ import annotations

import pytest

from app.dependencies import build_services
from app.services.knowledge import (
    HybridSearchService,
    KnowledgeSearchService,
)


@pytest.fixture
def voyage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable the knowledge layer + supply a Voyage key so an embedding
    client + vector search are wired. Tests in this file gate on the
    *retrieval* flag, not the layer / Voyage gates."""
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.setenv("KW_VOYAGE_API_KEY", "test-key")


def test_hybrid_flag_off_keeps_vector_only_search(
    voyage_env: None,
) -> None:
    """Default deployment — the flag is unset. ``knowledge_search`` is
    the vector-only :class:`KnowledgeSearchService`. No hybrid wrap."""
    services = build_services()
    assert services.knowledge_search is not None
    assert isinstance(services.knowledge_search, KnowledgeSearchService)
    assert not isinstance(services.knowledge_search, HybridSearchService)


def test_hybrid_flag_on_wraps_vector_in_hybrid_service(
    voyage_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``KW_HYBRID_RETRIEVAL_ENABLED=true``, the vector search is
    wrapped — ``services.knowledge_search`` returns a
    :class:`HybridSearchService` that exposes the same
    ``search(query, *, limit=...)`` surface."""
    monkeypatch.setenv("KW_HYBRID_RETRIEVAL_ENABLED", "true")
    services = build_services()
    assert isinstance(services.knowledge_search, HybridSearchService)
    # Drop-in surface: same ``embedding_model`` property so the chat
    # service's log emission stays stable.
    assert services.knowledge_search.embedding_model


def test_hybrid_flag_without_embedding_client_stays_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hybrid is opt-in on top of the vector retriever. With no Voyage
    key the vector retriever isn't wired, so the hybrid wrap is also
    skipped — ``knowledge_search`` stays ``None``. The flag alone
    can't conjure a retriever out of nothing."""
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.setenv("KW_HYBRID_RETRIEVAL_ENABLED", "true")
    monkeypatch.delenv("KW_VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    services = build_services()
    assert services.knowledge_search is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("", False),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("garbage", False),
    ],
)
def test_hybrid_retrieval_flag_truthiness(
    raw: str,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings flag truthiness mirrors :attr:`Settings.knowledge_layer_enabled`."""
    from app.settings import Settings

    monkeypatch.setenv("KW_HYBRID_RETRIEVAL_ENABLED", raw)
    settings = Settings()
    assert settings.hybrid_retrieval_enabled is expected
