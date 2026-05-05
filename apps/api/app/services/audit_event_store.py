"""Append-only audit event store (#26 residual).

The :meth:`AuditEventStore.query_page` surface (added for the
admin audit log viewer, #206 follow-up) layers a cursor-paginated
walk on top of the same append-only table. Cursor codec is opaque
to callers — both impls use a small JSON envelope encoded as
URL-safe base64 so the wire shape is filter-aware (a cursor cannot
leak rows from a different filter combination).

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
``limit``); the richer ``query_page`` surface — paginated, with the
``actor`` projection + ``until`` upper-bound the admin viewer reads —
sits beside it without disturbing existing ``query`` callers.
"""

from __future__ import annotations

import base64
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

    def query_page(
        self,
        *,
        event_name: str | None = None,
        actor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> tuple[list[AuditEvent], str | None]:
        """Cursor-paginated walk for the admin audit log viewer (#206 follow-up).

        Returns a ``(rows, next_cursor)`` tuple — ``next_cursor`` is
        opaque (the impl picks the codec) and ``None`` when this page
        is the last one. Pass it back as ``cursor`` on the next call
        to continue. ``actor`` filters on the ``actor`` value
        projected out of the event payload (the audit emitters stash
        ``actor`` in ``payload['actor']``); rows with no actor are
        excluded only when ``actor is not None``.

        Newest-first ordering: rows sort by ``ts_utc DESC`` with a
        stable tiebreaker so a same-timestamp tie doesn't drift across
        pages.
        """

    def list_event_names(self) -> list[str]:
        """Return the distinct event names persisted in the store.

        Surfaces the audit vocabulary the operator UI's filter
        dropdown needs without forcing it to mirror the doc-side
        constants. Sorted lexicographically so the dropdown render
        order is deterministic across calls.
        """


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

    def query_page(
        self,
        *,
        event_name: str | None = None,
        actor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> tuple[list[AuditEvent], str | None]:
        if limit < 1 or limit > MAX_QUERY_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_QUERY_LIMIT}; got {limit}.")
        cursor_ts, cursor_offset = _decode_audit_cursor(cursor)
        with self._lock:
            # Stable order: ts_utc DESC, then insertion-order DESC for
            # ties so a same-ts run doesn't drift between pages.
            indexed = list(enumerate(self._events))
            indexed.sort(key=lambda pair: (pair[1].ts_utc, pair[0]), reverse=True)
            matched: list[AuditEvent] = []
            for _idx, event in indexed:
                if event_name is not None and event.event_name != event_name:
                    continue
                if actor is not None and event_actor(event) != actor:
                    continue
                if since is not None and event.ts_utc < since:
                    continue
                if until is not None and event.ts_utc > until:
                    continue
                if cursor_ts is not None and event.ts_utc > cursor_ts:
                    # The cursor records the boundary timestamp of the
                    # previous page's last row; everything strictly
                    # newer was already returned. Equal-ts rows are
                    # disambiguated via the offset below.
                    continue
                matched.append(event)
            # Drop the prefix the cursor already consumed (rows at the
            # boundary timestamp returned in the previous page).
            sliced = matched[cursor_offset:]
            page = sliced[:limit]
            next_cursor: str | None = None
            if len(sliced) > limit:
                # New cursor: anchor on the last row's ts and the
                # number of rows at that ts already emitted on this
                # page (so a same-ts run paginates correctly).
                last_ts = page[-1].ts_utc
                same_ts_count = sum(1 for ev in page if ev.ts_utc == last_ts)
                # Carry over the prior offset when the boundary
                # straddled the previous page.
                prior_carry = cursor_offset if cursor_ts is not None and cursor_ts == last_ts else 0
                next_cursor = _encode_audit_cursor(last_ts, prior_carry + same_ts_count)
            return page, next_cursor

    def list_event_names(self) -> list[str]:
        with self._lock:
            return sorted({event.event_name for event in self._events})


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

    def query_page(
        self,
        *,
        event_name: str | None = None,
        actor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> tuple[list[AuditEvent], str | None]:
        if limit < 1 or limit > MAX_QUERY_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_QUERY_LIMIT}; got {limit}.")
        cursor_id = _decode_audit_cursor_id(cursor)
        clauses: list[str] = []
        params: list[Any] = []
        if event_name is not None:
            clauses.append("event_name = ?")
            params.append(event_name)
        if since is not None:
            clauses.append("ts_utc >= ?")
            params.append(since.astimezone(UTC).isoformat(timespec="seconds"))
        if until is not None:
            clauses.append("ts_utc <= ?")
            params.append(until.astimezone(UTC).isoformat(timespec="seconds"))
        if cursor_id is not None:
            # Cursor anchors on the row id (the AUTOINCREMENT primary
            # key), which is monotonic with insertion order. Combined
            # with the ``ORDER BY ts_utc DESC, id DESC`` ordering the
            # next page is everything with a strictly-smaller id at
            # the same-or-older timestamp — i.e. ``id < cursor_id``.
            clauses.append("id < ?")
            params.append(cursor_id)
        # Actor lives in the payload JSON. SQLite's json_extract scans
        # the column but the audit table is small enough that a full
        # scan is acceptable — the alternative is a denormalised
        # ``actor`` column and a schema migration.
        if actor is not None:
            clauses.append("json_extract(payload_json, '$.actor') = ?")
            params.append(actor)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        # Fetch ``limit + 1`` so we can detect a follow-up page without
        # a second COUNT query.
        params.append(limit + 1)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, event_name, level, ts_utc, document_id, version_id, payload_json"
                f" FROM audit_events{where}"
                " ORDER BY ts_utc DESC, id DESC"
                " LIMIT ?",
                params,
            ).fetchall()
        if len(rows) > limit:
            tail = rows[limit - 1]  # last row that is *included* in this page.
            next_cursor: str | None = _encode_audit_cursor_id(int(tail[0]))
            rows = rows[:limit]
        else:
            next_cursor = None
        return [_row_to_event(row[1:]) for row in rows], next_cursor

    def list_event_names(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT event_name FROM audit_events ORDER BY event_name"
            ).fetchall()
        return [row[0] for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def event_actor(event: AuditEvent) -> str | None:
    """Project the ``actor`` field out of an event's payload.

    Audit emitters stash the acting principal under ``payload['actor']``
    (e.g. the admin route writes ``actor=user.id`` on its
    ``log.info(...)`` extras). The value is only useful when it's a
    non-empty string; everything else collapses to ``None`` so the
    UI's actor filter doesn't accidentally match an integer id or a
    bool flag.
    """
    candidate = event.payload.get("actor") if event.payload else None
    if isinstance(candidate, str) and candidate:
        return candidate
    return None


def _encode_audit_cursor(boundary_ts: datetime, offset: int) -> str:
    """Encode the in-memory cursor (boundary timestamp + offset).

    The in-memory store has no monotonic id so we anchor on the row's
    timestamp plus the count of equal-ts rows already emitted. The
    JSON envelope is base64url'd so it can be passed verbatim through
    a query-string without escaping. Callers MUST treat it as opaque.
    """
    payload = json.dumps(
        {"ts": boundary_ts.astimezone(UTC).isoformat(), "offset": int(offset)}
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_audit_cursor(cursor: str | None) -> tuple[datetime | None, int]:
    """Decode the in-memory cursor; ``(None, 0)`` for the first page."""
    if cursor is None:
        return None, 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        ts = datetime.fromisoformat(str(payload["ts"]))
        offset = int(payload.get("offset", 0))
        if offset < 0:
            raise ValueError("offset must be non-negative")
        return ts, offset
    except Exception as exc:  # noqa: BLE001 — surface as a deterministic ValueError.
        raise ValueError(f"Invalid audit cursor: {cursor!r}") from exc


def _encode_audit_cursor_id(row_id: int) -> str:
    """Encode the SQLite cursor (the row's PK)."""
    payload = json.dumps({"id": int(row_id)}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_audit_cursor_id(cursor: str | None) -> int | None:
    """Decode the SQLite cursor; ``None`` for the first page."""
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        return int(payload["id"])
    except Exception as exc:  # noqa: BLE001 — deterministic ValueError.
        raise ValueError(f"Invalid audit cursor: {cursor!r}") from exc


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
    "event_actor",
]
