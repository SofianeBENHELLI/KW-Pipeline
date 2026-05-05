"""Unit tests for ``_resolve_llm_provider`` and ``_maybe_build_llm``.

ADR-013 §6 multi-provider rules, locked down at the helper level so
the resolution logic is not coupled to the admin-route response shape.
"""

from __future__ import annotations

from app.dependencies import (
    _maybe_build_anthropic_llm,
    _maybe_build_llm,
    _resolve_llm_provider,
)
from app.services.knowledge.llm_client import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_GEMINI_MODEL,
    AnthropicLLMClient,
    GeminiLLMClient,
)
from app.settings import Settings


def _settings(**overrides: object) -> Settings:
    """Build a Settings instance with the named overrides applied."""
    return Settings(
        knowledge_layer_enabled_raw="true",
        **overrides,  # type: ignore[arg-type]
    )


# ─── _resolve_llm_provider — ``auto`` mode (default) ──────────────────────


def test_resolve_returns_none_when_knowledge_layer_disabled() -> None:
    s = Settings(
        knowledge_layer_enabled_raw="",
        gemini_api_key="ai-key",
        anthropic_api_key="sk-ant",
    )
    assert _resolve_llm_provider(s) is None


def test_resolve_auto_prefers_gemini_when_key_present() -> None:
    s = _settings(gemini_api_key="ai-key", anthropic_api_key="sk-ant")
    assert _resolve_llm_provider(s) == "gemini"


def test_resolve_auto_falls_back_to_anthropic_when_only_anthropic_set() -> None:
    s = _settings(gemini_api_key="", anthropic_api_key="sk-ant")
    assert _resolve_llm_provider(s) == "anthropic"


def test_resolve_auto_returns_none_when_neither_key_set() -> None:
    s = _settings()
    assert _resolve_llm_provider(s) is None


# ─── _resolve_llm_provider — pinned modes ─────────────────────────────────


def test_resolve_pinned_gemini_uses_gemini_when_key_present() -> None:
    s = _settings(llm_provider="gemini", gemini_api_key="ai-key", anthropic_api_key="sk-ant")
    assert _resolve_llm_provider(s) == "gemini"


def test_resolve_pinned_gemini_returns_none_when_key_missing() -> None:
    """Operators who pin a provider want a missing-key misconfig to surface."""
    s = _settings(llm_provider="gemini", gemini_api_key="", anthropic_api_key="sk-ant")
    assert _resolve_llm_provider(s) is None


def test_resolve_pinned_anthropic_uses_anthropic() -> None:
    s = _settings(llm_provider="anthropic", gemini_api_key="ai-key", anthropic_api_key="sk-ant")
    assert _resolve_llm_provider(s) == "anthropic"


def test_resolve_pinned_anthropic_returns_none_when_key_missing() -> None:
    s = _settings(llm_provider="anthropic", gemini_api_key="ai-key", anthropic_api_key="")
    assert _resolve_llm_provider(s) is None


# ─── _maybe_build_llm — concrete client construction ──────────────────────


def test_maybe_build_llm_returns_none_when_no_provider_resolves() -> None:
    s = Settings(knowledge_layer_enabled_raw="true")
    assert _maybe_build_llm(s) is None


def test_maybe_build_llm_returns_gemini_with_default_model() -> None:
    s = _settings(gemini_api_key="ai-key")
    built = _maybe_build_llm(s)
    assert built is not None
    llm, model = built
    assert isinstance(llm, GeminiLLMClient)
    assert model == DEFAULT_GEMINI_MODEL


def test_maybe_build_llm_honours_gemini_model_override() -> None:
    s = _settings(gemini_api_key="ai-key", gemini_model="gemini-2.5-pro")
    built = _maybe_build_llm(s)
    assert built is not None
    _, model = built
    assert model == "gemini-2.5-pro"


def test_maybe_build_llm_returns_anthropic_in_fallback_mode() -> None:
    s = _settings(anthropic_api_key="sk-ant")
    built = _maybe_build_llm(s)
    assert built is not None
    llm, model = built
    assert isinstance(llm, AnthropicLLMClient)
    assert model == DEFAULT_ANTHROPIC_MODEL


def test_maybe_build_llm_honours_anthropic_model_override() -> None:
    s = _settings(anthropic_api_key="sk-ant", anthropic_model="claude-opus-4-7")
    built = _maybe_build_llm(s)
    assert built is not None
    _, model = built
    assert model == "claude-opus-4-7"


def test_legacy_alias_still_resolves() -> None:
    """``_maybe_build_anthropic_llm`` is kept as an alias for backwards-compat."""
    assert _maybe_build_anthropic_llm is _maybe_build_llm
