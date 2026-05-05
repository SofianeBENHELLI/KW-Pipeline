"""Tests for the HITL router decision tree (ADR-023 §6, EPIC-A A.2, #215).

Table-driven over every branch in :class:`HITLRouter.decide`:

1. OCR override → human (reason ``ocr_override``).
2. force_auto_corpus → auto (reason ``force_auto``).
3. external_workflow_enabled → external (reason ``external_workflow``).
4a. score >= threshold + SPC sampled → human (reason ``spc_sampled``).
4b. score >= threshold + not sampled → auto (reason ``above_threshold``).
5. score < threshold → human (reason ``below_threshold``).

The router's only side effect is bumping the SPC counters; we assert
that bump on every branch via a fake :class:`SamplingStateStore`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from app.schemas.validation_metadata import (
    ConfidenceScore,
    RoutingDecision,
)
from app.services.confidence_scorer import ALL_SIGNALS
from app.services.hitl_router import HITLRouter
from app.services.sampling_state_store import (
    InMemorySamplingStateStore,
    SamplingBucket,
)


def _score(
    *,
    overall: float = 0.95,
    ocr_override: bool = False,
) -> ConfidenceScore:
    """Build a :class:`ConfidenceScore` with the requested overall.

    ``signals`` and ``weights`` are populated with the canonical 5
    keys so the schema is valid; the values are immaterial — the
    router only reads ``overall`` and ``ocr_override_active``.
    """
    return ConfidenceScore(
        overall=overall,
        signals=dict.fromkeys(ALL_SIGNALS, overall),
        weights=dict.fromkeys(ALL_SIGNALS, 0.2),
        ocr_override_active=ocr_override,
        computed_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        computed_by_version="v1",
    )


def _router(
    *,
    threshold: float = 0.85,
    force_auto_corpus: bool = False,
    external_workflow_enabled: bool = False,
    sampling_rate: float = 0.0,
    random_value: float = 0.99,
) -> tuple[HITLRouter, InMemorySamplingStateStore]:
    """Construct a router + its in-memory sampling store.

    ``random_value`` pins the rng so SPC sampling is deterministic in
    tests. ``sampling_rate=0.0`` (the default) short-circuits the rng
    entirely; tests that exercise the SPC branch override both.
    """
    sampling_state = InMemorySamplingStateStore()
    router = HITLRouter(
        sampling_state=sampling_state,
        threshold=threshold,
        force_auto_corpus=force_auto_corpus,
        external_workflow_enabled=external_workflow_enabled,
        sampling_rate=sampling_rate,
        random_fn=lambda: random_value,
    )
    return router, sampling_state


# ---------------------------------------------------------------------------
# Branch 1: OCR override beats every other rule.
# ---------------------------------------------------------------------------


def test_ocr_override_routes_to_human():
    router, _ = _router()
    decision = router.decide(
        score=_score(overall=0.0, ocr_override=True),
        content_type="application/pdf",
        topic_cluster="compliance",
    )
    assert decision.method == "human"
    assert decision.reason == "ocr_override"
    assert decision.score_overall == 0.0
    assert decision.threshold == 0.85
    assert decision.bucket == ("application/pdf", "compliance")


def test_ocr_override_beats_force_auto():
    """ADR-023 §6: OCR override is a hard stop, even when the admin
    force-auto override is set. OCR'd content is never trusted."""
    router, _ = _router(force_auto_corpus=True)
    decision = router.decide(
        score=_score(overall=0.99, ocr_override=True),
        content_type="application/pdf",
        topic_cluster=None,
    )
    assert decision.method == "human"
    assert decision.reason == "ocr_override"


def test_ocr_override_beats_external_workflow():
    router, _ = _router(external_workflow_enabled=True)
    decision = router.decide(
        score=_score(overall=0.5, ocr_override=True),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "human"
    assert decision.reason == "ocr_override"


# ---------------------------------------------------------------------------
# Branch 2: force_auto_corpus admin override.
# ---------------------------------------------------------------------------


def test_force_auto_corpus_routes_low_score_to_auto():
    """ADR-023 §6 admin mode: every non-OCR version goes auto, even
    one that would otherwise route human on score."""
    router, _ = _router(force_auto_corpus=True)
    decision = router.decide(
        score=_score(overall=0.0),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "auto"
    assert decision.reason == "force_auto"


def test_force_auto_corpus_routes_high_score_to_auto_without_spc():
    """SPC sampling does not fire on the force-auto path — the
    operator's intent is "trust everything", and a probabilistic
    escalation would be confusing."""
    router, _ = _router(
        force_auto_corpus=True,
        sampling_rate=1.0,  # would normally always escalate
        random_value=0.0,
    )
    decision = router.decide(
        score=_score(overall=0.95),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "auto"
    assert decision.reason == "force_auto"


def test_force_auto_corpus_logs_warning_at_construction(caplog):
    """ADR-023 §6: the override should be loud at boot so accidental
    production usage is visible from a single grep."""
    with caplog.at_level(logging.WARNING):
        HITLRouter(
            sampling_state=InMemorySamplingStateStore(),
            threshold=0.85,
            force_auto_corpus=True,
            external_workflow_enabled=False,
            sampling_rate=0.05,
        )
    assert any(record.message == "hitl.force_auto_corpus_active" for record in caplog.records)


# ---------------------------------------------------------------------------
# Branch 3: external workflow placeholder.
# ---------------------------------------------------------------------------


def test_external_workflow_routes_to_external():
    """EPIC-B placeholder. The wiring layer hard-wires
    external_workflow_enabled=False today; tests still pin the
    behaviour so it stays correct when EPIC-B lights it up."""
    router, _ = _router(external_workflow_enabled=True)
    decision = router.decide(
        score=_score(overall=0.99),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "external"
    assert decision.reason == "external_workflow"


def test_external_workflow_skipped_when_disabled():
    router, _ = _router(external_workflow_enabled=False)
    decision = router.decide(
        score=_score(overall=0.99),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "auto"
    assert decision.reason == "above_threshold"


# ---------------------------------------------------------------------------
# Branches 4a / 4b: threshold + SPC sampling.
# ---------------------------------------------------------------------------


def test_score_above_threshold_routes_auto_when_not_sampled():
    """random_value (0.99) >= sampling_rate (0.05): no escalation."""
    router, _ = _router(threshold=0.85, sampling_rate=0.05, random_value=0.99)
    decision = router.decide(
        score=_score(overall=0.9),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "auto"
    assert decision.reason == "above_threshold"


def test_score_above_threshold_routes_human_when_spc_sampled():
    """random_value (0.0) < sampling_rate (0.05): escalate to human."""
    router, _ = _router(threshold=0.85, sampling_rate=0.05, random_value=0.0)
    decision = router.decide(
        score=_score(overall=0.95),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "human"
    assert decision.reason == "spc_sampled"


def test_score_exactly_at_threshold_is_eligible_for_auto():
    """``>= threshold`` is the contract — the boundary value passes."""
    router, _ = _router(threshold=0.85)
    decision = router.decide(
        score=_score(overall=0.85),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "auto"


def test_score_just_below_threshold_routes_human():
    """``< threshold`` lands on the below-threshold branch."""
    router, _ = _router(threshold=0.85)
    decision = router.decide(
        score=_score(overall=0.8499),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "human"
    assert decision.reason == "below_threshold"


def test_zero_sampling_rate_never_escalates():
    """ADR-023 §6: rate=0.0 short-circuits the rng. random_fn is
    never called, so even a pathological random_value can't escalate."""
    rng_calls: list[None] = []

    def _rng() -> float:
        rng_calls.append(None)
        return 0.0

    router = HITLRouter(
        sampling_state=InMemorySamplingStateStore(),
        threshold=0.85,
        force_auto_corpus=False,
        external_workflow_enabled=False,
        sampling_rate=0.0,
        random_fn=_rng,
    )
    decision = router.decide(
        score=_score(overall=0.95),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "auto"
    assert decision.reason == "above_threshold"
    assert rng_calls == []


def test_sampling_rate_one_always_escalates():
    """rate=1.0 makes every above-threshold version go human."""
    router, _ = _router(threshold=0.85, sampling_rate=1.0, random_value=0.999)
    decision = router.decide(
        score=_score(overall=0.95),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "human"
    assert decision.reason == "spc_sampled"


# ---------------------------------------------------------------------------
# Branch 5: below threshold → human.
# ---------------------------------------------------------------------------


def test_score_below_threshold_routes_human():
    router, _ = _router(threshold=0.85)
    decision = router.decide(
        score=_score(overall=0.5),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "human"
    assert decision.reason == "below_threshold"


def test_zero_score_routes_human():
    router, _ = _router(threshold=0.85)
    decision = router.decide(
        score=_score(overall=0.0),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "human"
    assert decision.reason == "below_threshold"


# ---------------------------------------------------------------------------
# SPC counter side-effects + bucket axis.
# ---------------------------------------------------------------------------


def test_decision_bumps_spc_counter():
    """Every router decision bumps the SPC ``samples_taken`` counter
    for the bucket the decision targets."""
    router, sampling = _router()
    router.decide(
        score=_score(overall=0.9),
        content_type="text/plain",
        topic_cluster="compliance",
    )
    counters = sampling.read_counters(
        bucket=SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    )
    assert counters.samples_taken == 1
    assert counters.samples_auto == 1


def test_missing_topic_cluster_uses_unknown_sentinel():
    """A ``None`` topic cluster lands the counter under the
    canonical ``"_unknown_"`` sentinel — same axis the SPC sampler
    keys on consistently across the codebase."""
    router, sampling = _router()
    decision = router.decide(
        score=_score(overall=0.5),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.bucket == ("text/plain", "_unknown_")
    counters = sampling.read_counters(
        bucket=SamplingBucket(content_type="text/plain", topic_cluster="_unknown_")
    )
    assert counters.samples_taken == 1


def test_decision_payload_round_trips_through_pydantic():
    """:class:`RoutingDecision` is a Pydantic model — assert the
    serialisation contract so audit tooling can rebuild it from
    a JSON column."""
    router, _ = _router()
    decision = router.decide(
        score=_score(overall=0.95),
        content_type="text/plain",
        topic_cluster="compliance",
    )
    payload = decision.model_dump()
    assert payload["method"] == "auto"
    assert payload["reason"] == "above_threshold"
    rebuilt = RoutingDecision.model_validate(payload)
    assert rebuilt == decision


# ---------------------------------------------------------------------------
# Constructor validation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("threshold", [-0.1, 1.01, float("nan")])
def test_threshold_out_of_range_raises(threshold: float) -> None:
    with pytest.raises(ValueError, match="threshold"):
        HITLRouter(
            sampling_state=InMemorySamplingStateStore(),
            threshold=threshold,
            force_auto_corpus=False,
            external_workflow_enabled=False,
            sampling_rate=0.05,
        )


@pytest.mark.parametrize("rate", [-0.1, 1.01, float("nan")])
def test_sampling_rate_out_of_range_raises(rate: float) -> None:
    with pytest.raises(ValueError, match="sampling_rate"):
        HITLRouter(
            sampling_state=InMemorySamplingStateStore(),
            threshold=0.85,
            force_auto_corpus=False,
            external_workflow_enabled=False,
            sampling_rate=rate,
        )


def test_router_exposes_threshold_and_sampling_rate():
    """The router's public properties echo the configured values so
    operators / tests can introspect without re-reading settings."""
    router, _ = _router(threshold=0.7, sampling_rate=0.1, random_value=0.5)
    assert router.threshold == pytest.approx(0.7)
    assert router.sampling_rate == pytest.approx(0.1)
