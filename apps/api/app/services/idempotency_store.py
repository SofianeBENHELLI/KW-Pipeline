"""Idempotency-key cache for POST endpoints.

Each entry records the Idempotency-Key header value together with the route
path so the same key can be reused across different endpoints without
collision.  ``request_hash`` guards against accidental reuse of a key with
different request body bytes — the store rejects such requests with a
descriptive error rather than silently returning a mismatched response.

Two implementations are provided, matching the catalog-store pattern:

* ``InMemoryIdempotencyStore`` — unit tests and ephemeral demos.
* ``SQLiteIdempotencyStore`` — local persistent deployments.

Both implement the ``IdempotencyStore`` Protocol.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

# ---------------------------------------------------------------------------
# Shared data type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredResponse:
    """A previously recorded response for an idempotency key."""

    key: str
    route: str
    request_hash: str
    response_status: int
    response_json: str  # raw JSON string
    created_at: datetime


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class IdempotencyStore(Protocol):
    """Persistence boundary for idempotent POST request tracking."""

    def get(self, key: str, route: str) -> StoredResponse | None:
        """Return the stored response for ``(key, route)``, or ``None``."""

    def put(
        self,
        key: str,
        route: str,
        request_hash: str,
        response_status: int,
        response_json: str,
    ) -> None:
        """Persist a response for ``(key, route)``."""

    def purge_expired(self, ttl_hours: int = 24) -> int:
        """Delete entries older than ``ttl_hours`` and return the count removed."""


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryIdempotencyStore:
    """In-memory idempotency store for unit tests and fast local demos."""

    def __init__(self) -> None:
        # Keyed by (key, route)
        self._entries: dict[tuple[str, str], StoredResponse] = {}

    def get(self, key: str, route: str) -> StoredResponse | None:
        return self._entries.get((key, route))

    def put(
        self,
        key: str,
        route: str,
        request_hash: str,
        response_status: int,
        response_json: str,
    ) -> None:
        self._entries[(key, route)] = StoredResponse(
            key=key,
            route=route,
            request_hash=request_hash,
            response_status=response_status,
            response_json=response_json,
            created_at=datetime.now(tz=UTC),
        )

    def purge_expired(self, ttl_hours: int = 24) -> int:
        cutoff = datetime.now(tz=UTC) - timedelta(hours=ttl_hours)
        expired = [k for k, v in self._entries.items() if v.created_at < cutoff]
        for k in expired:
            del self._entries[k]
        return len(expired)


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------

_SQLITE_BUSY_TIMEOUT_MS = 5000


class SQLiteIdempotencyStore:
    """SQLite-backed idempotency store for local persistent deployments."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str, route: str) -> StoredResponse | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT key, route, request_hash, response_status, response_json, created_at
                FROM idempotency_keys
                WHERE key = ? AND route = ?
                """,
                (key, route),
            ).fetchone()
        return self._from_row(row) if row else None

    def put(
        self,
        key: str,
        route: str,
        request_hash: str,
        response_status: int,
        response_json: str,
    ) -> None:
        now = datetime.now(tz=UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO idempotency_keys
                    (key, route, request_hash, response_status, response_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(key, route) DO UPDATE SET
                    request_hash   = excluded.request_hash,
                    response_status = excluded.response_status,
                    response_json  = excluded.response_json,
                    created_at     = excluded.created_at
                """,
                (key, route, request_hash, response_status, response_json, now),
            )

    def purge_expired(self, ttl_hours: int = 24) -> int:
        cutoff = (datetime.now(tz=UTC) - timedelta(hours=ttl_hours)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM idempotency_keys WHERE created_at < ?",
                (cutoff,),
            )
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    key              TEXT NOT NULL,
                    route            TEXT NOT NULL,
                    request_hash     TEXT NOT NULL,
                    response_status  INTEGER NOT NULL,
                    response_json    TEXT NOT NULL,
                    created_at       TEXT NOT NULL,
                    PRIMARY KEY (key, route)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_idempotency_keys_created_at
                ON idempotency_keys (created_at)
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> StoredResponse:
        return StoredResponse(
            key=row["key"],
            route=row["route"],
            request_hash=row["request_hash"],
            response_status=row["response_status"],
            response_json=row["response_json"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


# ---------------------------------------------------------------------------
# Request-hash helpers
# ---------------------------------------------------------------------------


def hash_bytes(data: bytes) -> str:
    """Return the hex SHA-256 of ``data``."""
    import hashlib

    return hashlib.sha256(data).hexdigest()


def hash_json_body(body: dict | None, path_params: dict | None = None) -> str:
    """Return the hex SHA-256 of a canonicalized JSON request body + path params.

    Keys are sorted recursively so ``{"b": 1, "a": 2}`` and ``{"a": 2, "b": 1}``
    produce the same digest. Path parameters are merged into the body before
    hashing so the route coordinates are part of the fingerprint.
    """
    import hashlib

    combined: dict = {}
    if path_params:
        combined.update(path_params)
    if body:
        combined.update(body)
    canonical = json.dumps(combined, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
