"""Tests for the HITL drift detector (ADR-023 §6, EPIC-A A.3 part 2, #215).

Table-driven over the ratio + ramp formula:

- Cold start: ``samples_auto == 0`` returns the baseline rate.
- Below or at threshold: returns the baseline rate.
- Above threshold: returns ``min(1.0, baseline * ramp_factor)``.
- Cap at 1.0 when the ramp would exceed it.
- Per-bucket isolation: a drifting bucket does not lift the rate of
  a clean sibling bucket.
"""

from __future__ import annotations

import pytest

from app.services.hitl_drift_detector import HITLDriftDetector
from app.services.sampling_state_store import (
    InMemorySamplingStateStore,
    SamplingBucket,
)


def _detector(
    *,
    baseline_rate: float = 0.05,
    drift_threshold: float = 0.10,
    ramp_factor: float = 10.0,
) -> tuple[HITLDriftDetector, InMemorySamplingStateStore]:
    """Build a detector + the in-memory store it reads from."""
    sampling_state = InMemorySamplingStateStore()
    detector = HITLDriftDetector(
        sampling_state=sampling_state,
        baseline_rate=baseline_rate,
        drift_threshold=drift_threshold,
        ramp_factor=ramp_factor,
    )
    return detector, sampling_state


# ---------------------------------------------------------------------------
# Cold-start: no auto samples yet → baseline.
# ---------------------------------------------------------------------------


def test_cold_start_returns_baseline():
    detector, _ = _detector(baseline_rate=0.05)
    assert detector.sampling_rate(("text/plain", "compliance")) == pytest.approx(0.05)


def test_cold_start_with_drift_event_only_still_baseline():
    """``samples_human_after_auto > 0`` but ``samples_auto == 0`` is
    a degenerate state — denominator is undefined. Hold at baseline."""
    detector, sampling = _detector(baseline_rate=0.05)
    sampling.record_drift_event(
        bucket=SamplingBucket(content_type="text/plain", topic_cluster="compliance"),
    )
    assert detector.sampling_rate(("text/plain", "compliance")) == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Below or at threshold: baseline.
# ---------------------------------------------------------------------------


def test_below_threshold_returns_baseline():
    detector, sampling = _detector(baseline_rate=0.05, drift_threshold=0.10)
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    # 100 auto samples, 5 drift events → ratio = 0.05 < 0.10
    for _ in range(100):
        sampling.record_decision(bucket=bucket, method="auto")
    for _ in range(5):
        sampling.record_drift_event(bucket=bucket)
    assert detector.sampling_rate(("text/plain", "compliance")) == pytest.approx(0.05)


def test_at_threshold_exactly_returns_baseline():
    """``ratio <= drift_threshold`` is the at-or-below contract — the
    boundary value does NOT trigger the ramp. We pick 10 / 100 = 0.10
    against threshold 0.10."""
    detector, sampling = _detector(baseline_rate=0.05, drift_threshold=0.10)
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    for _ in range(100):
        sampling.record_decision(bucket=bucket, method="auto")
    for _ in range(10):
        sampling.record_drift_event(bucket=bucket)
    assert detector.sampling_rate(("text/plain", "compliance")) == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Above threshold: ramp.
# ---------------------------------------------------------------------------


def test_above_threshold_ramps():
    detector, sampling = _detector(baseline_rate=0.05, drift_threshold=0.10, ramp_factor=10.0)
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    # 100 auto, 11 drift → ratio 0.11 > threshold 0.10
    for _ in range(100):
        sampling.record_decision(bucket=bucket, method="auto")
    for _ in range(11):
        sampling.record_drift_event(bucket=bucket)
    assert detector.sampling_rate(("text/plain", "compliance")) == pytest.approx(0.5)


def test_ramp_caps_at_one():
    """When ``baseline * ramp_factor`` exceeds 1.0, clamp to 1.0."""
    detector, sampling = _detector(baseline_rate=0.5, drift_threshold=0.10, ramp_factor=10.0)
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    for _ in range(100):
        sampling.record_decision(bucket=bucket, method="auto")
    for _ in range(50):
        sampling.record_drift_event(bucket=bucket)
    assert detector.sampling_rate(("text/plain", "compliance")) == pytest.approx(1.0)


def test_ramp_factor_one_is_noop():
    """``ramp_factor == 1.0`` keeps the rate at baseline even above
    the threshold — a sanity check that the formula is multiplicative
    not additive."""
    detector, sampling = _detector(baseline_rate=0.05, drift_threshold=0.10, ramp_factor=1.0)
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    for _ in range(100):
        sampling.record_decision(bucket=bucket, method="auto")
    for _ in range(50):
        sampling.record_drift_event(bucket=bucket)
    assert detector.sampling_rate(("text/plain", "compliance")) == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Per-bucket isolation.
# ---------------------------------------------------------------------------


def test_drift_in_one_bucket_does_not_affect_sibling():
    detector, sampling = _detector()
    drifting = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    clean = SamplingBucket(content_type="application/pdf", topic_cluster="legal")
    for _ in range(100):
        sampling.record_decision(bucket=drifting, method="auto")
        sampling.record_decision(bucket=clean, method="auto")
    for _ in range(50):
        sampling.record_drift_event(bucket=drifting)

    assert detector.sampling_rate(("text/plain", "compliance")) == pytest.approx(0.5)
    assert detector.sampling_rate(("application/pdf", "legal")) == pytest.approx(0.05)


def test_unknown_topic_cluster_sentinel_round_trips():
    """``"_unknown_"`` is just another bucket — nothing special-cases it."""
    detector, sampling = _detector()
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="_unknown_")
    for _ in range(100):
        sampling.record_decision(bucket=bucket, method="auto")
    for _ in range(50):
        sampling.record_drift_event(bucket=bucket)
    assert detector.sampling_rate(("text/plain", "_unknown_")) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Constructor validation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rate", [-0.1, 1.01, float("nan")])
def test_baseline_rate_out_of_range_raises(rate: float) -> None:
    with pytest.raises(ValueError, match="baseline_rate"):
        HITLDriftDetector(
            sampling_state=InMemorySamplingStateStore(),
            baseline_rate=rate,
            drift_threshold=0.10,
            ramp_factor=10.0,
        )


@pytest.mark.parametrize("threshold", [-0.1, float("nan")])
def test_drift_threshold_out_of_range_raises(threshold: float) -> None:
    with pytest.raises(ValueError, match="drift_threshold"):
        HITLDriftDetector(
            sampling_state=InMemorySamplingStateStore(),
            baseline_rate=0.05,
            drift_threshold=threshold,
            ramp_factor=10.0,
        )


@pytest.mark.parametrize("factor", [-0.1, float("nan")])
def test_ramp_factor_out_of_range_raises(factor: float) -> None:
    with pytest.raises(ValueError, match="ramp_factor"):
        HITLDriftDetector(
            sampling_state=InMemorySamplingStateStore(),
            baseline_rate=0.05,
            drift_threshold=0.10,
            ramp_factor=factor,
        )


# ---------------------------------------------------------------------------
# Properties (introspection).
# ---------------------------------------------------------------------------


def test_properties_round_trip():
    detector, _ = _detector(baseline_rate=0.07, drift_threshold=0.15, ramp_factor=4.0)
    assert detector.baseline_rate == pytest.approx(0.07)
    assert detector.drift_threshold == pytest.approx(0.15)
    assert detector.ramp_factor == pytest.approx(4.0)
