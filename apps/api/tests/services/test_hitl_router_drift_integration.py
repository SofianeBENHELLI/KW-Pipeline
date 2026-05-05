"""Router × drift-detector integration tests (EPIC-A A.3 part 2, #215).

Pins the wiring between :class:`HITLRouter` and
:class:`HITLDriftDetector`: the router asks the detector for a
per-bucket rate when a ``SamplingRateFn`` is wired, and falls back
to the constant ``sampling_rate`` constructor argument when it isn't.

The detector is stubbed via a plain callable so the rate logic is
isolated from the SamplingStateStore — tests in
:mod:`tests.services.test_hitl_drift_detector` cover the ratio +
ramp formula directly.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.schemas.validation_metadata import ConfidenceScore
from app.services.confidence_scorer import ALL_SIGNALS
from app.services.hitl_router import HITLRouter
from app.services.sampling_state_store import InMemorySamplingStateStore


def _score(*, overall: float = 0.95, ocr_override: bool = False) -> ConfidenceScore:
    return ConfidenceScore(
        overall=overall,
        signals=dict.fromkeys(ALL_SIGNALS, overall),
        weights=dict.fromkeys(ALL_SIGNALS, 0.2),
        ocr_override_active=ocr_override,
        computed_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        computed_by_version="v1",
    )


def test_router_uses_drift_detector_for_per_bucket_rate():
    """When wired, the router calls the detector once per decide()
    and uses the returned rate for the SPC roll."""
    calls: list[tuple[str, str]] = []

    def detector(bucket: tuple[str, str]) -> float:
        calls.append(bucket)
        # Return 1.0 so SPC sampling fires every time the router
        # consults the detector — that lets us assert the path
        # without flaky rng pinning.
        return 1.0

    router = HITLRouter(
        sampling_state=InMemorySamplingStateStore(),
        threshold=0.85,
        force_auto_corpus=False,
        external_workflow_enabled=False,
        sampling_rate=0.0,  # would normally never escalate
        drift_detector=detector,
        random_fn=lambda: 0.5,
    )
    decision = router.decide(
        score=_score(overall=0.95),
        content_type="text/plain",
        topic_cluster="compliance",
    )
    assert decision.method == "human"
    assert decision.reason == "spc_sampled"
    assert calls == [("text/plain", "compliance")]


def test_router_falls_back_to_constant_when_no_detector():
    """Backward-compat: ``drift_detector=None`` keeps the constant
    ``sampling_rate`` posture so existing tests don't churn."""
    router = HITLRouter(
        sampling_state=InMemorySamplingStateStore(),
        threshold=0.85,
        force_auto_corpus=False,
        external_workflow_enabled=False,
        sampling_rate=1.0,  # always escalate
        random_fn=lambda: 0.0,
    )
    decision = router.decide(
        score=_score(overall=0.95),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.method == "human"
    assert decision.reason == "spc_sampled"


def test_drift_detector_per_bucket_rate_drives_decision():
    """A bucket the detector says is drifting escalates more often
    than a clean sibling — the per-bucket isolation contract."""
    rates = {
        ("text/plain", "drifting"): 1.0,
        ("text/plain", "clean"): 0.0,
    }
    router = HITLRouter(
        sampling_state=InMemorySamplingStateStore(),
        threshold=0.85,
        force_auto_corpus=False,
        external_workflow_enabled=False,
        sampling_rate=0.5,
        drift_detector=lambda bucket: rates[bucket],
        random_fn=lambda: 0.99,  # rng draw rarely escalates
    )

    drifting = router.decide(
        score=_score(overall=0.95),
        content_type="text/plain",
        topic_cluster="drifting",
    )
    clean = router.decide(
        score=_score(overall=0.95),
        content_type="text/plain",
        topic_cluster="clean",
    )
    assert drifting.method == "human"
    assert drifting.reason == "spc_sampled"
    assert clean.method == "auto"
    assert clean.reason == "above_threshold"


def test_drift_detector_failure_falls_back_to_constant(caplog):
    """A detector exception is treated as "stay at baseline" — the
    HITL pipeline's fire-and-log discipline (ADR-012 §3) applies."""

    def angry_detector(_bucket: tuple[str, str]) -> float:
        raise RuntimeError("sampling state outage")

    router = HITLRouter(
        sampling_state=InMemorySamplingStateStore(),
        threshold=0.85,
        force_auto_corpus=False,
        external_workflow_enabled=False,
        sampling_rate=0.0,  # constant fallback never escalates
        drift_detector=angry_detector,
        random_fn=lambda: 0.0,
    )
    with caplog.at_level(logging.ERROR):
        decision = router.decide(
            score=_score(overall=0.95),
            content_type="text/plain",
            topic_cluster=None,
        )
    assert decision.method == "auto"
    assert decision.reason == "above_threshold"
    assert any(record.message == "hitl.drift_detector.read_failed" for record in caplog.records)


def test_drift_detector_clamps_out_of_range_rates():
    """A misbehaving detector returning <0 or >1 is clamped to the
    contracted range so the rng draw stays well-formed."""
    # Detector returns 1.5 → clamped to 1.0 → always escalate.
    router = HITLRouter(
        sampling_state=InMemorySamplingStateStore(),
        threshold=0.85,
        force_auto_corpus=False,
        external_workflow_enabled=False,
        sampling_rate=0.0,
        drift_detector=lambda _bucket: 1.5,
        random_fn=lambda: 0.99,
    )
    decision = router.decide(
        score=_score(overall=0.95),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.reason == "spc_sampled"

    # Detector returns -0.5 → clamped to 0.0 → never escalate.
    router_neg = HITLRouter(
        sampling_state=InMemorySamplingStateStore(),
        threshold=0.85,
        force_auto_corpus=False,
        external_workflow_enabled=False,
        sampling_rate=1.0,
        drift_detector=lambda _bucket: -0.5,
        random_fn=lambda: 0.0,
    )
    decision_neg = router_neg.decide(
        score=_score(overall=0.95),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision_neg.reason == "above_threshold"


def test_drift_detector_skipped_for_force_auto_branch():
    """Force-auto admin override bypasses the SPC sampler entirely —
    the detector should not be consulted on that path."""
    calls: list[tuple[str, str]] = []

    def detector(bucket: tuple[str, str]) -> float:
        calls.append(bucket)
        return 1.0

    router = HITLRouter(
        sampling_state=InMemorySamplingStateStore(),
        threshold=0.85,
        force_auto_corpus=True,
        external_workflow_enabled=False,
        sampling_rate=0.0,
        drift_detector=detector,
    )
    decision = router.decide(
        score=_score(overall=0.5),
        content_type="text/plain",
        topic_cluster=None,
    )
    assert decision.reason == "force_auto"
    assert calls == []
