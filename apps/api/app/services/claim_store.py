"""SQLite-backed storage for the atomic Claim/Fact data model
(#368, ADR-031).

Per ADR-031, claims live in SQLite — they are governance / audit
data ("what was extracted from a validated version, with provenance
back to the chunks"), not graph traversal data. The store is the
write surface the future LLM extractor pass calls into; the read
surface is :func:`list_for_subject`, which powers
``GET /knowledge/claims?subject_entity_id=…``.

Two storage shapes:

* :class:`InMemoryClaimStore` for tests and the in-process demo.
* :class:`SQLiteClaimStore` for the persistent runtime.

Both expose the same :class:`ClaimStore` Protocol so call sites
(future extractor wiring, the read route) don't care which backend
is active.

Cascade contract: ``delete_for_version`` returns the count of rows
removed for ``version_id``. The SQLite implementation walks the
``idx_claims_version_id`` index added by migration ``0012_claims``;
the in-memory implementation is a list filter. Both are idempotent.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

from app.schemas.claim import Claim
from app.services.catalog_store import (
    InvalidCursor,
    _decode_cursor,
    _encode_cursor,
)

# Default page size for ``list_for_subject``. Matches the catalog
# read paths so a client switching between the two doesn't have to
# re-tune ``limit``.
DEFAULT_CLAIMS_PAGE_LIMIT: Final[int] = 50

# Hard ceiling on a single page. Keeps the SQLite read bounded and
# the JSON envelope small enough for the Explorer to render in one
# pass; clients that need more walk the cursor.
MAX_CLAIMS_PAGE_LIMIT: Final[int] = 200


class ClaimStore(Protocol):
    """Persistence boundary for atomic Claim/Fact rows."""

    def save_claims(self, claims: list[Claim]) -> None:
        """Persist a batch of claims.

        ``extracted_at`` is set server-side on each claim before
        write — callers hand in claims without a timestamp and the
        store stamps them with ``datetime.now(UTC)`` so the wire
        always carries a server-authoritative value.

        Empty input is a no-op (the future extractor may emit zero
        claims for a given pass and the store should not error).
        """

    def list_for_subject(
        self,
        subject_entity_id: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_CLAIMS_PAGE_LIMIT,
    ) -> tuple[list[Claim], str | None]:
        """Return claims about ``subject_entity_id``, paginated.

        Returns a ``(items, next_cursor)`` tuple. ``next_cursor`` is
        ``None`` when no more rows exist behind the current page.
        Sorted by ``(extracted_at ASC, id ASC)`` — the ``id``
        tie-breaker keeps two same-instant inserts from shifting
        between pages.

        Raises :class:`InvalidCursor` when ``cursor`` cannot be
        decoded (route layer maps to HTTP 400 with the message in
        ``detail``).
        """

    def delete_for_version(self, version_id: str) -> int:
        """Remove every claim sourced from ``version_id``.

        Returns the count of rows removed. Idempotent: calling
        twice for the same version_id returns 0 on the second call.
        Used by the future cascade-deletion wiring (re-extract
        flow + version archive).
        """


class InMemoryClaimStore:
    """In-memory store — list of claims; Protocol parity with SQLite."""

    def __init__(self) -> None:
        self._claims: list[Claim] = []

    def save_claims(self, claims: list[Claim]) -> None:
        if not claims:
            return
        now = datetime.now(UTC)
        for claim in claims:
            # Stamp every save with a fresh server-authoritative
            # timestamp so two saves of "the same" claim get distinct
            # ``extracted_at`` values for cursor pagination.
            self._claims.append(claim.model_copy(update={"extracted_at": now}))

    def list_for_subject(
        self,
        subject_entity_id: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_CLAIMS_PAGE_LIMIT,
    ) -> tuple[list[Claim], str | None]:
        # Filter + stable sort. The sort key matches the SQLite
        # ORDER BY so the two implementations return the same page
        # for the same input.
        candidates = [c for c in self._claims if c.subject_entity_id == subject_entity_id]
        candidates.sort(key=lambda c: (c.extracted_at, c.id))
        if cursor is not None:
            cutoff_extracted_at, cutoff_id = _decode_cursor(cursor)
            candidates = [
                c for c in candidates if (c.extracted_at, c.id) > (cutoff_extracted_at, cutoff_id)
            ]
        page = candidates[:limit]
        next_cursor: str | None = None
        if len(page) >= limit and len(candidates) > limit:
            tail = page[-1]
            next_cursor = _encode_cursor((tail.extracted_at, tail.id))
        return page, next_cursor

    def delete_for_version(self, version_id: str) -> int:
        before = len(self._claims)
        self._claims = [c for c in self._claims if c.version_id != version_id]
        return before - len(self._claims)


class SQLiteClaimStore:
    """SQLite-backed claim store. Migration ``0012_claims`` creates
    the schema this class writes against."""

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

    def save_claims(self, claims: list[Claim]) -> None:
        if not claims:
            return
        now = datetime.now(UTC)
        rows = [_claim_to_row(c, extracted_at=now) for c in claims]
        with self._connect() as connection:
            try:
                connection.execute("BEGIN")
                connection.executemany(
                    """
                    INSERT INTO claims (
                        id,
                        document_id,
                        version_id,
                        subject_entity_id,
                        predicate,
                        object_value,
                        object_entity_id,
                        confidence,
                        schema_version,
                        extracted_at,
                        provenance_chunk_ids_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def list_for_subject(
        self,
        subject_entity_id: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_CLAIMS_PAGE_LIMIT,
    ) -> tuple[list[Claim], str | None]:
        cutoff_extracted_at: datetime | None = None
        cutoff_id: str | None = None
        if cursor is not None:
            cutoff_extracted_at, cutoff_id = _decode_cursor(cursor)
        # Fetch limit+1 so we can tell whether a "next" page exists
        # without a separate COUNT query.
        sql = (
            "SELECT id, document_id, version_id, subject_entity_id, predicate, "
            "object_value, object_entity_id, confidence, schema_version, "
            "extracted_at, provenance_chunk_ids_json "
            "FROM claims WHERE subject_entity_id = ?"
        )
        params: list[object] = [subject_entity_id]
        if cutoff_extracted_at is not None and cutoff_id is not None:
            # The cursor's ``(extracted_at, id)`` predicate matches the
            # ORDER BY below; the index on ``subject_entity_id`` plus
            # the secondary in-memory sort by id is bounded by ``limit``
            # so this remains cheap.
            sql += " AND (extracted_at, id) > (?, ?)"
            params.extend([cutoff_extracted_at.isoformat(), cutoff_id])
        sql += " ORDER BY extracted_at ASC, id ASC LIMIT ?"
        params.append(limit + 1)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        page_rows = rows[:limit]
        items = [_row_to_claim(row) for row in page_rows]
        next_cursor: str | None = None
        if len(rows) > limit and items:
            tail = items[-1]
            next_cursor = _encode_cursor((tail.extracted_at, tail.id))
        return items, next_cursor

    def delete_for_version(self, version_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM claims WHERE version_id = ?",
                (version_id,),
            )
            return cursor.rowcount


# ─── Internal helpers ───────────────────────────────────────────────


def _claim_to_row(claim: Claim, *, extracted_at: datetime) -> tuple[object, ...]:
    """Project a :class:`Claim` into the SQLite tuple shape.

    ``extracted_at`` is supplied by the caller (rather than read off
    the claim) so :meth:`SQLiteClaimStore.save_claims` can stamp the
    whole batch with a single now-timestamp — matches the in-memory
    store's behaviour.
    """
    return (
        claim.id,
        claim.document_id,
        claim.version_id,
        claim.subject_entity_id,
        claim.predicate,
        claim.object_value,
        claim.object_entity_id,
        claim.confidence,
        claim.schema_version,
        extracted_at.isoformat(),
        json.dumps(list(claim.provenance_chunk_ids)),
    )


def _row_to_claim(row: sqlite3.Row) -> Claim:
    """Re-build a :class:`Claim` from a SQLite row.

    Defensive against a stale ``schema_version`` value persisted by a
    future v0.2 store: the Pydantic model rejects anything but the
    current ``Literal`` set, so a mixed-version DB raises at the read
    boundary rather than silently flowing v0.2 rows to v0.1 readers.
    """
    raw_chunks = row["provenance_chunk_ids_json"]
    chunk_ids = _parse_chunk_ids(raw_chunks)
    return Claim(
        id=row["id"],
        document_id=row["document_id"],
        version_id=row["version_id"],
        subject_entity_id=row["subject_entity_id"],
        predicate=row["predicate"],
        object_value=row["object_value"],
        object_entity_id=row["object_entity_id"],
        confidence=row["confidence"],
        schema_version=row["schema_version"],
        extracted_at=datetime.fromisoformat(row["extracted_at"]),
        provenance_chunk_ids=chunk_ids,
    )


def _parse_chunk_ids(raw: str) -> list[str]:
    """Parse the JSON-encoded chunk-id list from the SQLite column.

    Returns the list of ids; defensive against a malformed cell
    (raises :class:`ValueError` rather than silently returning an
    empty list, so a corrupt row is loud).
    """
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"provenance_chunk_ids_json is not valid JSON: {exc}") from exc
    if not isinstance(decoded, list):
        raise ValueError("provenance_chunk_ids_json must decode to a JSON list.")
    if not all(isinstance(item, str) for item in decoded):
        raise ValueError("provenance_chunk_ids_json items must all be strings.")
    return list(decoded)


__all__ = [
    "DEFAULT_CLAIMS_PAGE_LIMIT",
    "MAX_CLAIMS_PAGE_LIMIT",
    "ClaimStore",
    "InMemoryClaimStore",
    "InvalidCursor",
    "SQLiteClaimStore",
]
