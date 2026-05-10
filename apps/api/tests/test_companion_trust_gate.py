"""Trust-gate policy tests for the AURA companion (#372 / ADR-029).

The gate is a pure function — these tests don't need a route, an LLM,
or a database. They pin the four-quadrant policy:

      operator_strict=True   operator_strict=False
widen=False  | filter        | filter
widen=True   | filter        | pass-through

Plus the constants that the future ``POST /companion/answer`` route
will need to compose against (settings flag, error code).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.errors import ErrorCode
from app.services.companion.trust_gate import (
    apply_trust_gate,
    is_trusted,
)
from app.settings import Settings


@dataclass
class _FakeChunk:
    """Minimal trust-flag carrier — matches the ``HasTrustFlags``
    structural protocol the gate consumes."""

    validation_status: str | None
    is_source_backed: bool


def _validated() -> _FakeChunk:
    return _FakeChunk(validation_status="VALIDATED", is_source_backed=False)


def _source_backed() -> _FakeChunk:
    return _FakeChunk(validation_status=None, is_source_backed=True)


def _candidate() -> _FakeChunk:
    return _FakeChunk(validation_status=None, is_source_backed=False)


def _rejected() -> _FakeChunk:
    return _FakeChunk(validation_status="REJECTED", is_source_backed=False)


class TestIsTrusted:
    def test_validated_passes(self):
        assert is_trusted(_validated()) is True

    def test_source_backed_passes_even_without_validation(self):
        assert is_trusted(_source_backed()) is True

    def test_candidate_fails(self):
        assert is_trusted(_candidate()) is False

    def test_rejected_fails(self):
        assert is_trusted(_rejected()) is False


class TestApplyTrustGate:
    def test_default_deny_keeps_only_validated_or_source_backed(self):
        items = [_validated(), _source_backed(), _candidate(), _rejected()]
        out = apply_trust_gate(items)
        assert len(out.kept) == 2
        assert out.filtered_count == 2

    def test_default_deny_preserves_input_order(self):
        items = [_candidate(), _validated(), _candidate(), _source_backed()]
        out = apply_trust_gate(items)
        # Kept items keep their original relative order — ranking
        # semantics are preserved.
        assert out.kept == [items[1], items[3]]

    def test_widen_with_operator_unstrict_is_pass_through(self):
        items = [_validated(), _candidate(), _rejected()]
        out = apply_trust_gate(items, widen=True, operator_strict=False)
        assert out.kept == items
        assert out.filtered_count == 0

    def test_widen_is_ignored_when_operator_strict(self):
        """Regulated deployments lock the gate on regardless of UI toggle."""
        items = [_validated(), _candidate()]
        out = apply_trust_gate(items, widen=True, operator_strict=True)
        assert len(out.kept) == 1
        assert out.kept[0].validation_status == "VALIDATED"
        assert out.filtered_count == 1

    def test_widen_false_is_filter_regardless_of_operator(self):
        items = [_validated(), _candidate()]
        for strict in (True, False):
            out = apply_trust_gate(items, widen=False, operator_strict=strict)
            assert len(out.kept) == 1
            assert out.filtered_count == 1

    def test_empty_input_returns_empty_kept_and_zero_filtered(self):
        out = apply_trust_gate([])
        assert out.kept == []
        assert out.filtered_count == 0

    def test_all_filtered_signals_no_validated_knowledge_via_filtered_count(self):
        """When the gate filters every candidate, ``filtered_count`` is
        non-zero with ``kept`` empty — the route uses this to surface
        ``KW_COMPANION_NO_VALIDATED_KNOWLEDGE`` rather than fabricating."""
        items = [_candidate(), _candidate(), _rejected()]
        out = apply_trust_gate(items)
        assert out.kept == []
        assert out.filtered_count == 3


class TestSettingDefault:
    def test_companion_trust_gate_strict_defaults_to_true(self):
        """ADR-029: default-deny is the safe ship posture; loosening
        later is fine, tightening is a regression."""
        settings = Settings()
        assert settings.companion_trust_gate_strict is True

    def test_companion_trust_gate_strict_can_be_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("KW_COMPANION_TRUST_GATE_STRICT", "false")
        settings = Settings()
        assert settings.companion_trust_gate_strict is False


class TestErrorCodeRegistration:
    def test_no_validated_knowledge_error_code_is_registered(self):
        """ADR-029 binds the error envelope to a stable code so the
        companion frontend can switch on it (\"toggle to widen\" CTA)."""
        assert ErrorCode.COMPANION_NO_VALIDATED_KNOWLEDGE == "KW_COMPANION_NO_VALIDATED_KNOWLEDGE"
