"""Append-only audit event store (#26 residual).

Persists the structured-logging event vocabulary documented in
``docs/architecture/observability.md`` to a SQLite table so operator
questions ("who validated doc X on what date?", "how many chat
queries fired the empty-retrieval short-circuit yesterday?") become
SQL queries rather than log scrapes.

Two implementations, behind a small Protocol:

- :class:`InMemoryAuditEventStore` — list-backed; default for tests.
- :class:`SQLiteAuditEventStore` — production. One thread-safe
  connection, one table, two indexes, one schema version.

Tamper-evidence (signed event chain) and retention purges are out of
scope for the foundation slice — this is the persistence layer the
governance arc will build on. The ``query`` shape is a narrow filter
surface today (`event_name`, `document_id`, `version_id`, `since`,
``limit``); a richer query API can land alongside the admin route.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# Bumped when the table schema changes. The ``schema_meta`` table
# holds the current value so a startup mismatch can be detected and
# migrated explicitly rather than silently corrupting data.
AUDIT_SCHEMA_VERSION = 1

# Default ceiling on a single :meth:`AuditEventStore.query` page. Caps
# the answer size for any future admin route so a malformed query
# can't drag back the entire table.
DEFAULT_QUERY_LIMIT = 200
MAX_QUERY_LIMIT = 1000


@dataclass(frozen=True)
class AuditEvent:
    """One persisted audit row.

    ``payload`` is the full ``extra`` dict the emitter passed to
    ``log.info(...)``, so consumers don't lose any field even when
    the indexed columns (``document_id`` / ``version_id``) aren't
    populated. ``ts_utc`` is always UTC ISO-8601 with seconds
    resolution; finer timing isn't useful for human audit answers.
    """

    event_name: str
    level: str
    ts_utc: datetime
    document_id: str | None
    version_id: str | None
    payload: dict[str, Any]


@runtime_checkable
class AuditEventStore(Protocol):
    """Append-only persistence boundary for the structured event log.

    Implementations MUST be safe under concurrent access from FastAPI's
    thread-pool — the audit handler attaches to the root logger and is
    invoked from every worker thread.
    """

    name: str

    def append(self, event: AuditEvent) -> None:
        """Persist one event. Append-only; never overwrites or deletes."""

    def query(
        self,
        *,
        event_name: str | None = None,
        document_id: str | None = None,
        version_id: str | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> list[AuditEvent]:
        """Return matching events, newest first, capped at ``limit``."""


class InMemoryAuditEventStore:
    """Deterministic in-process store for unit tests.

    Backed by a plain list under a coarse lock. Not suitable for
    production traffic (no durability, no cross-process visibility),
    but cheap enough that the default test suite can attach the audit
    handler without spinning up SQLite.
    """

    name: str = "in-memory"

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.RLock()

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def query(
        self,
        *,
        event_name: str | None = None,
        document_id: str | None = None,
        version_id: str | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> list[AuditEvent]:
        if limit < 1 or limit > MAX_QUERY_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_QUERY_LIMIT}; got {limit}.")
        with self._lock:
            matched: list[AuditEvent] = []
            for event in self._events:
                if event_name is not None and event.event_name != event_name:
                    continue
                if document_id is not None and event.document_id != document_id:
                    continue
                if version_id is not None and event.version_id != version_id:
                    continue
                if since is not None and event.ts_utc < since:
                    continue
                matched.append(event)
            matched.sort(key=lambda e: e.ts_utc, reverse=True)
            return matched[:limit]


class SQLiteAuditEventStore:
    """SQLite-backed persistence for the audit event vocabulary.

    One thread-safe connection per store instance. ``check_same_thread``
    is False because FastAPI dispatches handlers across the thread pool
    and the audit handler runs synchronously on the calling thread —
    each `append` is wrapped in a coarse lock so SQLite's write
    serialization holds.

    Schema is applied at construction; if an existing database has a
    different ``AUDIT_SCHEMA_VERSION`` the constructor raises so the
    operator notices the mismatch instead of silently mixing old and
    new columns.
    """

    name: str = "sqlite"

    def __init__(self, db_path: Path | str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; explicit transactions unnecessary for append-only.
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (  key TEXT PRIMARY KEY,  value TEXT NOT NULL)"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS audit_events ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  event_name TEXT NOT NULL,"
            "  level TEXT NOT NULL,"
            "  ts_utc TEXT NOT NULL,"
            "  document_id TEXT,"
            "  version_id TEXT,"
            "  payload_json TEXT NOT NULL"
            ")"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_event_name "
            "ON audit_events(event_name, ts_utc DESC)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_document "
            "ON audit_events(document_id, version_id, ts_utc DESC)"
        )
        cur.execute(
            "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
            (str(AUDIT_SCHEMA_VERSION),),
        )
        # Mismatch ⇒ explicit error. Migration is a separate concern.
        row = cur.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        if row is not None and int(row[0]) != AUDIT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Audit schema mismatch at {self._path}: "
                f"expected {AUDIT_SCHEMA_VERSION}, found {row[0]}. "
                "Migrate the database manually or point KW_AUDIT_DB_PATH "
                "at a fresh file."
            )

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_events("
                "  event_name, level, ts_utc, document_id, version_id, payload_json"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.event_name,
                    event.level,
                    event.ts_utc.astimezone(UTC).isoformat(timespec="seconds"),
                    event.document_id,
                    event.version_id,
                    json.dumps(event.payload, default=_jsonable_default),
                ),
            )

    def query(
        self,
        *,
        event_name: str | None = None,
        document_id: str | None = None,
        version_id: str | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> list[AuditEvent]:
        if limit < 1 or limit > MAX_QUERY_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_QUERY_LIMIT}; got {limit}.")
        clauses: list[str] = []
        params: list[Any] = []
        if event_name is not None:
            clauses.append("event_name = ?")
            params.append(event_name)
        if document_id is not None:
            clauses.append("document_id = ?")
            params.append(document_id)
        if version_id is not None:
            clauses.append("version_id = ?")
            params.append(version_id)
        if since is not None:
            clauses.append("ts_utc >= ?")
            params.append(since.astimezone(UTC).isoformat(timespec="seconds"))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_name, level, ts_utc, document_id, version_id, payload_json"
                f" FROM audit_events{where}"
                " ORDER BY ts_utc DESC, id DESC"
                " LIMIT ?",
                params,
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_event(row: Iterable[Any]) -> AuditEvent:
    name, level, ts_utc, document_id, version_id, payload_json = row
    return AuditEvent(
        event_name=name,
        level=level,
        ts_utc=datetime.fromisoformat(ts_utc),
        document_id=document_id,
        version_id=version_id,
        payload=json.loads(payload_json) if payload_json else {},
    )


def _jsonable_default(value: Any) -> Any:
    """Best-effort coercion for JSON-encoding the audit payload.

    The structured-logging emitters generally pass JSON-clean dicts —
    str / int / float / bool / list / dict — but occasionally a
    timestamp or path slips in. We coerce to ``str`` rather than
    raise so the audit handler never drops an event over a stray
    non-JSON-clean field.
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="seconds")
    if isinstance(value, Path):
        return str(value)
    return repr(value)


__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "AuditEvent",
    "AuditEventStore",
    "DEFAULT_QUERY_LIMIT",
    "InMemoryAuditEventStore",
    "MAX_QUERY_LIMIT",
    "SQLiteAuditEventStore",
]
