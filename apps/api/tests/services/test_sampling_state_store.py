"""Tests for the ``SamplingStateStore`` (ADR-023 §6, EPIC-A A.3, #215).

Parametrised across the in-memory and SQLite implementations so the
two surfaces stay byte-equivalent. The SQLite path runs migration
0009 against a fresh tmp database so the test suite catches a drift
between :class:`SQLiteSamplingStateStore` and the migration.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from app.services.migrations import _run_migrations
from app.services.sampling_state_store import (
    UNKNOWN_TOPIC_CLUSTER,
    InMemorySamplingStateStore,
    SamplingBucket,
    SamplingCounters,
    SamplingStateStore,
    SQLiteSamplingStateStore,
)


def _seed_sqlite_schema(db_path: Path) -> None:
    """Run the migration suite against a fresh tmp database."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        _run_migrations(conn)
    finally:
        conn.close()


@pytest.fixture(
    params=["in-memory", "sqlite"],
    ids=["in-memory", "sqlite"],
)
def store_factory(request, tmp_path) -> Callable[[], SamplingStateStore]:
    if request.param == "in-memory":
        return InMemorySamplingStateStore
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    return lambda: SQLiteSamplingStateStore(db_path)


def _bucket(content_type: str = "text/plain", topic_cluster: str = "compliance") -> SamplingBucket:
    return SamplingBucket(content_type=content_type, topic_cluster=topic_cluster)


def test_read_unknown_bucket_returns_zeroed_counters(store_factory):
    store = store_factory()
    counters = store.read_counters(bucket=_bucket())
    assert counters == SamplingCounters()


def test_record_auto_decision_increments_counters(store_factory):
    store = store_factory()
    store.record_decision(bucket=_bucket(), method="auto")
    counters = store.read_counters(bucket=_bucket())
    assert counters.samples_taken == 1
    assert counters.samples_auto == 1
    assert counters.samples_human == 0
    assert counters.samples_human_after_auto == 0
    assert counters.last_decision_at is not None


def test_record_human_decision_increments_counters(store_factory):
    store = store_factory()
    store.record_decision(bucket=_bucket(), method="human")
    counters = store.read_counters(bucket=_bucket())
    assert counters.samples_taken == 1
    assert counters.samples_auto == 0
    assert counters.samples_human == 1


def test_record_external_decision_only_bumps_taken(store_factory):
    """External routing increments ``samples_taken`` only — the
    dedicated column is deferred until EPIC-B lights the branch up.
    """
    store = store_factory()
    store.record_decision(bucket=_bucket(), method="external")
    counters = store.read_counters(bucket=_bucket())
    assert counters.samples_taken == 1
    assert counters.samples_auto == 0
    assert counters.samples_human == 0


def test_multiple_decisions_accumulate(store_factory):
    store = store_factory()
    store.record_decision(bucket=_bucket(), method="auto")
    store.record_decision(bucket=_bucket(), method="auto")
    store.record_decision(bucket=_bucket(), method="human")
    counters = store.read_counters(bucket=_bucket())
    assert counters.samples_taken == 3
    assert counters.samples_auto == 2
    assert counters.samples_human == 1


def test_buckets_are_isolated(store_factory):
    store = store_factory()
    store.record_decision(
        bucket=_bucket(content_type="text/plain"),
        method="auto",
    )
    store.record_decision(
        bucket=_bucket(content_type="application/pdf"),
        method="human",
    )
    plain = store.read_counters(bucket=_bucket(content_type="text/plain"))
    pdf = store.read_counters(bucket=_bucket(content_type="application/pdf"))
    assert plain.samples_auto == 1
    assert plain.samples_human == 0
    assert pdf.samples_auto == 0
    assert pdf.samples_human == 1


def test_topic_cluster_dimension_isolates_counters(store_factory):
    store = store_factory()
    store.record_decision(
        bucket=_bucket(topic_cluster="compliance"),
        method="auto",
    )
    store.record_decision(
        bucket=_bucket(topic_cluster="engineering"),
        method="auto",
    )
    compliance = store.read_counters(bucket=_bucket(topic_cluster="compliance"))
    engineering = store.read_counters(bucket=_bucket(topic_cluster="engineering"))
    assert compliance.samples_taken == 1
    assert engineering.samples_taken == 1


def test_record_drift_event_increments_only_drift_counter(store_factory):
    store = store_factory()
    store.record_drift_event(bucket=_bucket())
    counters = store.read_counters(bucket=_bucket())
    assert counters.samples_human_after_auto == 1
    # The drift event must NOT bump samples_taken; it's a follow-up
    # signal for the next-slice drift detector, not a decision.
    assert counters.samples_taken == 0


def test_drift_event_idempotent_pk_insert(store_factory):
    """A drift event for a brand-new bucket creates the row, then
    increments the drift counter; the SQLite path uses an INSERT OR
    IGNORE then UPDATE pattern that must not double-insert.
    """
    store = store_factory()
    store.record_drift_event(bucket=_bucket())
    store.record_drift_event(bucket=_bucket())
    counters = store.read_counters(bucket=_bucket())
    assert counters.samples_human_after_auto == 2


def test_unknown_topic_sentinel_helper():
    """:meth:`SamplingBucket.from_optional` collapses missing clusters
    to the canonical sentinel so the SPC sampler key stays stable."""
    assert (
        SamplingBucket.from_optional(content_type="text/plain", topic_cluster=None).topic_cluster
        == UNKNOWN_TOPIC_CLUSTER
    )
    assert (
        SamplingBucket.from_optional(content_type="text/plain", topic_cluster="").topic_cluster
        == UNKNOWN_TOPIC_CLUSTER
    )
    # A non-empty cluster passes through unchanged.
    assert (
        SamplingBucket.from_optional(
            content_type="text/plain", topic_cluster="compliance"
        ).topic_cluster
        == "compliance"
    )


def test_sqlite_persists_across_instances(tmp_path):
    """A second :class:`SQLiteSamplingStateStore` against the same DB
    must see the counters the first one wrote — checks the catalog
    backup story holds.
    """
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    first = SQLiteSamplingStateStore(db_path)
    first.record_decision(bucket=_bucket(), method="auto")
    second = SQLiteSamplingStateStore(db_path)
    counters = second.read_counters(bucket=_bucket())
    assert counters.samples_auto == 1
