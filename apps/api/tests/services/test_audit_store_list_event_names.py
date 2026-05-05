"""``AuditEventStore.list_event_names`` + ``query_page`` parity coverage.

#206 follow-up — both store impls expose the SELECT-DISTINCT
projection the admin audit log viewer's dropdown reads on every
request, plus the cursor-paginated walk the route's table renders.
Pin the sort order (lexicographic), the empty-store edge case, and
the cursor / filter contract so a future schema change can't
accidentally drop one impl out of parity with the other.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.services.audit_event_store import (
    AuditEvent,
    AuditEventStore,
    InMemoryAuditEventStore,
    SQLiteAuditEventStore,
    event_actor,
)


def _make_event(
    name: str,
    ts: datetime | None = None,
    actor: str | None = "tester",
) -> AuditEvent:
    payload: dict = {}
    if actor is not None:
        payload["actor"] = actor
    return AuditEvent(
        event_name=name,
        level="INFO",
        ts_utc=ts or datetime.now(tz=UTC),
        document_id="doc-x",
        version_id="ver-x",
        payload=payload,
    )


@pytest.fixture(params=["in_memory", "sqlite"])
def store(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AuditEventStore:
    if request.param == "in_memory":
        return InMemoryAuditEventStore()
    return SQLiteAuditEventStore(tmp_path / "audit.sqlite3")


class TestListEventNames:
    def test_empty_store_returns_empty_list(self, store: AuditEventStore) -> None:
        assert store.list_event_names() == []

    def test_distinct_names_sorted_lexicographically(self, store: AuditEventStore) -> None:
        store.append(_make_event("review.validated"))
        store.append(_make_event("routing.decided"))
        store.append(_make_event("review.validated"))  # duplicate — collapsed
        store.append(_make_event("document.archived_orphan"))

        assert store.list_event_names() == [
            "document.archived_orphan",
            "review.validated",
            "routing.decided",
        ]

    def test_one_name_returns_singleton(self, store: AuditEventStore) -> None:
        store.append(_make_event("review.validated"))
        store.append(_make_event("review.validated"))
        assert store.list_event_names() == ["review.validated"]


class TestQueryPage:
    def test_returns_rows_desc_with_no_cursor(self, store: AuditEventStore) -> None:
        base = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        for i in range(3):
            store.append(_make_event("review.validated", ts=base + timedelta(minutes=i)))

        rows, cursor = store.query_page(limit=10)
        assert len(rows) == 3
        assert cursor is None
        # DESC order.
        assert rows[0].ts_utc > rows[-1].ts_utc

    def test_filter_by_event_name(self, store: AuditEventStore) -> None:
        ts = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        store.append(_make_event("review.validated", ts=ts))
        store.append(_make_event("routing.decided", ts=ts))

        rows, _ = store.query_page(event_name="routing.decided", limit=10)
        assert {r.event_name for r in rows} == {"routing.decided"}

    def test_filter_by_actor(self, store: AuditEventStore) -> None:
        ts = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        store.append(_make_event("review.validated", ts=ts, actor="alice"))
        store.append(_make_event("review.validated", ts=ts, actor="bob"))
        store.append(_make_event("review.validated", ts=ts, actor=None))

        rows, _ = store.query_page(actor="alice", limit=10)
        assert {event_actor(r) for r in rows} == {"alice"}

    def test_filter_by_since_until(self, store: AuditEventStore) -> None:
        base = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        for i in range(5):
            store.append(_make_event("review.validated", ts=base + timedelta(hours=i)))

        rows, _ = store.query_page(
            since=base + timedelta(hours=1),
            until=base + timedelta(hours=3),
            limit=10,
        )
        assert len(rows) == 3

    def test_cursor_round_trip_no_overlap(self, store: AuditEventStore) -> None:
        base = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        for i in range(5):
            store.append(_make_event("review.validated", ts=base + timedelta(minutes=i)))

        first, cursor1 = store.query_page(limit=2)
        assert len(first) == 2
        assert cursor1 is not None

        second, cursor2 = store.query_page(limit=2, cursor=cursor1)
        assert len(second) == 2
        assert cursor2 is not None
        # No overlap between pages.
        first_names = {(r.event_name, r.ts_utc) for r in first}
        second_names = {(r.event_name, r.ts_utc) for r in second}
        assert first_names.isdisjoint(second_names)

        third, cursor3 = store.query_page(limit=2, cursor=cursor2)
        assert len(third) == 1
        assert cursor3 is None

    def test_invalid_cursor_raises_value_error(self, store: AuditEventStore) -> None:
        with pytest.raises(ValueError):
            store.query_page(cursor="not-a-real-cursor", limit=10)

    def test_limit_out_of_range_raises(self, store: AuditEventStore) -> None:
        with pytest.raises(ValueError):
            store.query_page(limit=0)
        with pytest.raises(ValueError):
            store.query_page(limit=10_000)


class TestEventActor:
    def test_returns_string_actor(self) -> None:
        event = _make_event("review.validated", actor="alice")
        assert event_actor(event) == "alice"

    def test_returns_none_when_actor_missing(self) -> None:
        event = AuditEvent(
            event_name="x",
            level="INFO",
            ts_utc=datetime.now(tz=UTC),
            document_id=None,
            version_id=None,
            payload={},
        )
        assert event_actor(event) is None

    def test_returns_none_when_actor_not_string(self) -> None:
        event = AuditEvent(
            event_name="x",
            level="INFO",
            ts_utc=datetime.now(tz=UTC),
            document_id=None,
            version_id=None,
            payload={"actor": 42},
        )
        assert event_actor(event) is None

    def test_returns_none_when_actor_empty_string(self) -> None:
        event = AuditEvent(
            event_name="x",
            level="INFO",
            ts_utc=datetime.now(tz=UTC),
            document_id=None,
            version_id=None,
            payload={"actor": ""},
        )
        assert event_actor(event) is None
