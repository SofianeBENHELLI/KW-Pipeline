"""Tests for the LLM business-taxonomy allocation data model + store +
read API (EPIC-1 slice 1.3, #340).

Covers:

* Migration ``0015_chunk_taxonomy_allocations`` creates the table +
  indexes.
* :class:`InMemoryChunkTaxonomyAllocationStore` and
  :class:`SQLiteChunkTaxonomyAllocationStore` implement the same
  Protocol with parity behaviour for save / list / delete
  (parametrized fixture).
* Wire schema validation: confidence range, assignments persistence
  (including empty case for "LLM ran, found nothing").
* ``GET /knowledge/taxonomy-allocations`` returns the list shape;
  filters on ``chunk_id`` / ``document_id``; cursor pagination
  round-trips; ``chunk_id`` wins when both filters are supplied.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.dependencies import build_persistent_services
from app.main import create_app
from app.schemas.chunk_taxonomy_allocation import (
    CHUNK_TAXONOMY_ALLOCATION_SCHEMA_VERSION,
    BusinessCategoryAssignment,
    ChunkTaxonomyAllocation,
    ChunkTaxonomyAllocationsListResponse,
)
from app.services.chunk_taxonomy_allocation_store import (
    DEFAULT_ALLOCATIONS_PAGE_LIMIT,
    InMemoryChunkTaxonomyAllocationStore,
    SQLiteChunkTaxonomyAllocationStore,
)
from app.services.migrations import _run_migrations


def _seed_sqlite_schema(db_path: Path) -> None:
    """Run migrations + seed the parent rows the FK depends on."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        _run_migrations(conn)
        for doc_id, vid in (("doc-1", "ver-1"), ("doc-2", "ver-2")):
            conn.execute(
                "INSERT INTO documents (id, original_filename, latest_version_id, created_at)"
                " VALUES (?, ?, ?, ?)",
                (doc_id, "fixture.txt", vid, "2026-05-16T12:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO document_versions ("
                "  id, document_id, version_number, filename, content_type, file_size,"
                "  sha256, storage_uri, status, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    vid,
                    doc_id,
                    1,
                    "fixture.txt",
                    "text/plain",
                    10,
                    "0" * 64,
                    "memory://0",
                    "STORED",
                    "2026-05-16T12:00:00+00:00",
                ),
            )
    finally:
        conn.close()


def _placeholder_extracted_at() -> datetime:
    return datetime(2026, 5, 16, tzinfo=UTC)


def _make_assignment(
    *,
    category_id: str = "hr.hybrid_work",
    confidence: float = 0.82,
    rationale: str = "The chunk discusses hybrid working schedules.",
    supporting_concept_texts: list[str] | None = None,
) -> BusinessCategoryAssignment:
    return BusinessCategoryAssignment(
        category_id=category_id,
        confidence=confidence,
        rationale=rationale,
        supporting_concept_texts=supporting_concept_texts or ["hybrid"],
    )


def _make_allocation(
    *,
    alloc_id: str = "alloc-1",
    chunk_id: str = "chunk-1",
    section_id: str = "chunk-1",
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    assignments: list[BusinessCategoryAssignment] | None = None,
    taxonomy_fingerprint: str = "deadbeef" * 2,
    model_id: str = "claude-sonnet-4-5",
    prompt_hash: str = "cafebabe" * 2,
) -> ChunkTaxonomyAllocation:
    return ChunkTaxonomyAllocation(
        id=alloc_id,
        chunk_id=chunk_id,
        section_id=section_id,
        document_id=document_id,
        version_id=version_id,
        assignments=assignments if assignments is not None else [_make_assignment()],
        taxonomy_fingerprint=taxonomy_fingerprint,
        model_id=model_id,
        prompt_hash=prompt_hash,
        extracted_at=_placeholder_extracted_at(),
    )


# ─── Migration ─────────────────────────────────────────────────────


def test_migration_0015_creates_table(tmp_path: Path) -> None:
    build_persistent_services(tmp_path)
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name = 'chunk_taxonomy_allocations'"
        )
        assert cursor.fetchone() is not None
    finally:
        db.close()


def test_migration_0015_creates_all_three_indexes(tmp_path: Path) -> None:
    build_persistent_services(tmp_path)
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='chunk_taxonomy_allocations'"
            )
        }
    finally:
        db.close()
    assert "idx_chunk_taxonomy_allocations_document_id" in names
    assert "idx_chunk_taxonomy_allocations_chunk_id" in names
    assert "idx_chunk_taxonomy_allocations_version_id" in names


def test_migration_0015_recorded_in_schema_migrations(tmp_path: Path) -> None:
    build_persistent_services(tmp_path)
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        rows = {row[0] for row in db.execute("SELECT id FROM schema_migrations")}
    finally:
        db.close()
    assert "0015_chunk_taxonomy_allocations" in rows


# ─── Schema validation ─────────────────────────────────────────────


def test_assignment_confidence_must_be_in_unit_interval() -> None:
    for bad in (-0.01, 1.01):
        with pytest.raises(ValidationError):
            BusinessCategoryAssignment(
                category_id="x",
                confidence=bad,
                rationale="y",
            )


def test_assignment_rationale_is_required() -> None:
    with pytest.raises(ValidationError):
        BusinessCategoryAssignment(
            category_id="x",
            confidence=0.5,
            rationale="",  # min_length=1
        )


def test_allocation_persists_empty_assignment_list() -> None:
    """The empty case is a meaningful audit row, not a default-deny
    rejection — slice 1.3's "LLM ran but nothing matched" signal."""
    allocation = _make_allocation(assignments=[])
    assert allocation.assignments == []
    # The row itself is still valid (it carries fingerprint / model
    # / prompt for traceability).
    assert allocation.id == "alloc-1"


def test_allocation_schema_version_is_v0_1_literal() -> None:
    assert CHUNK_TAXONOMY_ALLOCATION_SCHEMA_VERSION == "v0.1"
    allocation = _make_allocation()
    assert allocation.schema_version == "v0.1"


# ─── Store parity ─────────────────────────────────────────────────


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    if request.param == "memory":
        return InMemoryChunkTaxonomyAllocationStore()
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    return SQLiteChunkTaxonomyAllocationStore(db_path)


def test_store_starts_empty(store: Any) -> None:
    items, next_cursor = store.list_for_document("doc-1")
    assert items == []
    assert next_cursor is None
    items, next_cursor = store.list_for_chunk("chunk-1")
    assert items == []
    items, next_cursor = store.list_all()
    assert items == []


def test_store_save_then_list_round_trips_allocation(store: Any) -> None:
    allocation = _make_allocation(
        assignments=[
            _make_assignment(category_id="hr"),
            _make_assignment(category_id="hr.hybrid_work", confidence=0.95),
        ]
    )
    store.save_allocations([allocation])
    items, _ = store.list_for_document("doc-1")
    assert len(items) == 1
    fetched = items[0]
    assert fetched.id == allocation.id
    assert fetched.chunk_id == allocation.chunk_id
    assert fetched.taxonomy_fingerprint == allocation.taxonomy_fingerprint
    assert fetched.model_id == allocation.model_id
    assert fetched.prompt_hash == allocation.prompt_hash
    assert [a.category_id for a in fetched.assignments] == ["hr", "hr.hybrid_work"]
    assert fetched.assignments[1].confidence == 0.95
    # Store stamps a server-authoritative extracted_at on save.
    assert fetched.extracted_at >= datetime(2026, 5, 16, tzinfo=UTC)


def test_store_round_trips_empty_assignments(store: Any) -> None:
    """The empty case is persisted (audit evidence the LLM ran)."""
    allocation = _make_allocation(assignments=[])
    store.save_allocations([allocation])
    items, _ = store.list_all()
    assert len(items) == 1
    assert items[0].assignments == []


def test_store_save_handles_empty_batch(store: Any) -> None:
    store.save_allocations([])
    items, _ = store.list_all()
    assert items == []


def test_store_filters_by_document_id(store: Any) -> None:
    store.save_allocations(
        [
            _make_allocation(alloc_id="a-1", document_id="doc-1", version_id="ver-1"),
            _make_allocation(alloc_id="a-2", document_id="doc-2", version_id="ver-2"),
        ]
    )
    items, _ = store.list_for_document("doc-1")
    assert [a.id for a in items] == ["a-1"]
    items, _ = store.list_for_document("doc-2")
    assert [a.id for a in items] == ["a-2"]


def test_store_filters_by_chunk_id(store: Any) -> None:
    store.save_allocations(
        [
            _make_allocation(
                alloc_id="a-1",
                chunk_id="chunk-A",
                section_id="chunk-A",
                version_id="ver-1",
            ),
            _make_allocation(
                alloc_id="a-2",
                chunk_id="chunk-B",
                section_id="chunk-B",
                document_id="doc-2",
                version_id="ver-2",
            ),
        ]
    )
    items, _ = store.list_for_chunk("chunk-A")
    assert [a.id for a in items] == ["a-1"]


def test_store_list_all_returns_every_allocation(store: Any) -> None:
    store.save_allocations(
        [
            _make_allocation(alloc_id="a-1", document_id="doc-1", version_id="ver-1"),
            _make_allocation(alloc_id="a-2", document_id="doc-2", version_id="ver-2"),
        ]
    )
    items, _ = store.list_all()
    assert {a.id for a in items} == {"a-1", "a-2"}


def test_store_pagination_walks_all_pages(store: Any) -> None:
    allocations = [
        _make_allocation(alloc_id=f"a-{i:02d}", chunk_id=f"chunk-{i}", section_id=f"chunk-{i}")
        for i in range(5)
    ]
    # For SQLite, every allocation needs the same version_id (FK
    # constraint); ver-1 was seeded. The in-memory store doesn't
    # care.
    store.save_allocations(allocations)

    page1, c1 = store.list_for_document("doc-1", limit=2)
    assert len(page1) == 2
    assert c1 is not None
    page2, c2 = store.list_for_document("doc-1", cursor=c1, limit=2)
    assert len(page2) == 2
    page3, c3 = store.list_for_document("doc-1", cursor=c2, limit=2)
    assert len(page3) == 1
    assert c3 is None
    seen = {a.id for a in page1 + page2 + page3}
    assert seen == {f"a-{i:02d}" for i in range(5)}


def test_store_pagination_invalid_cursor_raises(store: Any) -> None:
    from app.services.catalog_store import InvalidCursor

    with pytest.raises(InvalidCursor):
        store.list_for_document("doc-1", cursor="not-a-real-cursor")


def test_store_delete_for_version_removes_only_that_version(store: Any) -> None:
    store.save_allocations(
        [
            _make_allocation(alloc_id="a-1", document_id="doc-1", version_id="ver-1"),
            _make_allocation(alloc_id="a-2", document_id="doc-2", version_id="ver-2"),
        ]
    )
    removed = store.delete_for_version("ver-1")
    assert removed == 1
    items, _ = store.list_all()
    assert [a.id for a in items] == ["a-2"]


def test_store_delete_for_version_idempotent(store: Any) -> None:
    store.save_allocations([_make_allocation()])
    assert store.delete_for_version("ver-1") == 1
    assert store.delete_for_version("ver-1") == 0


def test_store_default_page_limit_constant_is_sane() -> None:
    assert 1 <= DEFAULT_ALLOCATIONS_PAGE_LIMIT <= 200


# ─── SQLite-only: cascade + FK ─────────────────────────────────────


def test_sqlite_store_rejects_unknown_version_id(tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    store = SQLiteChunkTaxonomyAllocationStore(db_path)
    bad = _make_allocation(version_id="ver-nonexistent")
    with pytest.raises(sqlite3.IntegrityError):
        store.save_allocations([bad])


def test_sqlite_store_cascade_deletes_when_version_deleted(tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    store = SQLiteChunkTaxonomyAllocationStore(db_path)
    store.save_allocations([_make_allocation(version_id="ver-1")])

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM document_versions WHERE id = ?", ("ver-1",))
        conn.commit()
    finally:
        conn.close()

    items, _ = store.list_all()
    assert items == []


# ─── Route ─────────────────────────────────────────────────────────


def test_route_returns_empty_list_when_no_allocations() -> None:
    app = create_app()
    client = TestClient(app)
    response = client.get("/knowledge/taxonomy-allocations")
    assert response.status_code == 200
    parsed = ChunkTaxonomyAllocationsListResponse.model_validate(response.json())
    assert parsed.items == []
    assert parsed.next_cursor is None
    assert parsed.schema_version == "v0.1"


def test_route_filters_by_document_id() -> None:
    app = create_app()
    services = app.state.services
    services.chunk_taxonomy_allocation_store.save_allocations(
        [
            _make_allocation(alloc_id="a-1", document_id="doc-1", version_id="ver-1"),
            _make_allocation(alloc_id="a-2", document_id="doc-2", version_id="ver-2"),
        ]
    )
    client = TestClient(app)
    response = client.get("/knowledge/taxonomy-allocations?document_id=doc-1")
    assert response.status_code == 200
    parsed = ChunkTaxonomyAllocationsListResponse.model_validate(response.json())
    assert [a.id for a in parsed.items] == ["a-1"]


def test_route_filters_by_chunk_id() -> None:
    app = create_app()
    services = app.state.services
    services.chunk_taxonomy_allocation_store.save_allocations(
        [
            _make_allocation(
                alloc_id="a-1",
                chunk_id="chunk-A",
                section_id="chunk-A",
            ),
            _make_allocation(
                alloc_id="a-2",
                chunk_id="chunk-B",
                section_id="chunk-B",
            ),
        ]
    )
    client = TestClient(app)
    response = client.get("/knowledge/taxonomy-allocations?chunk_id=chunk-A")
    parsed = ChunkTaxonomyAllocationsListResponse.model_validate(response.json())
    assert [a.id for a in parsed.items] == ["a-1"]


def test_route_returns_400_when_both_filters_supplied() -> None:
    """``chunk_id`` and ``document_id`` are mutually exclusive — silent
    wins behaviour would be surprising and an intersection contract is
    not yet documented. The route rejects with 400 so the caller knows
    they violated the contract."""
    app = create_app()
    client = TestClient(app)
    response = client.get("/knowledge/taxonomy-allocations?chunk_id=chunk-A&document_id=doc-1")
    assert response.status_code == 400
    assert "chunk_id" in response.json()["detail"]
    assert "document_id" in response.json()["detail"]


def test_route_returns_400_on_invalid_cursor() -> None:
    app = create_app()
    client = TestClient(app)
    response = client.get("/knowledge/taxonomy-allocations?cursor=not-real")
    assert response.status_code == 400
    assert "Invalid cursor" in response.json()["detail"]
