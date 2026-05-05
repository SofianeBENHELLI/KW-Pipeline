"""Corpus norms backing the length / asset z-score signals (ADR-023 §1, §4).

The :class:`ConfidenceScorer`'s section-length and asset-count
signals are z-scored against per-bucket corpus norms — a "policy"
PDF in the "compliance" cluster has a different length distribution
than a "specs" DOCX in the "engineering" cluster, and we want each
section's anomaly score to be relative to its own bucket, not a
global mean that mashes them together.

Two implementations live here, behind the
:class:`CorpusNormsProvider` Protocol:

- :class:`InMemoryCorpusNormsStore` — deterministic, list-backed; the
  default for the in-memory wiring and unit tests.
- :class:`SQLiteCorpusNormsStore` — persistent, materialises
  ``(mean, stddev, sample_count)`` rows in the ``corpus_norms``
  table (migration 0008).

Norms are **lazily materialised**: the first request for an unknown
bucket triggers a one-time scan via the configured
:class:`NormSampleProvider` (typically a thin adapter over the
catalog's semantic documents). Subsequent reads are O(1). A future
slice will add a nightly recompute job; v1 lives with whatever the
bucket looked like at first use, which is good enough for the pilot
and consistent with ADR-023 §4's cold-start tolerance.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


# Canonical metric names. Strings (not enums) so the SQLite primary
# key has no implicit Pydantic coupling and tests can pass any string
# without a schema dance — the scorer is the only writer in v1.
METRIC_SECTION_LENGTH = "section_length"
METRIC_ASSET_COUNT = "asset_count"


@dataclass(frozen=True)
class CorpusNorm:
    """One materialised norm bucket.

    ``stddev = 0.0`` is a valid value (every sample in the bucket is
    identical); the scorer treats that case as "every section in this
    bucket scores 1.0" rather than dividing by zero. ``sample_count
    == 0`` is impossible — the scorer falls back to "unknown bucket"
    rather than persisting a zero-sample row.
    """

    content_type: str
    topic_cluster: str
    metric_name: str
    sample_count: int
    mean: float
    stddev: float


@runtime_checkable
class CorpusNormsProvider(Protocol):
    """Read-side contract for "what's the norm for this bucket?"

    Returns ``None`` for unknown buckets. The scorer treats a ``None``
    return as "score this signal 1.0 for the affected sections" per
    ADR-023's cold-start tolerance — we don't penalise the corpus for
    not knowing what "normal" looks like yet.
    """

    def get(
        self,
        *,
        content_type: str,
        topic_cluster: str,
        metric_name: str,
    ) -> CorpusNorm | None:  # pragma: no cover - Protocol
        ...

    def upsert(self, norm: CorpusNorm) -> None:  # pragma: no cover - Protocol
        """Replace any existing row for ``(content_type, topic_cluster,
        metric_name)`` with ``norm``. Idempotent."""

    def list_all(self) -> list[CorpusNorm]:  # pragma: no cover - Protocol
        """Return every persisted norm. Used by tests and future
        admin tooling; not on a hot path."""


@runtime_checkable
class NormSampleProvider(Protocol):
    """Source of raw samples for the lazy materialisation path.

    Production wiring is the catalog's semantic documents; tests
    inject a fixed list. The provider is consulted at most once per
    bucket per process (the materialised norm is then persisted) so
    its read pattern can be expensive without dominating the scorer.
    """

    def section_length_samples(
        self,
        *,
        content_type: str,
        topic_cluster: str,
    ) -> list[int]:  # pragma: no cover - Protocol
        ...

    def asset_count_samples(
        self,
        *,
        content_type: str,
        topic_cluster: str,
    ) -> list[int]:  # pragma: no cover - Protocol
        ...


class InMemoryCorpusNormsStore:
    """Dict-backed :class:`CorpusNormsProvider`. Default for tests."""

    name: str = "in-memory"

    def __init__(self) -> None:
        self._norms: dict[tuple[str, str, str], CorpusNorm] = {}
        self._lock = threading.RLock()

    def get(
        self,
        *,
        content_type: str,
        topic_cluster: str,
        metric_name: str,
    ) -> CorpusNorm | None:
        with self._lock:
            return self._norms.get((content_type, topic_cluster, metric_name))

    def upsert(self, norm: CorpusNorm) -> None:
        if norm.sample_count <= 0:
            raise ValueError(
                f"corpus norm sample_count must be positive; got {norm.sample_count}.",
            )
        with self._lock:
            self._norms[(norm.content_type, norm.topic_cluster, norm.metric_name)] = norm

    def list_all(self) -> list[CorpusNorm]:
        with self._lock:
            return sorted(
                self._norms.values(),
                key=lambda n: (n.content_type, n.topic_cluster, n.metric_name),
            )


class SQLiteCorpusNormsStore:
    """SQLite-backed :class:`CorpusNormsProvider`.

    Reuses the catalog's database file by default (migration 0008
    creates the ``corpus_norms`` table inside it). One thread-safe
    connection per store instance; ``check_same_thread`` is False
    because FastAPI dispatches handlers across the thread pool.
    """

    name: str = "sqlite"

    def __init__(self, database_path: Path | str) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.RLock()

    def get(
        self,
        *,
        content_type: str,
        topic_cluster: str,
        metric_name: str,
    ) -> CorpusNorm | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT content_type, topic_cluster, metric_name, sample_count, mean, stddev "
                "FROM corpus_norms "
                "WHERE content_type = ? AND topic_cluster = ? AND metric_name = ?",
                (content_type, topic_cluster, metric_name),
            ).fetchone()
        if row is None:
            return None
        return CorpusNorm(
            content_type=row[0],
            topic_cluster=row[1],
            metric_name=row[2],
            sample_count=int(row[3]),
            mean=float(row[4]),
            stddev=float(row[5]),
        )

    def upsert(self, norm: CorpusNorm) -> None:
        if norm.sample_count <= 0:
            raise ValueError(
                f"corpus norm sample_count must be positive; got {norm.sample_count}.",
            )
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO corpus_norms ("
                "  content_type, topic_cluster, metric_name, sample_count, mean, stddev, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    norm.content_type,
                    norm.topic_cluster,
                    norm.metric_name,
                    norm.sample_count,
                    norm.mean,
                    norm.stddev,
                    now,
                ),
            )

    def list_all(self) -> list[CorpusNorm]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT content_type, topic_cluster, metric_name, sample_count, mean, stddev "
                "FROM corpus_norms "
                "ORDER BY content_type, topic_cluster, metric_name"
            ).fetchall()
        return [
            CorpusNorm(
                content_type=row[0],
                topic_cluster=row[1],
                metric_name=row[2],
                sample_count=int(row[3]),
                mean=float(row[4]),
                stddev=float(row[5]),
            )
            for row in rows
        ]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(str(self._path))
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()


def compute_norm_from_samples(
    *,
    content_type: str,
    topic_cluster: str,
    metric_name: str,
    samples: Sequence[float],
) -> CorpusNorm | None:
    """Build a :class:`CorpusNorm` from raw samples.

    Returns ``None`` for an empty sample list (the scorer treats this
    as "unknown bucket"). Uses the **population standard deviation**
    rather than the sample stddev — the corpus *is* our population
    for the purposes of "what's normal in this bucket?", and the
    population formula is well-defined for ``n == 1`` (yields
    ``stddev = 0.0``).
    """
    if not samples:
        return None
    n = len(samples)
    mean = sum(samples) / n
    variance = sum((x - mean) ** 2 for x in samples) / n
    stddev = math.sqrt(variance)
    return CorpusNorm(
        content_type=content_type,
        topic_cluster=topic_cluster,
        metric_name=metric_name,
        sample_count=n,
        mean=mean,
        stddev=stddev,
    )


class LazyCorpusNorms:
    """Materialise norms on first read; cache to a backing store.

    Wraps a :class:`CorpusNormsProvider` (the persistence layer) and
    a :class:`NormSampleProvider` (the sample source) so the scorer
    can call :meth:`get` unconditionally and pay the materialisation
    cost only on the first miss for a bucket. Subsequent reads hit
    the persisted row directly.

    Buckets with **no samples** in the source provider are recorded
    as "absent" via the ``_known_empty`` set so we don't re-walk the
    corpus on every score. The set is in-process only; a fresh boot
    re-checks (cheap given the persisted store usually has the row).
    """

    name: str = "lazy"

    def __init__(
        self,
        *,
        store: CorpusNormsProvider,
        samples: NormSampleProvider,
    ) -> None:
        self._store = store
        self._samples = samples
        self._known_empty: set[tuple[str, str, str]] = set()
        self._lock = threading.RLock()

    def get(
        self,
        *,
        content_type: str,
        topic_cluster: str,
        metric_name: str,
    ) -> CorpusNorm | None:
        cached = self._store.get(
            content_type=content_type,
            topic_cluster=topic_cluster,
            metric_name=metric_name,
        )
        if cached is not None:
            return cached

        key = (content_type, topic_cluster, metric_name)
        with self._lock:
            if key in self._known_empty:
                return None

        # Cache miss: pull samples and materialise.
        if metric_name == METRIC_SECTION_LENGTH:
            raw = self._samples.section_length_samples(
                content_type=content_type,
                topic_cluster=topic_cluster,
            )
        elif metric_name == METRIC_ASSET_COUNT:
            raw = self._samples.asset_count_samples(
                content_type=content_type,
                topic_cluster=topic_cluster,
            )
        else:
            # Unknown metric — no fallback possible. Log and return
            # None so the scorer treats the signal as cold-start.
            log.warning(
                "corpus_norms.unknown_metric",
                extra={"metric_name": metric_name},
            )
            return None

        norm = compute_norm_from_samples(
            content_type=content_type,
            topic_cluster=topic_cluster,
            metric_name=metric_name,
            samples=raw,
        )
        if norm is None:
            with self._lock:
                self._known_empty.add(key)
            return None

        try:
            self._store.upsert(norm)
        except Exception:  # noqa: BLE001 - persistence is best-effort
            log.exception(
                "corpus_norms.persist_failed",
                extra={
                    "content_type": content_type,
                    "topic_cluster": topic_cluster,
                    "metric_name": metric_name,
                },
            )
        return norm

    def upsert(self, norm: CorpusNorm) -> None:
        self._store.upsert(norm)
        # Drop any cached "empty" marker for this bucket — we now have
        # a real norm, so future lookups should hit the store.
        with self._lock:
            self._known_empty.discard((norm.content_type, norm.topic_cluster, norm.metric_name))

    def list_all(self) -> list[CorpusNorm]:
        return self._store.list_all()


__all__ = [
    "CorpusNorm",
    "CorpusNormsProvider",
    "InMemoryCorpusNormsStore",
    "LazyCorpusNorms",
    "METRIC_ASSET_COUNT",
    "METRIC_SECTION_LENGTH",
    "NormSampleProvider",
    "SQLiteCorpusNormsStore",
    "compute_norm_from_samples",
]
