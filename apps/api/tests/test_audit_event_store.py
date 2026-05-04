"""Tests for the audit-event store + handler (#26 residual).

The store + handler are exercised against in-process fakes — no
filesystem touches in the default suite beyond the two SQLite tests
that use ``tmp_path`` so each run gets its own database.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.services.audit_event_store import (
    AUDIT_SCHEMA_VERSION,
    AuditEvent,
    InMemoryAuditEventStore,
    SQLiteAuditEventStore,
)
from app.services.audit_log_handler import AuditLogHandler

# ─── Store: in-memory ────────────────────────────────────────────────────


def _make_event(
    *,
    name: str = "document.uploaded",
    document_id: str | None = "doc-1",
    version_id: str | None = "ver-1",
    ts: datetime | None = None,
    payload: dict | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_name=name,
        level="INFO",
        ts_utc=ts or datetime.now(tz=UTC),
        document_id=document_id,
        version_id=version_id,
        payload=payload or {"document_id": document_id, "version_id": version_id},
    )


def test_in_memory_store_round_trip():
    store = InMemoryAuditEventStore()
    event = _make_event()
    store.append(event)

    [returned] = store.query()
    assert returned.event_name == event.event_name
    assert returned.document_id == "doc-1"
    assert returned.payload == event.payload


def test_in_memory_store_filters_by_event_name_and_document():
    store = InMemoryAuditEventStore()
    store.append(_make_event(name="document.uploaded", document_id="A"))
    store.append(_make_event(name="document.uploaded", document_id="B"))
    store.append(_make_event(name="extraction.completed", document_id="A"))

    by_name = store.query(event_name="document.uploaded")
    assert {e.document_id for e in by_name} == {"A", "B"}

    by_doc = store.query(document_id="A")
    assert {e.event_name for e in by_doc} == {"document.uploaded", "extraction.completed"}

    by_both = store.query(event_name="document.uploaded", document_id="A")
    assert len(by_both) == 1


def test_in_memory_store_orders_newest_first_and_caps_limit():
    store = InMemoryAuditEventStore()
    base = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
    for i in range(5):
        store.append(_make_event(ts=base + timedelta(minutes=i)))

    rows = store.query(limit=3)
    assert len(rows) == 3
    assert rows[0].ts_utc > rows[-1].ts_utc


def test_in_memory_store_filters_by_since():
    store = InMemoryAuditEventStore()
    base = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
    store.append(_make_event(ts=base))
    store.append(_make_event(ts=base + timedelta(hours=1)))

    rows = store.query(since=base + timedelta(minutes=30))
    assert len(rows) == 1


def test_in_memory_store_rejects_invalid_limit():
    store = InMemoryAuditEventStore()
    with pytest.raises(ValueError):
        store.query(limit=0)
    with pytest.raises(ValueError):
        store.query(limit=10_000)


# ─── Store: SQLite ───────────────────────────────────────────────────────


def test_sqlite_store_persists_across_instances(tmp_path):
    db_path = tmp_path / "audit.sqlite3"
    store_a = SQLiteAuditEventStore(db_path)
    store_a.append(_make_event(payload={"document_id": "doc-1", "bytes": 42}))
    store_a.close()

    store_b = SQLiteAuditEventStore(db_path)
    rows = store_b.query()
    assert len(rows) == 1
    assert rows[0].payload["bytes"] == 42
    store_b.close()


def test_sqlite_store_writes_schema_version_metadata(tmp_path):
    db_path = tmp_path / "audit.sqlite3"
    store = SQLiteAuditEventStore(db_path)
    cur = store._conn.cursor()  # type: ignore[attr-defined]
    row = cur.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    assert int(row[0]) == AUDIT_SCHEMA_VERSION
    store.close()


def test_sqlite_store_round_trip_with_filters(tmp_path):
    db_path = tmp_path / "audit.sqlite3"
    store = SQLiteAuditEventStore(db_path)
    base = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
    store.append(_make_event(name="document.uploaded", document_id="A", ts=base))
    store.append(
        _make_event(
            name="document.uploaded",
            document_id="B",
            ts=base + timedelta(minutes=1),
        )
    )
    store.append(
        _make_event(
            name="knowledge.chat.answered",
            document_id=None,
            version_id=None,
            ts=base + timedelta(minutes=2),
            payload={"mode": "rag", "vector_hits": 3},
        )
    )

    by_name = store.query(event_name="document.uploaded")
    assert {e.document_id for e in by_name} == {"A", "B"}

    chat_only = store.query(event_name="knowledge.chat.answered")
    assert chat_only[0].payload["mode"] == "rag"
    assert chat_only[0].document_id is None

    since_filter = store.query(since=base + timedelta(minutes=2))
    assert len(since_filter) == 1
    assert since_filter[0].event_name == "knowledge.chat.answered"

    store.close()


def test_sqlite_store_detects_schema_version_mismatch(tmp_path):
    """A pre-existing DB at a different schema version must raise."""
    import sqlite3

    db_path = tmp_path / "audit.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO schema_meta(key, value) VALUES ('schema_version', '999')")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="schema mismatch"):
        SQLiteAuditEventStore(db_path)


# ─── Logging handler ─────────────────────────────────────────────────────


def _emit(name: str, **extra) -> tuple[InMemoryAuditEventStore, AuditLogHandler]:
    """Helper: emit one log record through a fresh handler + store."""
    store = InMemoryAuditEventStore()
    handler = AuditLogHandler(store)
    logger = logging.getLogger("audit-test")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info(name, extra=extra)
    return store, handler


def test_handler_persists_dotted_event_records():
    store, _ = _emit(
        "document.uploaded",
        document_id="doc-1",
        version_id="ver-1",
        sha256="0" * 64,
        bytes=42,
    )
    [event] = store.query()
    assert event.event_name == "document.uploaded"
    assert event.document_id == "doc-1"
    assert event.version_id == "ver-1"
    assert event.payload["sha256"] == "0" * 64
    assert event.payload["bytes"] == 42
    assert event.level == "INFO"


def test_handler_ignores_plain_prose_log_lines():
    """Records whose msg isn't a dotted name are not persisted."""
    store, _ = _emit("Starting up")
    assert store.query() == []
    store, _ = _emit("Single")  # no dot
    assert store.query() == []
    store, _ = _emit("UPPERCASE.event")  # not lowercase-snake
    assert store.query() == []


def test_handler_extracts_payload_excluding_logging_internals():
    """The payload must contain only what the emitter passed via extra."""
    store, _ = _emit(
        "extraction.completed",
        document_id="doc-1",
        version_id="ver-1",
        section_count=5,
    )
    [event] = store.query()
    # Reserved logging attributes (``levelname``, ``pathname``, …) must
    # not bleed into the persisted payload.
    assert "levelname" not in event.payload
    assert "pathname" not in event.payload
    assert "filename" not in event.payload
    assert event.payload.keys() >= {"document_id", "version_id", "section_count"}


def test_handler_isolates_store_failures():
    """A store hiccup does not propagate into the calling thread."""

    class ExplodingStore:
        name = "exploding"

        def append(self, event: AuditEvent) -> None:
            raise RuntimeError("disk full")

        def query(self, **kwargs):
            return []

    handler = AuditLogHandler(ExplodingStore())  # type: ignore[arg-type]
    logger = logging.getLogger("audit-isolation")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    # Must not raise even though the store explodes.
    logger.info("document.uploaded", extra={"document_id": "doc-1"})


def test_handler_records_warning_level_events():
    store = InMemoryAuditEventStore()
    handler = AuditLogHandler(store)
    logger = logging.getLogger("audit-warnings")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.warning("knowledge.chat.unresolved_citation", extra={"unresolved_markers": ["[c-fake]"]})
    [event] = store.query()
    assert event.level == "WARNING"
    assert event.payload["unresolved_markers"] == ["[c-fake]"]


# ─── _build_audit_store factory wiring ───────────────────────────────────


def test_build_audit_store_off_by_default(monkeypatch):
    from app.dependencies import _build_audit_store
    from app.settings import Settings

    monkeypatch.delenv("KW_AUDIT_ENABLED", raising=False)
    store = _build_audit_store(Settings())
    assert isinstance(store, InMemoryAuditEventStore)


def test_build_audit_store_uses_explicit_path(monkeypatch, tmp_path):
    from app.dependencies import _build_audit_store
    from app.settings import Settings

    monkeypatch.setenv("KW_AUDIT_ENABLED", "true")
    monkeypatch.setenv("KW_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite3"))
    store = _build_audit_store(Settings())
    try:
        assert isinstance(store, SQLiteAuditEventStore)
    finally:
        store.close()  # type: ignore[union-attr]


def test_build_audit_store_uses_default_dir_when_no_path(monkeypatch, tmp_path):
    from app.dependencies import _build_audit_store
    from app.settings import Settings

    monkeypatch.setenv("KW_AUDIT_ENABLED", "1")
    monkeypatch.delenv("KW_AUDIT_DB_PATH", raising=False)
    store = _build_audit_store(Settings(), default_dir=tmp_path)
    try:
        assert isinstance(store, SQLiteAuditEventStore)
        assert (tmp_path / "audit.sqlite3").exists()
    finally:
        store.close()  # type: ignore[union-attr]


def test_build_audit_store_falls_back_to_memory_when_no_path_and_no_dir(monkeypatch):
    """Truthy flag from in-memory factory ⇒ no path resolvable ⇒ in-memory fallback."""
    from app.dependencies import _build_audit_store
    from app.settings import Settings

    monkeypatch.setenv("KW_AUDIT_ENABLED", "true")
    monkeypatch.delenv("KW_AUDIT_DB_PATH", raising=False)
    store = _build_audit_store(Settings())
    assert isinstance(store, InMemoryAuditEventStore)


# ─── _jsonable_default coercion ──────────────────────────────────────────


def test_jsonable_default_handles_datetimes_paths_and_other_types():
    """Non-JSON-clean payload values are coerced rather than dropped."""
    from pathlib import Path

    from app.services.audit_event_store import _jsonable_default

    ts = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
    assert _jsonable_default(ts) == "2026-05-04T10:00:00+00:00"
    assert _jsonable_default(Path("/tmp/foo")) == "/tmp/foo"
    # Anything else falls through to ``repr`` so the JSON encoder
    # never raises on the audit handler's hot path.
    assert "MyObj" in _jsonable_default(type("MyObj", (), {"__repr__": lambda self: "MyObj()"})())


def test_sqlite_store_persists_payload_with_non_jsonable_values(tmp_path):
    """End-to-end check that ``_jsonable_default`` lets odd payloads through."""
    from pathlib import Path

    db_path = tmp_path / "audit.sqlite3"
    store = SQLiteAuditEventStore(db_path)
    odd = AuditEvent(
        event_name="odd.event",
        level="INFO",
        ts_utc=datetime.now(tz=UTC),
        document_id=None,
        version_id=None,
        payload={"path": Path("/tmp/foo"), "when": datetime(2026, 5, 4, tzinfo=UTC)},
    )
    store.append(odd)
    [returned] = store.query()
    # Both fields were coerced to strings on the way in.
    assert returned.payload["path"] == "/tmp/foo"
    assert returned.payload["when"] == "2026-05-04T00:00:00+00:00"
    store.close()
