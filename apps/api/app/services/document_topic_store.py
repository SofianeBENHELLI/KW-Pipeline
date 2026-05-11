"""SQLite-backed storage for the LLM-extracted document-topic data
model (#411, ADR-031).

Per ADR-031, document topics live in SQLite — they are governance /
audit data ("what themes the LLM identified for this validated
version, with provenance back to the chunks"), not graph traversal
data. The store is the write surface the future
:class:`~app.services.topic_extractor.TopicExtractor` calls into; the
read surface is :func:`list_for_document` and :func:`list_all`, which
power ``GET /knowledge/topics?document_id=…`` and the cross-document
variant.

Two storage shapes:

* :class:`InMemoryDocumentTopicStore` for tests and the in-process
  demo.
* :class:`SQLiteDocumentTopicStore` for the persistent runtime.

Both expose the same :class:`DocumentTopicStore` Protocol so call
sites (future extractor wiring, the read route) don't care which
backend is active.

Cascade contract: ``delete_for_version`` returns the count of rows
removed for ``version_id``. The SQLite implementation walks the
``idx_document_topics_version_id`` index added by migration
``0014_document_topics``; the in-memory implementation is a list
filter. Both are idempotent.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

from app.schemas.document_topic import DocumentTopic
from app.services.catalog_store import (
    InvalidCursor,
    _decode_cursor,
    _encode_cursor,
)

# Default page size for ``list_all``. Matches the catalog read paths
# so a client switching between the two doesn't have to re-tune
# ``limit``.
DEFAULT_TOPICS_PAGE_LIMIT: Final[int] = 50

# Hard ceiling on a single page. Keeps the SQLite read bounded and
# the JSON envelope small enough for the Explorer to render in one
# pass; clients that need more walk the cursor.
MAX_TOPICS_PAGE_LIMIT: Final[int] = 200


class DocumentTopicStore(Protocol):
    """Persistence boundary for LLM-extracted document-topic rows."""

    def save_topics(self, topics: list[DocumentTopic]) -> None:
        """Persist a batch of document topics.

        ``extracted_at`` is set server-side on each topic before
        write — callers hand in topics without a timestamp and the
        store stamps them with ``datetime.now(UTC)`` so the wire
        always carries a server-authoritative value.

        Empty input is a no-op (the future extractor may emit zero
        topics for a given pass and the store should not error).
        """

    def list_for_document(
        self,
        document_id: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_TOPICS_PAGE_LIMIT,
    ) -> tuple[list[DocumentTopic], str | None]:
        """Return topics for ``document_id``, paginated.

        Returns a ``(items, next_cursor)`` tuple. ``next_cursor`` is
        ``None`` when no more rows exist behind the current page.
        Sorted by ``(extracted_at ASC, id ASC)`` — the ``id``
        tie-breaker keeps two same-instant inserts from shifting
        between pages.

        Raises :class:`InvalidCursor` when ``cursor`` cannot be
        decoded (route layer maps to HTTP 400 with the message in
        ``detail``).
        """

    def list_all(
        self,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_TOPICS_PAGE_LIMIT,
    ) -> tuple[list[DocumentTopic], str | None]:
        """Return every topic in the store, paginated.

        Same shape as :meth:`list_for_document` but without the
        document filter. Used by the cross-document
        ``GET /knowledge/topics`` route (Atlas / corpus-wide
        overview).
        """

    def delete_for_version(self, version_id: str) -> int:
        """Remove every topic sourced from ``version_id``.

        Returns the count of rows removed. Idempotent: calling
        twice for the same version_id returns 0 on the second call.
        Used by the future cascade-deletion wiring (re-extract
        flow + version archive).
        """


class InMemoryDocumentTopicStore:
    """In-memory store — list of topics; Protocol parity with SQLite."""

    def __init__(self) -> None:
        self._topics: list[DocumentTopic] = []

    def save_topics(self, topics: list[DocumentTopic]) -> None:
        if not topics:
            return
        now = datetime.now(UTC)
        for topic in topics:
            # Stamp every save with a fresh server-authoritative
            # timestamp so two saves of "the same" topic get distinct
            # ``extracted_at`` values for cursor pagination.
            self._topics.append(topic.model_copy(update={"extracted_at": now}))

    def list_for_document(
        self,
        document_id: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_TOPICS_PAGE_LIMIT,
    ) -> tuple[list[DocumentTopic], str | None]:
        candidates = [t for t in self._topics if t.document_id == document_id]
        return self._paginate(candidates, cursor=cursor, limit=limit)

    def list_all(
        self,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_TOPICS_PAGE_LIMIT,
    ) -> tuple[list[DocumentTopic], str | None]:
        return self._paginate(list(self._topics), cursor=cursor, limit=limit)

    def delete_for_version(self, version_id: str) -> int:
        before = len(self._topics)
        self._topics = [t for t in self._topics if t.version_id != version_id]
        return before - len(self._topics)

    @staticmethod
    def _paginate(
        candidates: list[DocumentTopic],
        *,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[DocumentTopic], str | None]:
        # Stable sort matches the SQLite ORDER BY so both implementations
        # return the same page for the same input.
        candidates.sort(key=lambda t: (t.extracted_at, t.id))
        if cursor is not None:
            cutoff_extracted_at, cutoff_id = _decode_cursor(cursor)
            candidates = [
                t for t in candidates if (t.extracted_at, t.id) > (cutoff_extracted_at, cutoff_id)
            ]
        page = candidates[:limit]
        next_cursor: str | None = None
        if len(page) >= limit and len(candidates) > limit:
            tail = page[-1]
            next_cursor = _encode_cursor((tail.extracted_at, tail.id))
        return page, next_cursor


class SQLiteDocumentTopicStore:
    """SQLite-backed document-topic store. Migration ``0014_document_topics``
    creates the schema this class writes against."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        # Match the catalog store's PRAGMA posture (FK enforcement
        # so the FK on ``version_id`` actually fires when a
        # document_versions row is removed).
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def save_topics(self, topics: list[DocumentTopic]) -> None:
        if not topics:
            return
        now = datetime.now(UTC)
        rows = [_topic_to_row(t, extracted_at=now) for t in topics]
        with self._connect() as connection:
            try:
                connection.execute("BEGIN")
                connection.executemany(
                    """
                    INSERT INTO document_topics (
                        id,
                        document_id,
                        version_id,
                        label,
                        summary,
                        keywords_json,
                        confidence,
                        schema_version,
                        extracted_at,
                        supporting_chunk_ids_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def list_for_document(
        self,
        document_id: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_TOPICS_PAGE_LIMIT,
    ) -> tuple[list[DocumentTopic], str | None]:
        return self._query(
            where_sql="WHERE document_id = ?",
            where_params=[document_id],
            cursor=cursor,
            limit=limit,
        )

    def list_all(
        self,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_TOPICS_PAGE_LIMIT,
    ) -> tuple[list[DocumentTopic], str | None]:
        return self._query(where_sql="", where_params=[], cursor=cursor, limit=limit)

    def delete_for_version(self, version_id: str) -> int:
        with self._connect() as connection:
            cursor_obj = connection.execute(
                "DELETE FROM document_topics WHERE version_id = ?",
                (version_id,),
            )
            return cursor_obj.rowcount

    def _query(
        self,
        *,
        where_sql: str,
        where_params: list[object],
        cursor: str | None,
        limit: int,
    ) -> tuple[list[DocumentTopic], str | None]:
        cutoff_extracted_at: datetime | None = None
        cutoff_id: str | None = None
        if cursor is not None:
            cutoff_extracted_at, cutoff_id = _decode_cursor(cursor)
        # Fetch limit+1 so we can tell whether a "next" page exists
        # without a separate COUNT query.
        sql = (
            "SELECT id, document_id, version_id, label, summary, keywords_json, "
            "confidence, schema_version, extracted_at, supporting_chunk_ids_json "
            "FROM document_topics "
        ) + where_sql
        params: list[object] = list(where_params)
        if cutoff_extracted_at is not None and cutoff_id is not None:
            connector = " AND " if where_sql else " WHERE "
            sql += f"{connector}(extracted_at, id) > (?, ?)"
            params.extend([cutoff_extracted_at.isoformat(), cutoff_id])
        sql += " ORDER BY extracted_at ASC, id ASC LIMIT ?"
        params.append(limit + 1)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        page_rows = rows[:limit]
        items = [_row_to_topic(row) for row in page_rows]
        next_cursor: str | None = None
        if len(rows) > limit and items:
            tail = items[-1]
            next_cursor = _encode_cursor((tail.extracted_at, tail.id))
        return items, next_cursor


# ─── Internal helpers ───────────────────────────────────────────────


def _topic_to_row(topic: DocumentTopic, *, extracted_at: datetime) -> tuple[object, ...]:
    """Project a :class:`DocumentTopic` into the SQLite tuple shape.

    ``extracted_at`` is supplied by the caller (rather than read off
    the topic) so :meth:`SQLiteDocumentTopicStore.save_topics` can
    stamp the whole batch with a single now-timestamp — matches the
    in-memory store's behaviour.
    """
    return (
        topic.id,
        topic.document_id,
        topic.version_id,
        topic.label,
        topic.summary,
        json.dumps(list(topic.keywords)),
        topic.confidence,
        topic.schema_version,
        extracted_at.isoformat(),
        json.dumps(list(topic.supporting_chunk_ids)),
    )


def _row_to_topic(row: sqlite3.Row) -> DocumentTopic:
    """Re-build a :class:`DocumentTopic` from a SQLite row.

    Defensive against a stale ``schema_version`` value persisted by a
    future v0.2 store: the Pydantic model rejects anything but the
    current ``Literal`` set, so a mixed-version DB raises at the read
    boundary rather than silently flowing v0.2 rows to v0.1 readers.
    """
    keywords = _parse_string_list(row["keywords_json"], field="keywords_json")
    chunk_ids = _parse_string_list(
        row["supporting_chunk_ids_json"],
        field="supporting_chunk_ids_json",
    )
    return DocumentTopic(
        id=row["id"],
        document_id=row["document_id"],
        version_id=row["version_id"],
        label=row["label"],
        summary=row["summary"],
        keywords=keywords,
        confidence=row["confidence"],
        schema_version=row["schema_version"],
        extracted_at=datetime.fromisoformat(row["extracted_at"]),
        supporting_chunk_ids=chunk_ids,
    )


def _parse_string_list(raw: str, *, field: str) -> list[str]:
    """Parse a JSON-encoded ``list[str]`` from a SQLite column.

    Returns the list of strings; defensive against a malformed cell
    (raises :class:`ValueError` rather than silently returning an
    empty list, so a corrupt row is loud).
    """
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} is not valid JSON: {exc}") from exc
    if not isinstance(decoded, list):
        raise ValueError(f"{field} must decode to a JSON list.")
    if not all(isinstance(item, str) for item in decoded):
        raise ValueError(f"{field} items must all be strings.")
    return list(decoded)


__all__ = [
    "DEFAULT_TOPICS_PAGE_LIMIT",
    "MAX_TOPICS_PAGE_LIMIT",
    "DocumentTopicStore",
    "InMemoryDocumentTopicStore",
    "InvalidCursor",
    "SQLiteDocumentTopicStore",
]
