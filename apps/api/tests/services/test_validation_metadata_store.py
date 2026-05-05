"""Tests for the ``ValidationMetadataStore`` sidecar (ADR-023 §4, #215).

Parametrised across the in-memory and SQLite implementations so the
two surfaces stay byte-equivalent (the catalog-DB persistence path
is the production wiring; the in-memory path is the test default).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.schemas.validation_metadata import ConfidenceScore, ValidationMetadata
from app.services.migrations import _run_migrations
from app.services.validation_metadata_store import (
    InMemoryValidationMetadataStore,
    SQLiteValidationMetadataStore,
    ValidationMetadataStore,
)


def _make_score(overall: float = 0.7, ocr_override: bool = False) -> ConfidenceScore:
    return ConfidenceScore(
        overall=overall,
        signals={
            "ocr": 1.0 if not ocr_override else 0.0,
            "orphan_ratio": 0.8,
            "length_z": 0.7,
            "topic_incoherence": 0.9,
            "citation_coverage": 0.5,
        },
        weights={
            "ocr": 0.2,
            "orphan_ratio": 0.2,
            "length_z": 0.2,
            "topic_incoherence": 0.2,
            "citation_coverage": 0.2,
        },
        ocr_override_active=ocr_override,
        computed_at=datetime(2026, 5, 5, 12, 0, 0, tzinfo=UTC),
        computed_by_version="v1",
    )


def _seed_sqlite_schema(db_path: Path) -> None:
    """Run the migration suite + seed parent rows for the FK.

    ``validation_metadata.version_id`` is a foreign key into
    ``document_versions(id)``; the SQLite store would refuse an
    insert for a version_id that isn't already in the catalog. To
    keep the unit tests independent of the catalog wiring, we seed
    a small set of parent rows once per fresh database.
    """
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        _run_migrations(conn)
        conn.execute(
            "INSERT INTO documents (id, original_filename, latest_version_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("doc-1", "fixture.txt", "ver-1", "2026-05-05T12:00:00+00:00"),
        )
        for vid in ("ver-1", "ver-ocr", "ver-h", "ver-no-score", "ver-0", "ver-2"):
            conn.execute(
                "INSERT INTO document_versions ("
                "  id, document_id, version_number, filename, content_type, file_size,"
                "  sha256, storage_uri, status, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    vid,
                    "doc-1",
                    1,
                    "fixture.txt",
                    "text/plain",
                    10,
                    "0" * 64,
                    "memory://0",
                    "STORED",
                    "2026-05-05T12:00:00+00:00",
                ),
            )
    finally:
        conn.close()


@pytest.fixture(
    params=["in-memory", "sqlite"],
    ids=["in-memory", "sqlite"],
)
def store_factory(request, tmp_path) -> Callable[[], ValidationMetadataStore]:
    """Yield a zero-arg factory for both implementations.

    Using a factory rather than a fixture-instance lets tests build
    multiple independent stores in a single test (e.g. to verify
    cross-instance reads on the SQLite path).
    """
    if request.param == "in-memory":
        return InMemoryValidationMetadataStore
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    return lambda: SQLiteValidationMetadataStore(db_path)


def test_get_missing_returns_none(store_factory):
    store = store_factory()
    assert store.get("ghost-version") is None


def test_upsert_then_get_round_trip(store_factory):
    store = store_factory()
    metadata = ValidationMetadata(
        version_id="ver-1",
        confidence_score=_make_score(overall=0.92),
    )
    store.upsert(metadata)
    fetched = store.get("ver-1")
    assert fetched is not None
    assert fetched.version_id == "ver-1"
    assert fetched.confidence_score is not None
    assert fetched.confidence_score.overall == pytest.approx(0.92)
    assert fetched.confidence_score.signals == metadata.confidence_score.signals
    assert fetched.confidence_score.weights == metadata.confidence_score.weights
    assert fetched.confidence_score.ocr_override_active is False
    assert fetched.confidence_score.computed_by_version == "v1"


def test_upsert_replaces_existing_row(store_factory):
    store = store_factory()
    store.upsert(ValidationMetadata(version_id="ver-1", confidence_score=_make_score(0.4)))
    store.upsert(ValidationMetadata(version_id="ver-1", confidence_score=_make_score(0.9)))
    fetched = store.get("ver-1")
    assert fetched is not None
    assert fetched.confidence_score is not None
    assert fetched.confidence_score.overall == pytest.approx(0.9)


def test_ocr_override_round_trips(store_factory):
    store = store_factory()
    store.upsert(
        ValidationMetadata(
            version_id="ver-ocr",
            confidence_score=_make_score(overall=0.0, ocr_override=True),
        )
    )
    fetched = store.get("ver-ocr")
    assert fetched is not None
    assert fetched.confidence_score is not None
    assert fetched.confidence_score.ocr_override_active is True
    assert fetched.confidence_score.overall == 0.0


def test_routing_decision_round_trips(store_factory):
    """The next-slice ``hitl_router.py`` writes these fields; the
    sidecar must round-trip them so the audit query 'show me every
    auto-validated version' stays a SQL one-liner.
    """
    store = store_factory()
    store.upsert(
        ValidationMetadata(
            version_id="ver-1",
            confidence_score=_make_score(0.95),
            routing_decision="auto",
            validation_method="auto",
            validation_actor=None,
        )
    )
    fetched = store.get("ver-1")
    assert fetched is not None
    assert fetched.routing_decision == "auto"
    assert fetched.validation_method == "auto"
    assert fetched.validation_actor is None


def test_human_route_with_actor_round_trips(store_factory):
    store = store_factory()
    store.upsert(
        ValidationMetadata(
            version_id="ver-h",
            confidence_score=_make_score(0.5),
            routing_decision="human",
            validation_method="human",
            validation_actor="alice",
        )
    )
    fetched = store.get("ver-h")
    assert fetched is not None
    assert fetched.routing_decision == "human"
    assert fetched.validation_actor == "alice"


def test_metadata_without_score_round_trips(store_factory):
    """A scorer-disabled run still records a row (router-only). The
    score block stays None on read.
    """
    store = store_factory()
    store.upsert(ValidationMetadata(version_id="ver-no-score"))
    fetched = store.get("ver-no-score")
    assert fetched is not None
    assert fetched.confidence_score is None
    assert fetched.routing_decision is None


def test_list_all_returns_every_row(store_factory):
    store = store_factory()
    for i in range(3):
        store.upsert(
            ValidationMetadata(
                version_id=f"ver-{i}",
                confidence_score=_make_score(0.5 + 0.1 * i),
            )
        )
    rows = store.list_all()
    assert {row.version_id for row in rows} == {"ver-0", "ver-1", "ver-2"}


def test_upsert_does_not_share_state_with_caller(store_factory):
    """Mutating the input after upsert must NOT mutate the stored row."""
    store = store_factory()
    metadata = ValidationMetadata(
        version_id="ver-1",
        confidence_score=_make_score(0.7),
    )
    store.upsert(metadata)
    # In-memory implementation deep-copies; SQLite does so trivially.
    metadata.confidence_score.overall = 0.0  # type: ignore[union-attr]
    fetched = store.get("ver-1")
    assert fetched is not None
    assert fetched.confidence_score is not None
    assert fetched.confidence_score.overall == pytest.approx(0.7)
