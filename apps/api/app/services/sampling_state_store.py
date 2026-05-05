"""SPC sampling state store (ADR-023 §6, EPIC-A A.3, #215).

Records per-bucket counters for the HITL router's SPC (statistical
process control) sampling layer. A bucket is a
``(content_type, topic_cluster)`` pair — the same axis the corpus
norms use for the section-length / asset-count z-score signals — so
the future drift detector can correlate "sample escalations" with
"section-length anomalies" without a join through a third table.

For this slice the consumer is :class:`HITLRouter` only. Two
implementations live behind the :class:`SamplingStateStore` Protocol:

- :class:`InMemorySamplingStateStore` — dict-backed; the test default
  and the in-memory wiring's backing store.
- :class:`SQLiteSamplingStateStore` — persistent; reuses the catalog's
  database file (migration 0009) so a backup of ``catalog.sqlite3``
  carries the SPC counters along with the validation metadata.

Counter vocabulary (the column comments in migration 0009 explain
the wire shape):

- ``samples_taken``   — total decisions recorded for this bucket.
- ``samples_auto``    — decisions where the router picked ``auto``.
- ``samples_human``   — decisions where the router picked ``human``
  (covers below-threshold, OCR-override, and SPC-escalated paths).
- ``samples_human_after_auto`` — drift signal. Bumped via
  :meth:`record_drift_event` when a human reviewer flips a
  previously-auto-routed version. The auto-promotion / drift-detector
  worker is the next slice; today the column exists so the schema
  doesn't need a follow-up migration when that slice lands.

External routing decisions (``method == "external"``) currently never
fire because the EPIC-B branch is dead, so we don't record an
``samples_external`` column yet. When EPIC-B lands a future migration
will add it without breaking the current contract.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.schemas.validation_metadata import RoutingMethod

log = logging.getLogger(__name__)

# Sentinel for "no topic cluster" — corpus norms uses the empty string
# for the same purpose, but a primary-key column needs a non-NULL
# default and ``""`` is awkward to read in SQL. ``"_unknown_"`` is
# loud enough that an audit query won't mistake it for a real cluster.
UNKNOWN_TOPIC_CLUSTER = "_unknown_"


@dataclass(frozen=True)
class SamplingBucket:
    """The ``(content_type, topic_cluster)`` axis SPC counters key on.

    Frozen so the bucket can serve as a dict key on the in-memory
    store without an explicit ``__hash__`` shim, and so callers can't
    mutate a bucket after passing it in.
    """

    content_type: str
    topic_cluster: str

    @classmethod
    def from_optional(
        cls,
        *,
        content_type: str,
        topic_cluster: str | None,
    ) -> SamplingBucket:
        """Coerce a possibly-empty topic cluster into the canonical bucket.

        ``None`` and ``""`` both collapse to :data:`UNKNOWN_TOPIC_CLUSTER`
        so the sampler key stays stable across the in-memory wiring
        (which uses the empty string for "no cluster") and the
        persistent wiring (which writes ``"_unknown_"`` to the DB).
        """
        cluster = topic_cluster or ""
        return cls(content_type=content_type, topic_cluster=cluster or UNKNOWN_TOPIC_CLUSTER)


@dataclass(frozen=True)
class SamplingCounters:
    """Read-side view of one bucket's counters."""

    samples_taken: int = 0
    samples_auto: int = 0
    samples_human: int = 0
    samples_human_after_auto: int = 0
    last_decision_at: datetime | None = None


@runtime_checkable
class SamplingStateStore(Protocol):
    """Per-bucket SPC counter persistence boundary."""

    name: str

    def record_decision(  # pragma: no cover - Protocol
        self,
        *,
        bucket: SamplingBucket,
        method: RoutingMethod,
    ) -> None:
        """Bump ``samples_taken`` and the matching method counter.

        Idempotent on the schema dimension only — a second call always
        increments. The router invokes this exactly once per
        :meth:`HITLRouter.decide`, which is the contract.
        """

    def record_drift_event(  # pragma: no cover - Protocol
        self,
        *,
        bucket: SamplingBucket,
    ) -> None:
        """Bump ``samples_human_after_auto`` for the drift detector.

        Called by the future auto-promotion / drift-detector worker
        when a human reviewer overturns a previously-auto-routed
        version. Today no caller invokes this — the method exists so
        the schema and tests are pinned for the slice that lights it
        up.
        """

    def read_counters(  # pragma: no cover - Protocol
        self,
        *,
        bucket: SamplingBucket,
    ) -> SamplingCounters:
        """Return the bucket's counters; absent buckets read as zeroed."""


@dataclass
class _MutableCounters:
    samples_taken: int = 0
    samples_auto: int = 0
    samples_human: int = 0
    samples_human_after_auto: int = 0
    last_decision_at: datetime | None = None

    def snapshot(self) -> SamplingCounters:
        return SamplingCounters(
            samples_taken=self.samples_taken,
            samples_auto=self.samples_auto,
            samples_human=self.samples_human,
            samples_human_after_auto=self.samples_human_after_auto,
            last_decision_at=self.last_decision_at,
        )


class InMemorySamplingStateStore:
    """Dict-backed :class:`SamplingStateStore` for tests + in-memory wiring."""

    name: str = "in-memory"

    def __init__(self) -> None:
        self._rows: dict[SamplingBucket, _MutableCounters] = {}
        self._lock = threading.RLock()

    def record_decision(
        self,
        *,
        bucket: SamplingBucket,
        method: RoutingMethod,
    ) -> None:
        with self._lock:
            row = self._rows.setdefault(bucket, _MutableCounters())
            row.samples_taken += 1
            if method == "auto":
                row.samples_auto += 1
            elif method == "human":
                row.samples_human += 1
            # ``external`` decisions are recorded on samples_taken only;
            # see the module docstring for why the dedicated column is
            # deferred until EPIC-B lights up the branch.
            row.last_decision_at = datetime.now(UTC)

    def record_drift_event(self, *, bucket: SamplingBucket) -> None:
        with self._lock:
            row = self._rows.setdefault(bucket, _MutableCounters())
            row.samples_human_after_auto += 1

    def read_counters(self, *, bucket: SamplingBucket) -> SamplingCounters:
        with self._lock:
            row = self._rows.get(bucket)
        return row.snapshot() if row is not None else SamplingCounters()


class SQLiteSamplingStateStore:
    """SQLite-backed :class:`SamplingStateStore` (migration 0009).

    Reuses the catalog's database file so a single backup carries the
    sampling state along with ``document_versions`` /
    ``validation_metadata`` / ``corpus_norms``. The compound primary
    key ``(content_type, topic_cluster)`` keeps inserts idempotent
    under the ``INSERT OR IGNORE`` + ``UPDATE`` write pattern below.
    """

    name: str = "sqlite"

    def __init__(self, database_path: Path | str) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.RLock()

    def record_decision(
        self,
        *,
        bucket: SamplingBucket,
        method: RoutingMethod,
    ) -> None:
        now_iso = datetime.now(UTC).isoformat()
        column_for_method = {
            "auto": "samples_auto",
            "human": "samples_human",
        }
        with self._connect() as conn:
            # Idempotent PK insert; the UPDATE below is what increments.
            # Using a separate INSERT-OR-IGNORE + UPDATE rather than
            # an UPSERT keeps SQLite older than 3.24 happy too.
            conn.execute(
                "INSERT OR IGNORE INTO sampling_state ("
                "  content_type, topic_cluster, samples_taken, samples_auto,"
                "  samples_human, samples_human_after_auto, last_decision_at"
                ") VALUES (?, ?, 0, 0, 0, 0, NULL)",
                (bucket.content_type, bucket.topic_cluster),
            )
            method_column = column_for_method.get(method)
            if method_column is None:
                # ``external`` branch — bump samples_taken only. The
                # dedicated column is deferred until EPIC-B lights it up.
                conn.execute(
                    "UPDATE sampling_state "
                    "SET samples_taken = samples_taken + 1, last_decision_at = ? "
                    "WHERE content_type = ? AND topic_cluster = ?",
                    (now_iso, bucket.content_type, bucket.topic_cluster),
                )
            else:
                conn.execute(
                    f"UPDATE sampling_state "
                    f"SET samples_taken = samples_taken + 1, "
                    f"    {method_column} = {method_column} + 1, "
                    f"    last_decision_at = ? "
                    f"WHERE content_type = ? AND topic_cluster = ?",
                    (now_iso, bucket.content_type, bucket.topic_cluster),
                )

    def record_drift_event(self, *, bucket: SamplingBucket) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sampling_state ("
                "  content_type, topic_cluster, samples_taken, samples_auto,"
                "  samples_human, samples_human_after_auto, last_decision_at"
                ") VALUES (?, ?, 0, 0, 0, 0, NULL)",
                (bucket.content_type, bucket.topic_cluster),
            )
            conn.execute(
                "UPDATE sampling_state "
                "SET samples_human_after_auto = samples_human_after_auto + 1 "
                "WHERE content_type = ? AND topic_cluster = ?",
                (bucket.content_type, bucket.topic_cluster),
            )

    def read_counters(self, *, bucket: SamplingBucket) -> SamplingCounters:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT samples_taken, samples_auto, samples_human,"
                "       samples_human_after_auto, last_decision_at "
                "FROM sampling_state "
                "WHERE content_type = ? AND topic_cluster = ?",
                (bucket.content_type, bucket.topic_cluster),
            ).fetchone()
        if row is None:
            return SamplingCounters()
        (
            samples_taken,
            samples_auto,
            samples_human,
            samples_human_after_auto,
            last_decision_at,
        ) = row
        last = datetime.fromisoformat(last_decision_at) if last_decision_at is not None else None
        return SamplingCounters(
            samples_taken=int(samples_taken),
            samples_auto=int(samples_auto),
            samples_human=int(samples_human),
            samples_human_after_auto=int(samples_human_after_auto),
            last_decision_at=last,
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(str(self._path))
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()


# Re-export the dataclass so ``from sampling_state_store import _MutableCounters``
# never becomes a lure for callers — the public surface is the typed
# Protocol + the read-side ``SamplingCounters``.
__all__ = [
    "InMemorySamplingStateStore",
    "SQLiteSamplingStateStore",
    "SamplingBucket",
    "SamplingCounters",
    "SamplingStateStore",
    "UNKNOWN_TOPIC_CLUSTER",
]
