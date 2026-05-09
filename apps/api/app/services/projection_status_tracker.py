"""In-memory tracker for knowledge-projection side-effect status.

After a document is validated, the projection (graph + entity extraction)
runs as a fire-and-log side effect — sometimes inline, sometimes as a
background asyncio task. The catalog is the source of truth for the FSM
("VALIDATED"), but reviewer UIs want to know whether the *graph* is
populated yet so they can show a "Projecting…" indicator instead of an
empty graph view.

This module exposes a process-local tracker keyed by version id.

Design choices:

- **Process-local, not persisted.** Status survives until the process
  exits; on restart, the tracker forgets in-flight projections. The
  reviewer UI's polling loop tolerates that (it falls back to "completed
  enough to render whatever is in the graph store").
- **TTL pruning.** Terminal entries (``COMPLETED`` / ``FAILED``) are
  pruned after ``terminal_ttl_seconds`` so a long-lived process doesn't
  accumulate one entry per validated version. The default is 1 hour,
  comfortably longer than any realistic UI poll window.
- **Thread-safe.** A ``threading.Lock`` guards every mutation. Both the
  in-line and async dispatch paths can write to the tracker from
  different threads, so the lock is required.
- **Pure dict-of-dicts internally.** No Pydantic in the hot path; the
  route layer projects onto its response schema.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

DEFAULT_TERMINAL_TTL_SECONDS = 3600  # 1 hour


class ProjectionStatus(StrEnum):
    """States a per-version projection can be in.

    The state machine is one-way: ``IN_PROGRESS`` always transitions to
    ``COMPLETED`` or ``FAILED`` — never back. A second validate of the
    same version overwrites any prior terminal entry (the projection
    runs again).
    """

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ProjectionStatusEntry:
    """Snapshot of one version's projection status.

    Returned by reads (``ProjectionStatusTracker.get``); ``error`` is
    populated only for ``FAILED`` and carries a short, operator-readable
    string (the exception class + the first 200 chars of the message).
    Internal stack traces stay in the structured logs.
    """

    version_id: str
    status: ProjectionStatus
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


@dataclass
class ProjectionStatusTracker:
    """Thread-safe map of version_id → :class:`ProjectionStatusEntry`."""

    terminal_ttl_seconds: int = DEFAULT_TERMINAL_TTL_SECONDS
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    _entries: dict[str, ProjectionStatusEntry] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def mark_started(self, version_id: str) -> None:
        """Record that projection for ``version_id`` is now in flight.

        Overwrites any prior entry for the same version — a re-validation
        starts a fresh projection and we want the new state to win.
        """
        now = self.clock()
        with self._lock:
            self._entries[version_id] = ProjectionStatusEntry(
                version_id=version_id,
                status=ProjectionStatus.IN_PROGRESS,
                started_at=now,
            )

    def mark_completed(self, version_id: str) -> None:
        """Record that projection for ``version_id`` finished successfully.

        Silently drops the update if ``mark_started`` was never called —
        the tracker only knows what it was told, and an out-of-order
        completion is almost certainly a bug elsewhere; logging it from
        here would be noise.
        """
        now = self.clock()
        with self._lock:
            existing = self._entries.get(version_id)
            if existing is None:
                return
            self._entries[version_id] = ProjectionStatusEntry(
                version_id=version_id,
                status=ProjectionStatus.COMPLETED,
                started_at=existing.started_at,
                completed_at=now,
            )
        self._maybe_prune(now)

    def mark_failed(self, version_id: str, error: str) -> None:
        """Record that projection for ``version_id`` raised.

        ``error`` is truncated at 200 chars to keep the response small;
        full stack traces stay in the structured logs.
        """
        now = self.clock()
        truncated = (error or "")[:200]
        with self._lock:
            existing = self._entries.get(version_id)
            if existing is None:
                return
            self._entries[version_id] = ProjectionStatusEntry(
                version_id=version_id,
                status=ProjectionStatus.FAILED,
                started_at=existing.started_at,
                completed_at=now,
                error=truncated,
            )
        self._maybe_prune(now)

    def get(self, version_id: str) -> ProjectionStatusEntry | None:
        """Return the current entry for ``version_id``, or ``None`` if
        the tracker has nothing recorded (either never started or
        already pruned)."""
        with self._lock:
            return self._entries.get(version_id)

    def _maybe_prune(self, now: datetime) -> None:
        """Drop terminal entries whose ``completed_at`` is older than
        ``terminal_ttl_seconds``. Called opportunistically on every
        terminal write so the dict can't grow unbounded."""
        ttl = self.terminal_ttl_seconds
        if ttl <= 0:
            return
        with self._lock:
            stale: list[str] = []
            for vid, entry in self._entries.items():
                if entry.completed_at is None:
                    continue  # still in flight
                age = (now - entry.completed_at).total_seconds()
                if age > ttl:
                    stale.append(vid)
            for vid in stale:
                self._entries.pop(vid, None)
