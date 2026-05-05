"""SPC sampling-counter + drift-detector invariants (EPIC-A A.7, #215).

These are property-style tests run as plain pytest parametrisations
(``hypothesis`` is not in the api project's deps and adding it for
five invariants is overkill — the corpus of cases below covers the
edge space the ratio formula reaches in practice). The invariants
correspond to the "load-bearing" properties of the SPC counter +
drift formula:

1. ``samples_taken == samples_auto + samples_human`` — the router
   only ever picks one of those two methods (external is dead). The
   total = sum of the two parts is the conservation law.
2. ``samples_human_after_auto <= samples_human`` — drift is a strict
   subset of the human-review path.
3. ``0.0 <= sampling_rate(bucket) <= 1.0`` — the rate the detector
   returns is a probability.
4. ``sampling_rate(bucket) == baseline`` when ``samples_auto == 0`` —
   cold-start.
5. Above the drift threshold:
   ``sampling_rate(bucket) == min(1.0, baseline * ramp_factor)``.
6. After arbitrary ``record_decision`` + ``record_drift_event``
   sequences, counters reconcile (no decrements, no double-counts).
"""

from __future__ import annotations

import pytest

from app.schemas.validation_metadata import RoutingMethod
from app.services.hitl_drift_detector import HITLDriftDetector
from app.services.sampling_state_store import (
    InMemorySamplingStateStore,
    SamplingBucket,
)

# Edge-case + nominal sequences. Each is a list of "events" the
# router/promoter/rejection-handler would replay against the store.
# An "auto" / "human" event is a router decision; a "drift" event
# is a rejection-handler bump (samples_human_after_auto).
_EVENT_SEQUENCES: list[tuple[str, list[str]]] = [
    ("empty", []),
    ("single_auto", ["auto"]),
    ("single_human", ["human"]),
    ("single_drift", ["drift"]),
    ("auto_then_drift", ["auto", "drift"]),
    ("human_then_drift", ["human", "drift"]),
    ("balanced_5_5", ["auto"] * 5 + ["human"] * 5),
    ("auto_heavy", ["auto"] * 100 + ["human"] * 5),
    ("human_heavy", ["auto"] * 5 + ["human"] * 100),
    ("drift_below_threshold", ["auto"] * 100 + ["drift"] * 5),
    ("drift_at_threshold", ["auto"] * 100 + ["drift"] * 10),
    ("drift_above_threshold", ["auto"] * 100 + ["drift"] * 25),
    ("drift_perfect_ratio", ["auto"] * 10 + ["drift"] * 10),
    (
        "interleaved",
        ["auto", "human", "auto", "human", "drift", "auto", "human", "drift"],
    ),
]


def _replay(store: InMemorySamplingStateStore, bucket: SamplingBucket, events: list[str]) -> None:
    for event in events:
        if event == "auto":
            store.record_decision(bucket=bucket, method="auto")
        elif event == "human":
            store.record_decision(bucket=bucket, method="human")
        elif event == "drift":
            store.record_drift_event(bucket=bucket)
        else:  # pragma: no cover - test typo
            raise AssertionError(f"unknown event {event!r}")


# ---------------------------------------------------------------------------
# Invariant 1: samples_taken == samples_auto + samples_human (no external).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "events"), _EVENT_SEQUENCES)
def test_invariant_total_equals_sum_of_methods(name: str, events: list[str]) -> None:
    del name
    store = InMemorySamplingStateStore()
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    _replay(store, bucket, events)
    counters = store.read_counters(bucket=bucket)
    assert counters.samples_taken == counters.samples_auto + counters.samples_human


# ---------------------------------------------------------------------------
# Invariant 2: samples_human_after_auto <= samples_human is the
# load-bearing relation when drift events are recorded only on
# human reviews. The store doesn't enforce this — the rejection
# handler does (only fires on a human review, after a routing.decided
# auto). We pin the contract by gating drift events on a human
# review having already been recorded for this bucket.
# ---------------------------------------------------------------------------


def _gated_drift_replay(events: list[str]) -> InMemorySamplingStateStore:
    """Replay events but drop drift events that aren't preceded by a
    human review — mirrors the rejection handler's contract."""
    store = InMemorySamplingStateStore()
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    human_count = 0
    drift_count = 0
    for event in events:
        if event == "auto":
            store.record_decision(bucket=bucket, method="auto")
        elif event == "human":
            store.record_decision(bucket=bucket, method="human")
            human_count += 1
        elif event == "drift" and drift_count < human_count:
            store.record_drift_event(bucket=bucket)
            drift_count += 1
    return store


@pytest.mark.parametrize(("name", "events"), _EVENT_SEQUENCES)
def test_invariant_drift_subset_of_human(name: str, events: list[str]) -> None:
    del name
    store = _gated_drift_replay(events)
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    counters = store.read_counters(bucket=bucket)
    assert counters.samples_human_after_auto <= counters.samples_human


# ---------------------------------------------------------------------------
# Invariant 3: 0.0 <= sampling_rate(bucket) <= 1.0 always.
# ---------------------------------------------------------------------------


_DETECTOR_PARAMS: list[tuple[float, float, float]] = [
    (0.05, 0.10, 10.0),  # default config
    (0.0, 0.10, 10.0),  # rate fully off
    (1.0, 0.10, 10.0),  # rate fully on
    (0.5, 0.10, 10.0),  # ramp would saturate
    (0.05, 0.0, 10.0),  # any drift triggers ramp
    (0.05, 0.10, 0.0),  # ramp factor zero
    (0.05, 0.10, 1.0),  # ramp factor identity
    (0.05, 0.99, 1000.0),  # extreme threshold
]


@pytest.mark.parametrize(("name", "events"), _EVENT_SEQUENCES)
@pytest.mark.parametrize(("baseline", "threshold", "ramp"), _DETECTOR_PARAMS)
def test_invariant_sampling_rate_in_unit_interval(
    name: str,
    events: list[str],
    baseline: float,
    threshold: float,
    ramp: float,
) -> None:
    del name
    store = InMemorySamplingStateStore()
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    _replay(store, bucket, events)
    detector = HITLDriftDetector(
        sampling_state=store,
        baseline_rate=baseline,
        drift_threshold=threshold,
        ramp_factor=ramp,
    )
    rate = detector.sampling_rate(("text/plain", "compliance"))
    assert 0.0 <= rate <= 1.0


# ---------------------------------------------------------------------------
# Invariant 4: cold-start returns baseline.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("baseline", "threshold", "ramp"), _DETECTOR_PARAMS)
def test_invariant_cold_start_returns_baseline(
    baseline: float, threshold: float, ramp: float
) -> None:
    store = InMemorySamplingStateStore()
    detector = HITLDriftDetector(
        sampling_state=store,
        baseline_rate=baseline,
        drift_threshold=threshold,
        ramp_factor=ramp,
    )
    rate = detector.sampling_rate(("text/plain", "any"))
    assert rate == pytest.approx(baseline)


def test_invariant_cold_start_holds_after_human_only_decisions() -> None:
    """``samples_auto == 0`` even if humans have been routed → still
    cold-start. This pins the "denominator is auto, not taken" rule."""
    store = InMemorySamplingStateStore()
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    for _ in range(50):
        store.record_decision(bucket=bucket, method="human")
    detector = HITLDriftDetector(
        sampling_state=store,
        baseline_rate=0.05,
        drift_threshold=0.10,
        ramp_factor=10.0,
    )
    assert detector.sampling_rate(("text/plain", "compliance")) == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Invariant 5: above-threshold ramp formula.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("baseline", "ramp"),
    [
        (0.05, 10.0),
        (0.1, 5.0),
        (0.01, 100.0),  # saturates at 1.0
        (0.5, 10.0),  # saturates at 1.0
        (0.5, 0.5),  # ramp_factor < 1, ratio still > threshold
    ],
)
def test_invariant_ramp_formula_above_threshold(baseline: float, ramp: float) -> None:
    store = InMemorySamplingStateStore()
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    # Force the ratio above any reasonable threshold (1:1).
    for _ in range(10):
        store.record_decision(bucket=bucket, method="auto")
    for _ in range(10):
        store.record_drift_event(bucket=bucket)
    detector = HITLDriftDetector(
        sampling_state=store,
        baseline_rate=baseline,
        drift_threshold=0.10,
        ramp_factor=ramp,
    )
    expected = min(1.0, baseline * ramp)
    assert detector.sampling_rate(("text/plain", "compliance")) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Invariant 6: counters reconcile under arbitrary event interleaving.
# Monotonic — no decrements, no double-counts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "events"), _EVENT_SEQUENCES)
def test_invariant_counters_monotonic(name: str, events: list[str]) -> None:
    """After every prefix of the sequence, every counter is >= its
    previous value. No decrements, no resets."""
    del name
    store = InMemorySamplingStateStore()
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")

    prev_taken = 0
    prev_auto = 0
    prev_human = 0
    prev_drift = 0
    for event in events:
        if event == "auto":
            store.record_decision(bucket=bucket, method="auto")
        elif event == "human":
            store.record_decision(bucket=bucket, method="human")
        elif event == "drift":
            store.record_drift_event(bucket=bucket)
        counters = store.read_counters(bucket=bucket)
        assert counters.samples_taken >= prev_taken
        assert counters.samples_auto >= prev_auto
        assert counters.samples_human >= prev_human
        assert counters.samples_human_after_auto >= prev_drift
        prev_taken = counters.samples_taken
        prev_auto = counters.samples_auto
        prev_human = counters.samples_human
        prev_drift = counters.samples_human_after_auto


def test_invariant_counters_reconcile_per_event_type() -> None:
    """The per-method counter increments by exactly 1 per
    record_decision call, and samples_taken increments by exactly 1
    too. samples_human_after_auto increments by exactly 1 per
    record_drift_event call."""
    store = InMemorySamplingStateStore()
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="compliance")

    expected_auto = 0
    expected_human = 0
    expected_drift = 0
    methods: list[RoutingMethod] = ["auto", "human", "auto", "human", "auto"]
    for method in methods:
        store.record_decision(bucket=bucket, method=method)
        if method == "auto":
            expected_auto += 1
        else:
            expected_human += 1
        counters = store.read_counters(bucket=bucket)
        assert counters.samples_auto == expected_auto
        assert counters.samples_human == expected_human
        assert counters.samples_taken == expected_auto + expected_human

    for i in range(3):
        store.record_drift_event(bucket=bucket)
        expected_drift = i + 1
        counters = store.read_counters(bucket=bucket)
        assert counters.samples_human_after_auto == expected_drift
        # Drift events do NOT bump samples_taken.
        assert counters.samples_taken == expected_auto + expected_human


# ---------------------------------------------------------------------------
# Invariant: per-bucket isolation. Counter mutations on bucket A must
# not affect bucket B's counters or sampling rate.
# ---------------------------------------------------------------------------


def test_invariant_bucket_isolation() -> None:
    store = InMemorySamplingStateStore()
    a = SamplingBucket(content_type="text/plain", topic_cluster="compliance")
    b = SamplingBucket(content_type="application/pdf", topic_cluster="legal")
    for _ in range(10):
        store.record_decision(bucket=a, method="auto")
    for _ in range(5):
        store.record_drift_event(bucket=a)

    counters_b = store.read_counters(bucket=b)
    assert counters_b.samples_taken == 0
    assert counters_b.samples_auto == 0
    assert counters_b.samples_human == 0
    assert counters_b.samples_human_after_auto == 0

    detector = HITLDriftDetector(
        sampling_state=store,
        baseline_rate=0.05,
        drift_threshold=0.10,
        ramp_factor=10.0,
    )
    assert detector.sampling_rate(
        (b.content_type, b.topic_cluster),
    ) == pytest.approx(0.05)
