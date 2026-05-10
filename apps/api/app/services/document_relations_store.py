"""SQLite-backed cache for aggregated document↔document relations
(ADR-031, #380).

The Explorer's relation evidence drawer (#318) pulls
``GET /knowledge/relations/aggregate`` per pair the user clicks.
The on-demand compute walks the Neo4j chunk-edge layer at the
boundary between two documents, scores each contributing pair,
and aggregates. Cheap at MVP scale, expensive at the 100k+ chunks
target.

This module persists those aggregates in SQLite. The route reads
from here on the hot path; the projector populates here on
write completion. ``?refresh=true`` on the route bypasses the
cache for a fresh compute (cache-miss debugging, drift override).

Both store implementations expose the same Protocol so the cache
service is backend-agnostic.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.schemas.knowledge_relations import (
    AggregatedRelationEvidence,
    ContributingChunkPair,
)


class DocumentRelationsStore(Protocol):
    """Persistence boundary for the document↔document aggregate cache."""

    def get(
        self,
        *,
        source_document_id: str,
        target_document_id: str,
    ) -> tuple[AggregatedRelationEvidence, datetime] | None:
        """Return the cached aggregate + its ``computed_at`` timestamp,
        or ``None`` if the pair has never been computed."""

    def upsert(
        self,
        evidence: AggregatedRelationEvidence,
        *,
        now: datetime | None = None,
    ) -> None:
        """Write (or replace) the cached row for the
        ``(source_document_id, target_document_id)`` pair carried in
        ``evidence``. Does NOT auto-write the reverse pair —
        callers that want both directions cached must call twice."""

    def delete_for_document(self, document_id: str) -> int:
        """Remove every cached row that names ``document_id`` as
        either source or target. Returns the row count deleted.

        Used by the cache-invalidation path on document purge — the
        edge structure changes when a document is removed, so any
        cached aggregate touching it must be evicted."""


class InMemoryDocumentRelationsStore:
    """Dict-backed store for tests and the in-process demo."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], tuple[AggregatedRelationEvidence, datetime]] = {}

    def get(
        self,
        *,
        source_document_id: str,
        target_document_id: str,
    ) -> tuple[AggregatedRelationEvidence, datetime] | None:
        return self._rows.get((source_document_id, target_document_id))

    def upsert(
        self,
        evidence: AggregatedRelationEvidence,
        *,
        now: datetime | None = None,
    ) -> None:
        when = now or datetime.now(UTC)
        self._rows[(evidence.source_document_id, evidence.target_document_id)] = (evidence, when)

    def delete_for_document(self, document_id: str) -> int:
        keys = [key for key in self._rows if document_id in key]
        for key in keys:
            del self._rows[key]
        return len(keys)


class SQLiteDocumentRelationsStore:
    """SQLite-backed cache. Migration ``0011_document_relations``
    creates the schema this class writes against."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def get(
        self,
        *,
        source_document_id: str,
        target_document_id: str,
    ) -> tuple[AggregatedRelationEvidence, datetime] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    aggregate_score,
                    pair_count,
                    is_bridge,
                    is_outlier,
                    top_pairs_json,
                    computed_at
                FROM document_relations
                WHERE source_document_id = ?
                  AND target_document_id = ?
                """,
                (source_document_id, target_document_id),
            ).fetchone()
        if row is None:
            return None
        evidence = AggregatedRelationEvidence(
            source_document_id=source_document_id,
            target_document_id=target_document_id,
            aggregate_score=float(row["aggregate_score"]),
            pair_count=int(row["pair_count"]),
            is_bridge=bool(row["is_bridge"]),
            is_outlier=bool(row["is_outlier"]),
            top_contributing_pairs=_decode_top_pairs(row["top_pairs_json"]),
        )
        return evidence, datetime.fromisoformat(row["computed_at"])

    def upsert(
        self,
        evidence: AggregatedRelationEvidence,
        *,
        now: datetime | None = None,
    ) -> None:
        when = (now or datetime.now(UTC)).isoformat()
        top_pairs_json = json.dumps([pair.model_dump() for pair in evidence.top_contributing_pairs])
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO document_relations (
                    source_document_id,
                    target_document_id,
                    aggregate_score,
                    pair_count,
                    is_bridge,
                    is_outlier,
                    top_pairs_json,
                    computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_document_id, target_document_id) DO UPDATE SET
                    aggregate_score = excluded.aggregate_score,
                    pair_count      = excluded.pair_count,
                    is_bridge       = excluded.is_bridge,
                    is_outlier      = excluded.is_outlier,
                    top_pairs_json  = excluded.top_pairs_json,
                    computed_at     = excluded.computed_at
                """,
                (
                    evidence.source_document_id,
                    evidence.target_document_id,
                    float(evidence.aggregate_score),
                    int(evidence.pair_count),
                    1 if evidence.is_bridge else 0,
                    1 if evidence.is_outlier else 0,
                    top_pairs_json,
                    when,
                ),
            )
            connection.commit()

    def delete_for_document(self, document_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM document_relations
                WHERE source_document_id = ?
                   OR target_document_id = ?
                """,
                (document_id, document_id),
            )
            connection.commit()
            return cursor.rowcount


def _decode_top_pairs(payload: str) -> list[ContributingChunkPair]:
    """Re-hydrate the JSON-stored ``top_contributing_pairs`` list."""
    raw: Iterable[dict] = json.loads(payload)
    return [ContributingChunkPair.model_validate(entry) for entry in raw]


__all__ = [
    "DocumentRelationsStore",
    "InMemoryDocumentRelationsStore",
    "SQLiteDocumentRelationsStore",
]
