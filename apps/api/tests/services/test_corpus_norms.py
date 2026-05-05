"""Tests for the corpus-norms layer (ADR-023 §1, §4, #215).

Three layers under test:

1. :func:`compute_norm_from_samples` — pure math.
2. :class:`InMemoryCorpusNormsStore` / :class:`SQLiteCorpusNormsStore`
   — persistence round-trips, parametrised across both impls.
3. :class:`LazyCorpusNorms` — first-miss materialisation through a
   :class:`NormSampleProvider` fake.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest

from app.services.corpus_norms import (
    METRIC_ASSET_COUNT,
    METRIC_SECTION_LENGTH,
    CorpusNorm,
    CorpusNormsProvider,
    InMemoryCorpusNormsStore,
    LazyCorpusNorms,
    SQLiteCorpusNormsStore,
    compute_norm_from_samples,
)
from app.services.migrations import _run_migrations

# ---------------------------------------------------------------------------
# Pure math
# ---------------------------------------------------------------------------


def test_compute_norm_from_empty_samples_returns_none():
    assert (
        compute_norm_from_samples(
            content_type="text/plain",
            topic_cluster="",
            metric_name=METRIC_SECTION_LENGTH,
            samples=[],
        )
        is None
    )


def test_compute_norm_single_sample_yields_zero_stddev():
    """Population stddev with n=1 is zero by definition."""
    norm = compute_norm_from_samples(
        content_type="text/plain",
        topic_cluster="",
        metric_name=METRIC_SECTION_LENGTH,
        samples=[42],
    )
    assert norm is not None
    assert norm.sample_count == 1
    assert norm.mean == pytest.approx(42.0)
    assert norm.stddev == 0.0


def test_compute_norm_population_formula():
    """Pin the formula: variance = Σ(x-μ)² / n (population, not sample)."""
    samples = [10, 20, 30, 40, 50]
    norm = compute_norm_from_samples(
        content_type="text/plain",
        topic_cluster="",
        metric_name=METRIC_SECTION_LENGTH,
        samples=samples,
    )
    assert norm is not None
    # μ = 30, Σ(x-μ)² = 400 + 100 + 0 + 100 + 400 = 1000, /5 = 200, √ = ~14.14
    assert norm.mean == pytest.approx(30.0)
    assert norm.stddev == pytest.approx(math.sqrt(200), rel=1e-6)


# ---------------------------------------------------------------------------
# Persistence (parametrised)
# ---------------------------------------------------------------------------


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
def norms_store(request, tmp_path) -> CorpusNormsProvider:
    if request.param == "in-memory":
        return InMemoryCorpusNormsStore()
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    return SQLiteCorpusNormsStore(db_path)


def test_get_missing_bucket_returns_none(norms_store):
    assert (
        norms_store.get(
            content_type="text/plain",
            topic_cluster="cluster-A",
            metric_name=METRIC_SECTION_LENGTH,
        )
        is None
    )


def test_upsert_then_get_round_trip(norms_store):
    norm = CorpusNorm(
        content_type="text/plain",
        topic_cluster="cluster-A",
        metric_name=METRIC_SECTION_LENGTH,
        sample_count=5,
        mean=200.0,
        stddev=50.0,
    )
    norms_store.upsert(norm)
    fetched = norms_store.get(
        content_type="text/plain",
        topic_cluster="cluster-A",
        metric_name=METRIC_SECTION_LENGTH,
    )
    assert fetched is not None
    assert fetched.sample_count == 5
    assert fetched.mean == pytest.approx(200.0)
    assert fetched.stddev == pytest.approx(50.0)


def test_upsert_replaces_existing_bucket(norms_store):
    norms_store.upsert(
        CorpusNorm(
            content_type="text/plain",
            topic_cluster="",
            metric_name=METRIC_SECTION_LENGTH,
            sample_count=1,
            mean=1.0,
            stddev=0.0,
        )
    )
    norms_store.upsert(
        CorpusNorm(
            content_type="text/plain",
            topic_cluster="",
            metric_name=METRIC_SECTION_LENGTH,
            sample_count=10,
            mean=100.0,
            stddev=10.0,
        )
    )
    fetched = norms_store.get(
        content_type="text/plain",
        topic_cluster="",
        metric_name=METRIC_SECTION_LENGTH,
    )
    assert fetched is not None
    assert fetched.mean == pytest.approx(100.0)
    assert fetched.sample_count == 10


def test_zero_sample_count_raises(norms_store):
    with pytest.raises(ValueError, match="sample_count"):
        norms_store.upsert(
            CorpusNorm(
                content_type="text/plain",
                topic_cluster="",
                metric_name=METRIC_SECTION_LENGTH,
                sample_count=0,
                mean=0.0,
                stddev=0.0,
            )
        )


def test_list_all_returns_every_row(norms_store):
    for content_type in ("text/plain", "application/pdf"):
        norms_store.upsert(
            CorpusNorm(
                content_type=content_type,
                topic_cluster="",
                metric_name=METRIC_SECTION_LENGTH,
                sample_count=10,
                mean=100.0,
                stddev=10.0,
            )
        )
    rows = norms_store.list_all()
    assert {row.content_type for row in rows} == {"text/plain", "application/pdf"}


# ---------------------------------------------------------------------------
# Lazy materialisation
# ---------------------------------------------------------------------------


class _FakeSamples:
    """Hand-written :class:`NormSampleProvider` for the lazy path."""

    def __init__(self, length_samples: list[int], asset_samples: list[int]) -> None:
        self.length_samples = length_samples
        self.asset_samples = asset_samples
        self.length_call_count = 0
        self.asset_call_count = 0

    def section_length_samples(self, *, content_type: str, topic_cluster: str) -> list[int]:
        del content_type, topic_cluster
        self.length_call_count += 1
        return list(self.length_samples)

    def asset_count_samples(self, *, content_type: str, topic_cluster: str) -> list[int]:
        del content_type, topic_cluster
        self.asset_call_count += 1
        return list(self.asset_samples)


def test_lazy_norms_materialises_on_first_miss():
    backing = InMemoryCorpusNormsStore()
    samples = _FakeSamples(length_samples=[10, 20, 30, 40, 50], asset_samples=[5])
    lazy = LazyCorpusNorms(store=backing, samples=samples)

    # First request: miss → samples consulted → norm persisted.
    norm = lazy.get(
        content_type="text/plain",
        topic_cluster="cluster-A",
        metric_name=METRIC_SECTION_LENGTH,
    )
    assert norm is not None
    assert norm.mean == pytest.approx(30.0)
    assert samples.length_call_count == 1
    # Backing store now has the row.
    assert backing.list_all()


def test_lazy_norms_caches_subsequent_reads():
    backing = InMemoryCorpusNormsStore()
    samples = _FakeSamples(length_samples=[100], asset_samples=[])
    lazy = LazyCorpusNorms(store=backing, samples=samples)

    lazy.get(
        content_type="text/plain",
        topic_cluster="",
        metric_name=METRIC_SECTION_LENGTH,
    )
    lazy.get(
        content_type="text/plain",
        topic_cluster="",
        metric_name=METRIC_SECTION_LENGTH,
    )
    # Second call hits the persisted row, not the sample provider.
    assert samples.length_call_count == 1


def test_lazy_norms_remembers_empty_buckets():
    """An empty sample list means 'cold-start, no signal' — re-asking
    should NOT trigger another corpus walk."""
    backing = InMemoryCorpusNormsStore()
    samples = _FakeSamples(length_samples=[], asset_samples=[])
    lazy = LazyCorpusNorms(store=backing, samples=samples)

    assert (
        lazy.get(
            content_type="text/plain",
            topic_cluster="",
            metric_name=METRIC_SECTION_LENGTH,
        )
        is None
    )
    assert (
        lazy.get(
            content_type="text/plain",
            topic_cluster="",
            metric_name=METRIC_SECTION_LENGTH,
        )
        is None
    )
    # Sample provider is consulted once; the second call is short-circuited.
    assert samples.length_call_count == 1


def test_lazy_norms_unknown_metric_returns_none():
    backing = InMemoryCorpusNormsStore()
    samples = _FakeSamples(length_samples=[1, 2], asset_samples=[1, 2])
    lazy = LazyCorpusNorms(store=backing, samples=samples)
    assert (
        lazy.get(
            content_type="text/plain",
            topic_cluster="",
            metric_name="unknown_metric",
        )
        is None
    )


def test_lazy_norms_upsert_clears_known_empty_marker():
    """An explicit upsert must override a cached 'empty bucket' marker."""
    backing = InMemoryCorpusNormsStore()
    samples = _FakeSamples(length_samples=[], asset_samples=[])
    lazy = LazyCorpusNorms(store=backing, samples=samples)

    # First lookup: cold-start, marks the bucket empty.
    assert (
        lazy.get(
            content_type="text/plain",
            topic_cluster="",
            metric_name=METRIC_SECTION_LENGTH,
        )
        is None
    )
    # Operator manually upserts a norm.
    lazy.upsert(
        CorpusNorm(
            content_type="text/plain",
            topic_cluster="",
            metric_name=METRIC_SECTION_LENGTH,
            sample_count=3,
            mean=42.0,
            stddev=1.0,
        )
    )
    # Subsequent lookup must serve the persisted row.
    fetched = lazy.get(
        content_type="text/plain",
        topic_cluster="",
        metric_name=METRIC_SECTION_LENGTH,
    )
    assert fetched is not None
    assert fetched.mean == pytest.approx(42.0)


def test_lazy_norms_asset_metric_path():
    backing = InMemoryCorpusNormsStore()
    samples = _FakeSamples(length_samples=[], asset_samples=[3, 4, 5])
    lazy = LazyCorpusNorms(store=backing, samples=samples)
    norm = lazy.get(
        content_type="text/plain",
        topic_cluster="",
        metric_name=METRIC_ASSET_COUNT,
    )
    assert norm is not None
    assert norm.mean == pytest.approx(4.0)
    assert samples.asset_call_count == 1


def test_lazy_norms_list_all_delegates_to_backing_store():
    """``list_all`` is a thin pass-through; admin tooling reads through it."""
    backing = InMemoryCorpusNormsStore()
    backing.upsert(
        CorpusNorm(
            content_type="text/plain",
            topic_cluster="",
            metric_name=METRIC_SECTION_LENGTH,
            sample_count=3,
            mean=10.0,
            stddev=1.0,
        )
    )
    lazy = LazyCorpusNorms(
        store=backing,
        samples=_FakeSamples(length_samples=[], asset_samples=[]),
    )
    rows = lazy.list_all()
    assert len(rows) == 1
    assert rows[0].mean == pytest.approx(10.0)


def test_lazy_norms_swallows_persist_failure():
    """If the persistence layer raises on upsert, the materialised norm
    is still returned — persistence is best-effort per ADR-023 §4."""

    class _FlakyStore:
        name = "flaky"

        def get(self, **_kwargs):
            return None

        def upsert(self, norm):
            raise RuntimeError("simulated outage")

        def list_all(self):
            return []

    samples = _FakeSamples(length_samples=[10, 20, 30], asset_samples=[])
    lazy = LazyCorpusNorms(store=_FlakyStore(), samples=samples)
    norm = lazy.get(
        content_type="text/plain",
        topic_cluster="",
        metric_name=METRIC_SECTION_LENGTH,
    )
    assert norm is not None
    assert norm.mean == pytest.approx(20.0)
