"""Wiring tests for the optional spaCy NER enricher (#190).

The chain assembled by ``_build_enrichers`` must:

- always include the deterministic ``RuleBasedEntityEnricher``;
- include ``SpacyNerEnricher`` only when ``KW_NER_ENABLED`` is truthy;
- treat falsy / unset values exactly as the existing kill switches do.

These tests do not exercise the spaCy SDK — they construct the chain
factory with settings overrides and assert on the resulting types.
``SpacyNerEnricher`` itself is exercised against a stub ``nlp`` in
``test_spacy_ner_enricher.py``.
"""

from __future__ import annotations

import builtins

import pytest

from app.dependencies import _build_enrichers
from app.services.enrichers import RuleBasedEntityEnricher, SpacyNerEnricher
from app.settings import Settings


def test_chain_does_not_include_ner_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KW_NER_ENABLED", raising=False)
    chain = _build_enrichers(Settings())
    assert any(isinstance(e, RuleBasedEntityEnricher) for e in chain)
    assert not any(isinstance(e, SpacyNerEnricher) for e in chain)


@pytest.mark.parametrize("falsy", ["", "0", "false", "no", "off", "  "])
def test_chain_omits_ner_for_falsy_flag_values(
    monkeypatch: pytest.MonkeyPatch,
    falsy: str,
) -> None:
    monkeypatch.setenv("KW_NER_ENABLED", falsy)
    chain = _build_enrichers(Settings())
    assert not any(isinstance(e, SpacyNerEnricher) for e in chain)


def test_chain_includes_ner_when_flag_truthy_and_extra_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the flag on and a fake spaCy import, the enricher joins the chain."""

    class _StubNlp:
        def __call__(self, text: str):
            class _Doc:
                ents: list = []

            return _Doc()

    class _SpacyStub:
        @staticmethod
        def load(model: str):
            return _StubNlp()

    real_import = builtins.__import__

    def _import_with_spacy_stub(name: str, *args, **kwargs):
        if name == "spacy":
            return _SpacyStub
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import_with_spacy_stub)
    monkeypatch.setenv("KW_NER_ENABLED", "true")
    chain = _build_enrichers(Settings())
    assert any(isinstance(e, SpacyNerEnricher) for e in chain)


def test_chain_raises_runtime_error_when_flag_on_but_extra_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Misconfiguration must fail loudly at startup, not silently no-op."""
    real_import = builtins.__import__

    def _no_spacy(name: str, *args, **kwargs):
        if name == "spacy" or name.startswith("spacy."):
            raise ImportError("No module named 'spacy'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_spacy)
    monkeypatch.setenv("KW_NER_ENABLED", "true")
    with pytest.raises(RuntimeError, match="ner"):
        _build_enrichers(Settings())
