"""Persistence boundary for the first-class Playbook/Process data
model (#369, ADR-031).

A Process captures the procedural shape of a SOP document (ordered
steps, preconditions, outcomes) that flat chunk extraction would
otherwise flatten away. Per ADR-031, Processes are
governance-shaped — they describe what was extracted from a
document — so they live alongside the catalog tables in SQLite,
not in the Neo4j graph layer.

Two storage shapes:

* :class:`InMemoryProcessStore` for tests and the in-process demo.
* :class:`SQLiteProcessStore` for the persistent runtime. Reads /
  writes against the tables created by migration ``0013_processes``.

Both expose the same :class:`ProcessStore` Protocol so call sites
(boot wiring, the future SOP-aware parser, the read routes) don't
care which backend is active.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.schemas.process import Process, ProcessStep, ProcessSummary
from app.services.catalog_store import (
    InvalidCursor,
    _decode_cursor,
    _encode_cursor,
)

# Default page size for ``ProcessStore.list``. Matches the
# Explorer's other list views; the route layer accepts an explicit
# ``limit`` query param so the constant is just the sensible
# default when callers don't pass one.
DEFAULT_PROCESS_PAGE_LIMIT = 50
MAX_PROCESS_PAGE_LIMIT = 200


class ProcessStore(Protocol):
    """Persistence boundary for extracted Process payloads."""

    def save_process(self, process: Process) -> None:
        """Persist (or replace) a Process and its ordered step rows.

        Replace semantics: writing a Process with an existing ``id``
        overwrites the prior payload (metadata + step rows). This
        keeps the future SOP-aware parser's re-extraction path
        idempotent: re-emitting the same Process id replaces the
        previous version's payload in place without leaving orphan
        step rows behind.

        ``Process.created_at`` is overridden with the store's
        clock at write time — the store is the source of truth for
        the timestamp so callers can pass any placeholder.
        """

    def get(self, process_id: str) -> Process | None:
        """Return the full Process (metadata + ordered steps) or
        ``None`` when ``process_id`` is unknown. Steps come back
        sorted by :attr:`ProcessStep.step_number` ASC."""

    def list(
        self,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_PROCESS_PAGE_LIMIT,
    ) -> tuple[list[ProcessSummary], str | None]:
        """Return one page of process summaries (metadata-only).

        Pagination is by ``(created_at ASC, id ASC)`` — same codec
        as the document list (:func:`_encode_cursor` /
        :func:`_decode_cursor`) so the cursor token shape stays
        consistent across the knowledge surface. The second tuple
        element is the cursor for the next page, or ``None`` when
        this page is the last one.

        Raises :class:`InvalidCursor` when ``cursor`` cannot be
        decoded — the route layer maps that to HTTP 400.
        """

    def delete_for_version(self, version_id: str) -> int:
        """Remove every Process owned by ``version_id`` and cascade
        to its step rows. Returns the number of Process rows deleted
        (step rows aren't counted; the FK CASCADE handles them
        atomically).

        Used by the future SOP-aware parser's re-extraction path
        when a new version supersedes the previous extraction: drop
        the prior Processes in one statement before writing the new
        ones, so the store never carries stale rows.
        """


class InMemoryProcessStore:
    """Dict-backed store for tests and the in-process demo.

    Mirrors the SQLite store's contract bit-for-bit so the same
    parametrized test fixture exercises both backends. Internal
    ordering is by insertion-time ``(created_at, id)`` so the list
    pagination is deterministic across test runs without depending
    on dict insertion-order quirks.
    """

    def __init__(self) -> None:
        self._processes: dict[str, Process] = {}

    def save_process(self, process: Process) -> None:
        # Sort the step list so the persisted shape is canonical —
        # the SQLite backend's ``ORDER BY step_number ASC`` does
        # the same on read; keeping parity here means the
        # parametrised tests see identical bodies from both
        # backends without any extra normalisation.
        ordered_steps = sorted(process.steps, key=lambda step: step.step_number)
        self._processes[process.id] = process.model_copy(
            update={
                "steps": ordered_steps,
                "created_at": datetime.now(UTC),
            }
        )

    def get(self, process_id: str) -> Process | None:
        return self._processes.get(process_id)

    def list(
        self,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_PROCESS_PAGE_LIMIT,
    ) -> tuple[list[ProcessSummary], str | None]:
        ordered = sorted(
            self._processes.values(),
            key=lambda process: (process.created_at, process.id),
        )
        if cursor is not None:
            after_created_at, after_id = _decode_cursor(cursor)
            ordered = [
                process
                for process in ordered
                if (process.created_at, process.id) > (after_created_at, after_id)
            ]
        page = ordered[:limit]
        summaries = [_to_summary(process) for process in page]
        next_cursor: str | None
        if len(ordered) > limit and page:
            tail = page[-1]
            next_cursor = _encode_cursor((tail.created_at, tail.id))
        else:
            next_cursor = None
        return summaries, next_cursor

    def delete_for_version(self, version_id: str) -> int:
        # Snapshot the ids first so we don't mutate the dict during
        # iteration — same pattern the SQLite store gets for free
        # via its DELETE statement.
        doomed = [
            process_id
            for process_id, process in self._processes.items()
            if process.version_id == version_id
        ]
        for process_id in doomed:
            del self._processes[process_id]
        return len(doomed)


class SQLiteProcessStore:
    """SQLite-backed Process store. Migration ``0013_processes``
    creates the schema this class reads from and writes against."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        # FK enforcement so the ``ON DELETE CASCADE`` on
        # ``process_steps.process_id`` actually fires when a row
        # in ``processes`` is removed. SQLite defaults FK
        # enforcement off; every store on this codebase opts in.
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def save_process(self, process: Process) -> None:
        when = datetime.now(UTC).isoformat()
        ordered_steps = sorted(process.steps, key=lambda step: step.step_number)
        with self._connect() as connection:
            try:
                connection.execute("BEGIN")
                # Replace semantics: drop any existing payload for
                # this id, then re-insert. The CASCADE FK on
                # ``process_steps`` cleans up the old step rows in
                # the same statement.
                connection.execute("DELETE FROM processes WHERE id = ?", (process.id,))
                connection.execute(
                    """
                    INSERT INTO processes
                        (id, title, document_id, version_id,
                         schema_version, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        process.id,
                        process.title,
                        process.document_id,
                        process.version_id,
                        process.schema_version,
                        when,
                    ),
                )
                for step in ordered_steps:
                    connection.execute(
                        """
                        INSERT INTO process_steps
                            (process_id, step_number, title, body,
                             preconditions_json, outcomes_json,
                             referenced_tool_id, source_reference_ids_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            process.id,
                            step.step_number,
                            step.title,
                            step.body,
                            json.dumps(step.preconditions),
                            json.dumps(step.outcomes),
                            step.referenced_tool_id,
                            json.dumps(step.source_reference_ids),
                        ),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get(self, process_id: str) -> Process | None:
        with self._connect() as connection:
            process_row = connection.execute(
                """
                SELECT id, title, document_id, version_id,
                       schema_version, created_at
                FROM processes
                WHERE id = ?
                """,
                (process_id,),
            ).fetchone()
            if process_row is None:
                return None
            step_rows = connection.execute(
                """
                SELECT step_number, title, body, preconditions_json,
                       outcomes_json, referenced_tool_id,
                       source_reference_ids_json
                FROM process_steps
                WHERE process_id = ?
                ORDER BY step_number ASC
                """,
                (process_id,),
            ).fetchall()
        return _row_to_process(process_row, step_rows)

    def list(
        self,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_PROCESS_PAGE_LIMIT,
    ) -> tuple[list[ProcessSummary], str | None]:
        clauses: list[str] = []
        params: list[object] = []
        if cursor is not None:
            after_created_at, after_id = _decode_cursor(cursor)
            clauses.append("(created_at, id) > (?, ?)")
            params.extend([after_created_at.isoformat(), after_id])
        # Fetch ``limit + 1`` rows so the "is there more behind this
        # page" answer comes from the same query — no second SELECT
        # to compute the cursor. Mirrors the in-scope catalog list.
        params.append(int(limit) + 1)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        query = (
            "SELECT id, title, document_id, version_id, "
            "schema_version, created_at "
            f"FROM processes {where} "
            "ORDER BY created_at ASC, id ASC LIMIT ?"
        )
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        page = rows[:limit]
        summaries = [_row_to_summary(row) for row in page]
        next_cursor: str | None
        if len(rows) > limit and page:
            tail = summaries[-1]
            next_cursor = _encode_cursor((tail.created_at, tail.id))
        else:
            next_cursor = None
        return summaries, next_cursor

    def delete_for_version(self, version_id: str) -> int:
        with self._connect() as connection:
            try:
                connection.execute("BEGIN")
                cursor = connection.execute(
                    "DELETE FROM processes WHERE version_id = ?",
                    (version_id,),
                )
                deleted = cursor.rowcount or 0
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return deleted


# ─── Internal helpers ───────────────────────────────────────────────


def _to_summary(process: Process) -> ProcessSummary:
    """Drop ``steps`` from a Process to produce its list-view shape."""
    return ProcessSummary(
        id=process.id,
        title=process.title,
        document_id=process.document_id,
        version_id=process.version_id,
        schema_version=process.schema_version,
        created_at=process.created_at,
    )


def _row_to_process(
    process_row: sqlite3.Row,
    step_rows: list[sqlite3.Row],
) -> Process:
    """Re-build a :class:`Process` from a flat process row + ordered
    step rows. The step rows are already ordered by ``step_number
    ASC`` by the caller's ``ORDER BY``."""
    steps = [
        ProcessStep(
            step_number=int(row["step_number"]),
            title=row["title"],
            body=row["body"],
            preconditions=json.loads(row["preconditions_json"]),
            outcomes=json.loads(row["outcomes_json"]),
            referenced_tool_id=row["referenced_tool_id"],
            source_reference_ids=json.loads(row["source_reference_ids_json"]),
        )
        for row in step_rows
    ]
    return Process(
        id=process_row["id"],
        title=process_row["title"],
        document_id=process_row["document_id"],
        version_id=process_row["version_id"],
        schema_version=process_row["schema_version"],
        steps=steps,
        created_at=datetime.fromisoformat(process_row["created_at"]),
    )


def _row_to_summary(row: sqlite3.Row) -> ProcessSummary:
    return ProcessSummary(
        id=row["id"],
        title=row["title"],
        document_id=row["document_id"],
        version_id=row["version_id"],
        schema_version=row["schema_version"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


__all__ = [
    "DEFAULT_PROCESS_PAGE_LIMIT",
    "InMemoryProcessStore",
    "InvalidCursor",
    "MAX_PROCESS_PAGE_LIMIT",
    "ProcessStore",
    "SQLiteProcessStore",
]
