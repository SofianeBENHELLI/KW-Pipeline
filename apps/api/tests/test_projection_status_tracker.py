"""Unit tests for :class:`ProjectionStatusTracker`.

Pure dataclass + dict + lock — no FastAPI, no HTTP. Tests pin the
state-machine transitions, the TTL pruning, and the thread-safety
contract.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.services.projection_status_tracker import (
    DEFAULT_TERMINAL_TTL_SECONDS,
    ProjectionStatus,
    ProjectionStatusTracker,
)


class TestStateMachine:
    def test_get_returns_none_for_unknown_version(self) -> None:
        tracker = ProjectionStatusTracker()
        assert tracker.get("never-seen") is None

    def test_mark_started_creates_in_progress_entry(self) -> None:
        tracker = ProjectionStatusTracker()
        tracker.mark_started("v-1")

        entry = tracker.get("v-1")
        assert entry is not None
        assert entry.version_id == "v-1"
        assert entry.status is ProjectionStatus.IN_PROGRESS
        assert entry.completed_at is None
        assert entry.error is None

    def test_mark_completed_after_started(self) -> None:
        tracker = ProjectionStatusTracker()
        tracker.mark_started("v-1")
        tracker.mark_completed("v-1")

        entry = tracker.get("v-1")
        assert entry is not None
        assert entry.status is ProjectionStatus.COMPLETED
        assert entry.completed_at is not None
        assert entry.error is None

    def test_mark_failed_after_started(self) -> None:
        tracker = ProjectionStatusTracker()
        tracker.mark_started("v-1")
        tracker.mark_failed("v-1", "RuntimeError: boom")

        entry = tracker.get("v-1")
        assert entry is not None
        assert entry.status is ProjectionStatus.FAILED
        assert entry.completed_at is not None
        assert entry.error == "RuntimeError: boom"

    def test_mark_completed_without_started_is_silent_no_op(self) -> None:
        """Out-of-order completion is a bug elsewhere; tracker stays quiet."""
        tracker = ProjectionStatusTracker()
        tracker.mark_completed("v-1")
        assert tracker.get("v-1") is None

    def test_mark_failed_without_started_is_silent_no_op(self) -> None:
        tracker = ProjectionStatusTracker()
        tracker.mark_failed("v-1", "boom")
        assert tracker.get("v-1") is None

    def test_mark_started_overwrites_prior_terminal(self) -> None:
        """Re-validating runs projection again; the new IN_PROGRESS wins."""
        tracker = ProjectionStatusTracker()
        tracker.mark_started("v-1")
        tracker.mark_completed("v-1")
        tracker.mark_started("v-1")

        entry = tracker.get("v-1")
        assert entry is not None
        assert entry.status is ProjectionStatus.IN_PROGRESS
        assert entry.completed_at is None

    def test_error_truncated_to_200_chars(self) -> None:
        tracker = ProjectionStatusTracker()
        tracker.mark_started("v-1")
        tracker.mark_failed("v-1", "x" * 500)

        entry = tracker.get("v-1")
        assert entry is not None
        assert entry.error is not None
        assert len(entry.error) == 200


class TestTTL:
    def test_terminal_entries_pruned_after_ttl(self) -> None:
        clock = _MutableClock()
        tracker = ProjectionStatusTracker(terminal_ttl_seconds=10, clock=clock.now)

        # v-1 completes at t=0
        tracker.mark_started("v-1")
        tracker.mark_completed("v-1")
        assert tracker.get("v-1") is not None

        # Jump well past TTL, then trigger another mark to invoke prune.
        clock.advance(seconds=30)
        tracker.mark_started("v-2")
        tracker.mark_completed("v-2")

        assert tracker.get("v-1") is None  # pruned
        assert tracker.get("v-2") is not None  # fresh

    def test_in_progress_entries_never_pruned(self) -> None:
        """A pathologically long-running projection isn't dropped."""
        clock = _MutableClock()
        tracker = ProjectionStatusTracker(terminal_ttl_seconds=10, clock=clock.now)

        tracker.mark_started("long-runner")
        clock.advance(seconds=3600)

        # Trigger a prune via an unrelated terminal write.
        tracker.mark_started("other")
        tracker.mark_completed("other")

        # Long-runner still in flight, still tracked.
        entry = tracker.get("long-runner")
        assert entry is not None
        assert entry.status is ProjectionStatus.IN_PROGRESS

    def test_zero_or_negative_ttl_disables_pruning(self) -> None:
        clock = _MutableClock()
        tracker = ProjectionStatusTracker(terminal_ttl_seconds=0, clock=clock.now)

        tracker.mark_started("v-1")
        tracker.mark_completed("v-1")
        clock.advance(seconds=10_000)
        tracker.mark_started("v-2")
        tracker.mark_completed("v-2")

        # Both entries survive the elapsed time because pruning is off.
        assert tracker.get("v-1") is not None
        assert tracker.get("v-2") is not None


class TestThreadSafety:
    def test_concurrent_writes_do_not_corrupt_state(self) -> None:
        """100 threads each marking a unique version end-to-end. The
        tracker must record exactly 100 COMPLETED entries — no exceptions,
        no races, no lost writes."""
        tracker = ProjectionStatusTracker()
        threads: list[threading.Thread] = []
        n = 100

        def worker(i: int) -> None:
            vid = f"v-{i}"
            tracker.mark_started(vid)
            time.sleep(0)  # encourage scheduler interleaving
            tracker.mark_completed(vid)

        for i in range(n):
            threads.append(threading.Thread(target=worker, args=(i,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for i in range(n):
            entry = tracker.get(f"v-{i}")
            assert entry is not None
            assert entry.status is ProjectionStatus.COMPLETED


class TestDefaults:
    def test_default_ttl_is_one_hour(self) -> None:
        assert DEFAULT_TERMINAL_TTL_SECONDS == 3600

    def test_default_clock_returns_aware_utc(self) -> None:
        tracker = ProjectionStatusTracker()
        tracker.mark_started("v-1")
        entry = tracker.get("v-1")
        assert entry is not None
        assert entry.started_at.tzinfo is not None


# ─── Helper ───────────────────────────────────────────────────────────────────


class _MutableClock:
    """Manually-advanced clock for TTL tests."""

    def __init__(self, *, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 5, 10, 0, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, *, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


@pytest.fixture
def _silence_unused() -> None:
    """Silence ``import time`` lint when only used in one method above."""
    return None
