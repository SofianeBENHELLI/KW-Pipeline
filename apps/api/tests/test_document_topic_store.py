"""Tests for the LLM-extracted document-topic data model + store +
read API (#411, ADR-031).

Covers:

* Migration ``0014_document_topics`` creates the table + indexes.
* :class:`InMemoryDocumentTopicStore` and
  :class:`SQLiteDocumentTopicStore` implement the same Protocol with
  parity behaviour for save / list / delete (parametrized fixture).
* Wire schema validation: ``supporting_chunk_ids`` must be non-empty.
* ``GET /knowledge/topics`` returns the list shape; ``document_id``
  query param filters; cursor pagination round-trips.
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
from app.schemas.document_topic import (
    DOCUMENT_TOPIC_SCHEMA_VERSION,
    DocumentTopic,
    DocumentTopicsListResponse,
)
from app.services.document_topic_store import (
    DEFAULT_TOPICS_PAGE_LIMIT,
    InMemoryDocumentTopicStore,
    SQLiteDocumentTopicStore,
)
from app.services.migrations import _run_migrations

_SEEDED_VERSION_IDS = ("ver-1", "ver-2")


def _seed_sqlite_schema(db_path: Path) -> None:
    """Run migrations + seed the parent rows the FK depends on."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        _run_migrations(conn)
        conn.execute(
            "INSERT INTO documents (id, original_filename, latest_version_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("doc-1", "fixture.txt", "ver-1", "2026-05-11T12:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO documents (id, original_filename, latest_version_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("doc-2", "fixture-2.txt", "ver-2", "2026-05-11T12:00:00+00:00"),
        )
        for doc_id, vid in (("doc-1", "ver-1"), ("doc-2", "ver-2")):
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
                    "2026-05-11T12:00:00+00:00",
                ),
            )
    finally:
        conn.close()


def _placeholder_extracted_at() -> datetime:
    """Pre-store timestamp placeholder; the store overwrites on save."""
    return datetime(2026, 5, 11, tzinfo=UTC)


def _make_topic(
    *,
    topic_id: str = "topic-1",
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    label: str = "Microservices architecture",
    summary: str = "How the system splits services across the platform.",
    keywords: list[str] | None = None,
    confidence: float = 0.86,
    supporting_chunk_ids: list[str] | None = None,
) -> DocumentTopic:
    return DocumentTopic(
        id=topic_id,
        document_id=document_id,
        version_id=version_id,
        label=label,
        summary=summary,
        keywords=keywords or ["microservices", "platform"],
        confidence=confidence,
        extracted_at=_placeholder_extracted_at(),
        supporting_chunk_ids=supporting_chunk_ids or ["chunk-1"],
    )


# ─── Migration ─────────────────────────────────────────────────────


def test_migration_0014_creates_document_topics_table(tmp_path: Path) -> None:
    """Booting the persistent services applies the migration."""
    build_persistent_services(tmp_path)

    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = 'document_topics'"
        )
        assert cursor.fetchone() is not None, "document_topics table missing"
    finally:
        db.close()


def test_migration_0014_creates_document_topics_indexes(tmp_path: Path) -> None:
    build_persistent_services(tmp_path)
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='document_topics'"
            )
        }
    finally:
        db.close()
    assert "idx_document_topics_document_id" in names
    assert "idx_document_topics_version_id" in names


def test_migration_0014_records_in_schema_migrations(tmp_path: Path) -> None:
    build_persistent_services(tmp_path)
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        rows = {row[0] for row in db.execute("SELECT id FROM schema_migrations")}
    finally:
        db.close()
    assert "0014_document_topics" in rows


# ─── Schema validation ─────────────────────────────────────────────


def test_topic_requires_at_least_one_supporting_chunk_id() -> None:
    with pytest.raises(ValidationError):
        DocumentTopic(
            id="t-1",
            document_id="doc-1",
            version_id="ver-1",
            label="x",
            summary="y",
            keywords=[],
            confidence=0.5,
            extracted_at=_placeholder_extracted_at(),
            supporting_chunk_ids=[],
        )


def test_topic_confidence_must_be_in_unit_interval() -> None:
    for bad in (-0.01, 1.01):
        with pytest.raises(ValidationError):
            DocumentTopic(
                id="t-1",
                document_id="doc-1",
                version_id="ver-1",
                label="x",
                summary="y",
                keywords=[],
                confidence=bad,
                extracted_at=_placeholder_extracted_at(),
                supporting_chunk_ids=["c-1"],
            )


def test_topic_schema_version_is_v0_1_literal() -> None:
    assert DOCUMENT_TOPIC_SCHEMA_VERSION == "v0.1"
    topic = _make_topic()
    assert topic.schema_version == "v0.1"


# ─── Store parity (in-memory vs SQLite) ────────────────────────────


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    if request.param == "memory":
        return InMemoryDocumentTopicStore()
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    return SQLiteDocumentTopicStore(db_path)


def test_store_starts_empty(store: Any) -> None:
    items, next_cursor = store.list_for_document("doc-1")
    assert items == []
    assert next_cursor is None
    items, next_cursor = store.list_all()
    assert items == []
    assert next_cursor is None


def test_store_save_then_list_round_trips_topic(store: Any) -> None:
    topic = _make_topic()
    store.save_topics([topic])
    items, next_cursor = store.list_for_document("doc-1")
    assert len(items) == 1
    fetched = items[0]
    assert fetched.id == topic.id
    assert fetched.label == topic.label
    assert fetched.summary == topic.summary
    assert fetched.keywords == topic.keywords
    assert fetched.confidence == topic.confidence
    assert fetched.supporting_chunk_ids == topic.supporting_chunk_ids
    # Server stamps a fresh extracted_at on save (post-2026-05-11).
    assert fetched.extracted_at >= datetime(2026, 5, 11, tzinfo=UTC)
    assert next_cursor is None


def test_store_save_handles_empty_batch(store: Any) -> None:
    store.save_topics([])
    items, _ = store.list_for_document("doc-1")
    assert items == []


def test_store_round_trips_long_keyword_and_chunk_lists(store: Any) -> None:
    topic = _make_topic(
        keywords=[f"kw-{i}" for i in range(20)],
        supporting_chunk_ids=[f"chunk-{i}" for i in range(50)],
    )
    store.save_topics([topic])
    items, _ = store.list_for_document("doc-1")
    assert items[0].keywords == topic.keywords
    assert items[0].supporting_chunk_ids == topic.supporting_chunk_ids


def test_store_filters_by_document_id(store: Any) -> None:
    store.save_topics(
        [
            _make_topic(topic_id="t-a", document_id="doc-1", version_id="ver-1"),
            _make_topic(topic_id="t-b", document_id="doc-2", version_id="ver-2"),
        ]
    )
    items, _ = store.list_for_document("doc-1")
    assert [t.id for t in items] == ["t-a"]
    items, _ = store.list_for_document("doc-2")
    assert [t.id for t in items] == ["t-b"]


def test_store_list_all_returns_every_topic(store: Any) -> None:
    store.save_topics(
        [
            _make_topic(topic_id="t-a", document_id="doc-1", version_id="ver-1"),
            _make_topic(topic_id="t-b", document_id="doc-2", version_id="ver-2"),
        ]
    )
    items, _ = store.list_all()
    assert {t.id for t in items} == {"t-a", "t-b"}


def test_store_pagination_walks_all_pages(store: Any) -> None:
    topics = [
        _make_topic(topic_id=f"t-{i:02d}", supporting_chunk_ids=[f"chunk-{i}"]) for i in range(5)
    ]
    store.save_topics(topics)

    page1, cursor1 = store.list_for_document("doc-1", limit=2)
    assert len(page1) == 2
    assert cursor1 is not None
    page2, cursor2 = store.list_for_document("doc-1", cursor=cursor1, limit=2)
    assert len(page2) == 2
    page3, cursor3 = store.list_for_document("doc-1", cursor=cursor2, limit=2)
    assert len(page3) == 1  # last page
    assert cursor3 is None
    seen = {t.id for t in page1 + page2 + page3}
    assert seen == {f"t-{i:02d}" for i in range(5)}


def test_store_pagination_invalid_cursor_raises(store: Any) -> None:
    from app.services.catalog_store import InvalidCursor

    with pytest.raises(InvalidCursor):
        store.list_for_document("doc-1", cursor="not-a-real-cursor")


def test_store_delete_for_version_removes_only_that_version(store: Any) -> None:
    store.save_topics(
        [
            _make_topic(topic_id="t-a", document_id="doc-1", version_id="ver-1"),
            _make_topic(topic_id="t-b", document_id="doc-2", version_id="ver-2"),
        ]
    )
    removed = store.delete_for_version("ver-1")
    assert removed == 1
    items, _ = store.list_all()
    assert [t.id for t in items] == ["t-b"]


def test_store_delete_for_version_idempotent(store: Any) -> None:
    store.save_topics([_make_topic(version_id="ver-1")])
    assert store.delete_for_version("ver-1") == 1
    # Second call: nothing left, returns 0 cleanly.
    assert store.delete_for_version("ver-1") == 0


def test_store_default_page_limit_constant_is_sane() -> None:
    assert 1 <= DEFAULT_TOPICS_PAGE_LIMIT <= 200


# ─── SQLite-only: cascade + FK enforcement ─────────────────────────


def test_sqlite_store_rejects_topic_with_unknown_version_id(tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    store = SQLiteDocumentTopicStore(db_path)
    bad = _make_topic(version_id="ver-does-not-exist")
    with pytest.raises(sqlite3.IntegrityError):
        store.save_topics([bad])


def test_sqlite_store_cascade_deletes_topics_when_parent_version_deleted(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    store = SQLiteDocumentTopicStore(db_path)
    store.save_topics([_make_topic(version_id="ver-1")])

    # Delete the parent version row — the FK ON DELETE CASCADE should
    # clean the topic transparently.
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


def test_route_returns_empty_list_when_no_topics_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app()
    client = TestClient(app)
    response = client.get("/knowledge/topics")
    assert response.status_code == 200
    body = response.json()
    parsed = DocumentTopicsListResponse.model_validate(body)
    assert parsed.items == []
    assert parsed.next_cursor is None
    assert parsed.schema_version == "v0.1"


def test_route_filters_by_document_id(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    services = app.state.services
    services.document_topic_store.save_topics(
        [
            _make_topic(topic_id="t-a", document_id="doc-1", version_id="ver-1"),
            _make_topic(topic_id="t-b", document_id="doc-2", version_id="ver-2"),
        ]
    )
    client = TestClient(app)

    # Filtered by document_id=doc-1 → just t-a.
    response = client.get("/knowledge/topics?document_id=doc-1")
    assert response.status_code == 200
    parsed = DocumentTopicsListResponse.model_validate(response.json())
    assert [t.id for t in parsed.items] == ["t-a"]

    # No filter → both.
    response = client.get("/knowledge/topics")
    parsed = DocumentTopicsListResponse.model_validate(response.json())
    assert {t.id for t in parsed.items} == {"t-a", "t-b"}


def test_route_returns_400_on_invalid_cursor() -> None:
    app = create_app()
    client = TestClient(app)
    response = client.get("/knowledge/topics?cursor=not-real")
    assert response.status_code == 400
    assert "Invalid cursor" in response.json()["detail"]
