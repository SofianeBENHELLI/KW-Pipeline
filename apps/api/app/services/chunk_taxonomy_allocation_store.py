"""SQLite-backed storage for LLM business-taxonomy chunk allocations
(EPIC-1 slice 1.3, issue #340).

Per ADR-031, chunk-level allocations live in SQLite — they are
governance / audit data ("which categories did the LLM map this
chunk to, with provenance back to the source concepts and a model /
taxonomy / prompt fingerprint"), not graph traversal data. The
store is the write surface
:class:`~app.services.business_taxonomy_allocator.BusinessTaxonomyAllocator`
calls into; the read surface is :func:`list_for_document`,
:func:`list_for_chunk`, and :func:`list_all`, which power
``GET /knowledge/taxonomy-allocations``.

Two storage shapes:

* :class:`InMemoryChunkTaxonomyAllocationStore` for tests and the
  in-process demo.
* :class:`SQLiteChunkTaxonomyAllocationStore` for the persistent
  runtime.

Both expose the same :class:`ChunkTaxonomyAllocationStore` Protocol
so call sites (allocator wiring, the read route) don't care which
backend is active.

Cascade contract: ``delete_for_version`` returns the count of rows
removed for ``version_id``. The SQLite implementation walks the
``idx_chunk_taxonomy_allocations_version_id`` index added by
migration ``0015_chunk_taxonomy_allocations``; the in-memory
implementation is a list filter. Both are idempotent.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

from app.schemas.chunk_taxonomy_allocation import (
    BusinessCategoryAssignment,
    ChunkTaxonomyAllocation,
)
from app.services.catalog_store import (
    InvalidCursor,
    _decode_cursor,
    _encode_cursor,
)

# Default page size for ``list_all``. Matches the rest of the
# knowledge-layer read paths so a client switching between routes
# doesn't have to re-tune ``limit``.
DEFAULT_ALLOCATIONS_PAGE_LIMIT: Final[int] = 50

# Hard ceiling on a single page. Keeps the SQLite read bounded and
# the JSON envelope small enough for the chunk-inspector UI to
# render in one pass; clients that need more walk the cursor.
MAX_ALLOCATIONS_PAGE_LIMIT: Final[int] = 200


class ChunkTaxonomyAllocationStore(Protocol):
    """Persistence boundary for LLM allocation rows."""

    def save_allocations(self, allocations: list[ChunkTaxonomyAllocation]) -> None:
        """Persist a batch of allocations.

        ``extracted_at`` is set server-side on each row before write
        — callers hand in allocations with a sentinel timestamp and
        the store stamps them with ``datetime.now(UTC)`` so the wire
        always carries a server-authoritative value.

        Empty input is a no-op (an allocator may emit zero rows for
        a document with no non-empty chunks and the store should not
        error).
        """

    def list_for_document(
        self,
        document_id: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_ALLOCATIONS_PAGE_LIMIT,
    ) -> tuple[list[ChunkTaxonomyAllocation], str | None]:
        """Return allocations for ``document_id``, paginated.

        Sorted by ``(extracted_at ASC, id ASC)`` — the ``id``
        tie-breaker keeps two same-instant inserts from shifting
        between pages.

        Raises :class:`InvalidCursor` when ``cursor`` cannot be
        decoded (route layer maps to HTTP 400 with the message in
        ``detail``).
        """

    def list_for_chunk(
        self,
        chunk_id: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_ALLOCATIONS_PAGE_LIMIT,
    ) -> tuple[list[ChunkTaxonomyAllocation], str | None]:
        """Return allocations for a single chunk across versions.

        Same shape as :meth:`list_for_document`. Used by the chunk-
        inspector UI when an operator drills into one chunk's
        history.
        """

    def list_all(
        self,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_ALLOCATIONS_PAGE_LIMIT,
    ) -> tuple[list[ChunkTaxonomyAllocation], str | None]:
        """Return every allocation in the store, paginated."""

    def delete_for_version(self, version_id: str) -> int:
        """Remove every allocation sourced from ``version_id``.

        Returns the count of rows removed. Idempotent: calling twice
        for the same version_id returns 0 on the second call. Used
        by the re-allocation flow (the projector hook deletes prior
        rows before saving the new batch).
        """


class InMemoryChunkTaxonomyAllocationStore:
    """In-memory store — list of allocations; Protocol parity with SQLite."""

    def __init__(self) -> None:
        self._rows: list[ChunkTaxonomyAllocation] = []

    def save_allocations(self, allocations: list[ChunkTaxonomyAllocation]) -> None:
        if not allocations:
            return
        now = datetime.now(UTC)
        for allocation in allocations:
            self._rows.append(allocation.model_copy(update={"extracted_at": now}))

    def list_for_document(
        self,
        document_id: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_ALLOCATIONS_PAGE_LIMIT,
    ) -> tuple[list[ChunkTaxonomyAllocation], str | None]:
        candidates = [r for r in self._rows if r.document_id == document_id]
        return self._paginate(candidates, cursor=cursor, limit=limit)

    def list_for_chunk(
        self,
        chunk_id: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_ALLOCATIONS_PAGE_LIMIT,
    ) -> tuple[list[ChunkTaxonomyAllocation], str | None]:
        candidates = [r for r in self._rows if r.chunk_id == chunk_id]
        return self._paginate(candidates, cursor=cursor, limit=limit)

    def list_all(
        self,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_ALLOCATIONS_PAGE_LIMIT,
    ) -> tuple[list[ChunkTaxonomyAllocation], str | None]:
        return self._paginate(list(self._rows), cursor=cursor, limit=limit)

    def delete_for_version(self, version_id: str) -> int:
        before = len(self._rows)
        self._rows = [r for r in self._rows if r.version_id != version_id]
        return before - len(self._rows)

    @staticmethod
    def _paginate(
        candidates: list[ChunkTaxonomyAllocation],
        *,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[ChunkTaxonomyAllocation], str | None]:
        candidates.sort(key=lambda r: (r.extracted_at, r.id))
        if cursor is not None:
            cutoff_extracted_at, cutoff_id = _decode_cursor(cursor)
            candidates = [
                r for r in candidates if (r.extracted_at, r.id) > (cutoff_extracted_at, cutoff_id)
            ]
        page = candidates[:limit]
        next_cursor: str | None = None
        if len(page) >= limit and len(candidates) > limit:
            tail = page[-1]
            next_cursor = _encode_cursor((tail.extracted_at, tail.id))
        return page, next_cursor


class SQLiteChunkTaxonomyAllocationStore:
    """SQLite-backed allocation store. Migration
    ``0015_chunk_taxonomy_allocations`` creates the schema this
    class writes against."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def save_allocations(self, allocations: list[ChunkTaxonomyAllocation]) -> None:
        if not allocations:
            return
        now = datetime.now(UTC)
        rows = [_allocation_to_row(a, extracted_at=now) for a in allocations]
        with self._connect() as connection:
            try:
                connection.execute("BEGIN")
                connection.executemany(
                    """
                    INSERT INTO chunk_taxonomy_allocations (
                        id,
                        chunk_id,
                        section_id,
                        document_id,
                        version_id,
                        assignments_json,
                        taxonomy_fingerprint,
                        model_id,
                        prompt_hash,
                        schema_version,
                        extracted_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        limit: int = DEFAULT_ALLOCATIONS_PAGE_LIMIT,
    ) -> tuple[list[ChunkTaxonomyAllocation], str | None]:
        return self._query(
            where_sql="WHERE document_id = ?",
            where_params=[document_id],
            cursor=cursor,
            limit=limit,
        )

    def list_for_chunk(
        self,
        chunk_id: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_ALLOCATIONS_PAGE_LIMIT,
    ) -> tuple[list[ChunkTaxonomyAllocation], str | None]:
        return self._query(
            where_sql="WHERE chunk_id = ?",
            where_params=[chunk_id],
            cursor=cursor,
            limit=limit,
        )

    def list_all(
        self,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_ALLOCATIONS_PAGE_LIMIT,
    ) -> tuple[list[ChunkTaxonomyAllocation], str | None]:
        return self._query(where_sql="", where_params=[], cursor=cursor, limit=limit)

    def delete_for_version(self, version_id: str) -> int:
        with self._connect() as connection:
            cursor_obj = connection.execute(
                "DELETE FROM chunk_taxonomy_allocations WHERE version_id = ?",
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
    ) -> tuple[list[ChunkTaxonomyAllocation], str | None]:
        cutoff_extracted_at: datetime | None = None
        cutoff_id: str | None = None
        if cursor is not None:
            cutoff_extracted_at, cutoff_id = _decode_cursor(cursor)
        sql = (
            "SELECT id, chunk_id, section_id, document_id, version_id, "
            "assignments_json, taxonomy_fingerprint, model_id, prompt_hash, "
            "schema_version, extracted_at "
            "FROM chunk_taxonomy_allocations "
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
        items = [_row_to_allocation(row) for row in page_rows]
        next_cursor: str | None = None
        if len(rows) > limit and items:
            tail = items[-1]
            next_cursor = _encode_cursor((tail.extracted_at, tail.id))
        return items, next_cursor


# ─── Internal helpers ───────────────────────────────────────────────


def _allocation_to_row(
    allocation: ChunkTaxonomyAllocation,
    *,
    extracted_at: datetime,
) -> tuple[object, ...]:
    """Project a :class:`ChunkTaxonomyAllocation` into the SQLite
    tuple shape.

    The assignments list collapses into a single ``assignments_json``
    column — SQLite has no native array type and a separate join
    table would not buy anything the chunk inspector cares about
    (the UI always loads the full assignment set for one chunk).
    """
    assignments_payload = [a.model_dump(mode="json") for a in allocation.assignments]
    return (
        allocation.id,
        allocation.chunk_id,
        allocation.section_id,
        allocation.document_id,
        allocation.version_id,
        json.dumps(assignments_payload),
        allocation.taxonomy_fingerprint,
        allocation.model_id,
        allocation.prompt_hash,
        allocation.schema_version,
        extracted_at.isoformat(),
    )


def _row_to_allocation(row: sqlite3.Row) -> ChunkTaxonomyAllocation:
    """Re-build a :class:`ChunkTaxonomyAllocation` from a SQLite row.

    Defensive against a stale ``schema_version`` value persisted by a
    future v0.2 store: the Pydantic model rejects anything but the
    current ``Literal`` set, so a mixed-version DB raises at the
    read boundary rather than silently flowing v0.2 rows to v0.1
    readers.
    """
    assignments = _parse_assignments(row["assignments_json"])
    return ChunkTaxonomyAllocation(
        id=row["id"],
        chunk_id=row["chunk_id"],
        section_id=row["section_id"],
        document_id=row["document_id"],
        version_id=row["version_id"],
        assignments=assignments,
        taxonomy_fingerprint=row["taxonomy_fingerprint"],
        model_id=row["model_id"],
        prompt_hash=row["prompt_hash"],
        schema_version=row["schema_version"],
        extracted_at=datetime.fromisoformat(row["extracted_at"]),
    )


def _parse_assignments(raw: str) -> list[BusinessCategoryAssignment]:
    """Parse the ``assignments_json`` column.

    Fails loud on a malformed cell (rather than silently returning
    an empty list) so a corrupt row surfaces at the read boundary.
    """
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"assignments_json is not valid JSON: {exc}") from exc
    if not isinstance(decoded, list):
        raise ValueError("assignments_json must decode to a JSON list.")
    return [BusinessCategoryAssignment.model_validate(item) for item in decoded]


__all__ = [
    "DEFAULT_ALLOCATIONS_PAGE_LIMIT",
    "MAX_ALLOCATIONS_PAGE_LIMIT",
    "ChunkTaxonomyAllocationStore",
    "InMemoryChunkTaxonomyAllocationStore",
    "InvalidCursor",
    "SQLiteChunkTaxonomyAllocationStore",
]
