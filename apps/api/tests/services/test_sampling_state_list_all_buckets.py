"""Tests for ``SamplingStateStore.list_all_buckets`` (#215, EPIC-A close-out).

Parametrised across the in-memory and SQLite implementations so the
two surfaces stay byte-equivalent. The new method powers the Admin
HITL dashboard's per-bucket table.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from app.services.migrations import _run_migrations
from app.services.sampling_state_store import (
    InMemorySamplingStateStore,
    SamplingBucket,
    SamplingStateStore,
    SQLiteSamplingStateStore,
)


def _seed_sqlite_schema(db_path: Path) -> None:
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


def test_list_all_buckets_returns_empty_for_fresh_store(store_factory):
    store = store_factory()
    assert store.list_all_buckets() == []


def test_list_all_buckets_returns_every_recorded_bucket(store_factory):
    store = store_factory()
    bucket_a = SamplingBucket(content_type="text/plain", topic_cluster="A")
    bucket_b = SamplingBucket(content_type="application/pdf", topic_cluster="B")
    store.record_decision(bucket=bucket_a, method="auto")
    store.record_decision(bucket=bucket_a, method="human")
    store.record_decision(bucket=bucket_b, method="auto")
    store.record_drift_event(bucket=bucket_b)

    pairs = store.list_all_buckets()
    by_bucket = {pair[0]: pair[1] for pair in pairs}

    assert set(by_bucket.keys()) == {bucket_a, bucket_b}
    counters_a = by_bucket[bucket_a]
    assert counters_a.samples_taken == 2
    assert counters_a.samples_auto == 1
    assert counters_a.samples_human == 1
    assert counters_a.samples_human_after_auto == 0
    counters_b = by_bucket[bucket_b]
    assert counters_b.samples_taken == 1
    assert counters_b.samples_auto == 1
    assert counters_b.samples_human_after_auto == 1


def test_list_all_buckets_is_sorted_for_determinism(store_factory):
    store = store_factory()
    bucket_z = SamplingBucket(content_type="text/plain", topic_cluster="zeta")
    bucket_a = SamplingBucket(content_type="text/plain", topic_cluster="alpha")
    bucket_pdf = SamplingBucket(content_type="application/pdf", topic_cluster="alpha")
    # Insert in a deliberately scrambled order to make sure the store
    # doesn't leak insertion ordering through the API.
    store.record_decision(bucket=bucket_z, method="auto")
    store.record_decision(bucket=bucket_a, method="auto")
    store.record_decision(bucket=bucket_pdf, method="auto")

    keys = [pair[0] for pair in store.list_all_buckets()]

    # Sorted by (content_type, topic_cluster).
    assert keys == [bucket_pdf, bucket_a, bucket_z]


def test_list_all_buckets_records_drift_only_buckets(store_factory):
    """A bucket touched only by ``record_drift_event`` (i.e. a stale
    auto-routed version flipped by a reviewer well after the fact)
    still surfaces — the store inserts a row on first touch even
    when ``samples_taken`` is 0."""
    store = store_factory()
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="never-decided")
    store.record_drift_event(bucket=bucket)

    pairs = store.list_all_buckets()

    assert len(pairs) == 1
    bucket_out, counters = pairs[0]
    assert bucket_out == bucket
    assert counters.samples_taken == 0
    assert counters.samples_human_after_auto == 1


def test_list_all_buckets_preserves_last_decision_at(store_factory):
    """``last_decision_at`` round-trips on the bucket list call —
    the dashboard renders it as a relative date, so a missing
    timestamp breaks the UI."""
    store = store_factory()
    bucket = SamplingBucket(content_type="text/plain", topic_cluster="alpha")
    store.record_decision(bucket=bucket, method="auto")

    pairs = store.list_all_buckets()

    assert len(pairs) == 1
    _, counters = pairs[0]
    assert counters.last_decision_at is not None
