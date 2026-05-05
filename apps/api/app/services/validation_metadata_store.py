"""Sidecar persistence for HITL ``ValidationMetadata`` (ADR-023 §4).

One row per ``version_id`` keyed in the ``validation_metadata``
table (migration 0007). Two implementations behind a small
:class:`ValidationMetadataStore` Protocol:

- :class:`InMemoryValidationMetadataStore` — dict-backed; the test
  default and the in-memory wiring's backing store.
- :class:`SQLiteValidationMetadataStore` — production. Reuses the
  catalog's database file so the metadata sits next to the
  ``document_versions`` rows it references.

Per EPIC-A's "auto-validated == human-validated to consumers" rule,
this store is **internal**: no public API route reads from it. The
metadata supports auditing + the next-slice ``hitl_router.py``;
consumers of the public ``Document`` / ``DocumentVersion`` shape see
the same body they always saw.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.schemas.validation_metadata import (
    ConfidenceScore,
    RoutingMethod,
    ValidationMetadata,
    ValidationMethod,
)

log = logging.getLogger(__name__)


@runtime_checkable
class ValidationMetadataStore(Protocol):
    """Append/upsert + lookup boundary for the sidecar table."""

    name: str

    def upsert(self, metadata: ValidationMetadata) -> None:  # pragma: no cover - Protocol
        """Replace any existing row for ``metadata.version_id``.

        Idempotent — re-running with the same metadata is a no-op
        modulo the persisted-row's mutable timestamps. The scorer
        calls this on every ``NEEDS_REVIEW`` transition; the future
        router calls it again with the routing decision filled in.
        """

    def get(self, version_id: str) -> ValidationMetadata | None:  # pragma: no cover - Protocol
        """Return the persisted metadata for one version, or ``None``."""

    def list_all(self) -> list[ValidationMetadata]:  # pragma: no cover - Protocol
        """Return every persisted row. Tests + future admin tooling."""


class InMemoryValidationMetadataStore:
    """Dict-backed :class:`ValidationMetadataStore`."""

    name: str = "in-memory"

    def __init__(self) -> None:
        self._rows: dict[str, ValidationMetadata] = {}
        self._lock = threading.RLock()

    def upsert(self, metadata: ValidationMetadata) -> None:
        # Deep-copy via Pydantic so the caller can't mutate the stored
        # row by mutating the input after the call returns.
        with self._lock:
            self._rows[metadata.version_id] = metadata.model_copy(deep=True)

    def get(self, version_id: str) -> ValidationMetadata | None:
        with self._lock:
            stored = self._rows.get(version_id)
        return stored.model_copy(deep=True) if stored is not None else None

    def list_all(self) -> list[ValidationMetadata]:
        with self._lock:
            return [row.model_copy(deep=True) for row in self._rows.values()]


class SQLiteValidationMetadataStore:
    """SQLite-backed :class:`ValidationMetadataStore`."""

    name: str = "sqlite"

    def __init__(self, database_path: Path | str) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.RLock()

    def upsert(self, metadata: ValidationMetadata) -> None:
        score = metadata.confidence_score
        if score is None:
            ocr_override_value: int | None = None
        else:
            ocr_override_value = 1 if score.ocr_override_active else 0
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO validation_metadata ("
                "  version_id, confidence_overall, confidence_signals, confidence_weights,"
                "  ocr_override_active, confidence_computed_at, confidence_computed_by_version,"
                "  routing_decision, validation_method, validation_actor"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    metadata.version_id,
                    score.overall if score is not None else None,
                    json.dumps(score.signals, sort_keys=True) if score is not None else None,
                    json.dumps(score.weights, sort_keys=True) if score is not None else None,
                    ocr_override_value,
                    score.computed_at.isoformat() if score is not None else None,
                    score.computed_by_version if score is not None else None,
                    metadata.routing_decision,
                    metadata.validation_method,
                    metadata.validation_actor,
                ),
            )

    def get(self, version_id: str) -> ValidationMetadata | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT version_id, confidence_overall, confidence_signals, confidence_weights,"
                "       ocr_override_active, confidence_computed_at,"
                "       confidence_computed_by_version,"
                "       routing_decision, validation_method, validation_actor "
                "FROM validation_metadata WHERE version_id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_metadata(row)

    def list_all(self) -> list[ValidationMetadata]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT version_id, confidence_overall, confidence_signals, confidence_weights,"
                "       ocr_override_active, confidence_computed_at,"
                "       confidence_computed_by_version,"
                "       routing_decision, validation_method, validation_actor "
                "FROM validation_metadata "
                "ORDER BY version_id"
            ).fetchall()
        return [_row_to_metadata(row) for row in rows]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(str(self._path))
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()


def _row_to_metadata(row: tuple) -> ValidationMetadata:
    (
        version_id,
        confidence_overall,
        confidence_signals,
        confidence_weights,
        ocr_override_active,
        confidence_computed_at,
        confidence_computed_by_version,
        routing_decision,
        validation_method,
        validation_actor,
    ) = row
    score: ConfidenceScore | None = None
    if confidence_overall is not None and confidence_computed_at is not None:
        # ``ocr_override_active`` is stored as 0/1 INTEGER — coerce to
        # bool. ``None`` here means we never wrote a score (router-only
        # row); ``ConfidenceScore`` requires the override flag, so the
        # whole score block stays ``None`` in that case.
        score = ConfidenceScore(
            overall=float(confidence_overall),
            signals=json.loads(confidence_signals) if confidence_signals else {},
            weights=json.loads(confidence_weights) if confidence_weights else {},
            ocr_override_active=bool(ocr_override_active),
            computed_at=datetime.fromisoformat(confidence_computed_at),
            computed_by_version=confidence_computed_by_version or "v1",
        )
    return ValidationMetadata(
        version_id=version_id,
        confidence_score=score,
        routing_decision=_coerce_routing(routing_decision),
        validation_method=_coerce_method(validation_method),
        validation_actor=validation_actor,
    )


def _coerce_routing(value: str | None) -> RoutingMethod | None:
    """Narrow a freeform DB string into the typed ``RoutingMethod``
    literal. Unknown values are dropped to ``None`` rather than
    raising — the router is the authoritative writer, and a
    forward-compat row written by a newer service shouldn't crash
    this read path.
    """
    if value in {"auto", "human", "external"}:
        return value  # type: ignore[return-value]
    return None


def _coerce_method(value: str | None) -> ValidationMethod | None:
    if value in {"auto", "human", "external"}:
        return value  # type: ignore[return-value]
    return None


__all__ = [
    "InMemoryValidationMetadataStore",
    "SQLiteValidationMetadataStore",
    "ValidationMetadataStore",
]
