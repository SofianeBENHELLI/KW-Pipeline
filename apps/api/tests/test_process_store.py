"""Tests for the first-class Playbook/Process data model
(#369, ADR-031).

Covers:

* Migration ``0013_processes`` creates the expected schema (both
  tables + both indexes).
* :class:`InMemoryProcessStore` and :class:`SQLiteProcessStore`
  share the same Protocol with parity behaviour for save / get /
  list / delete_for_version. Parametrised so each test runs
  against both backends.
* Schema validation: ``step_number >= 1``, sortedness preserved,
  defaults for ``preconditions`` / ``outcomes``.
* Routes: ``GET /processes`` paginates, ``GET /processes/{id}``
  returns the full body, 404 when missing.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.dependencies import build_persistent_services, build_services
from app.main import create_app
from app.schemas.process import (
    PROCESS_SCHEMA_VERSION,
    Process,
    ProcessStep,
)
from app.services.process_store import (
    InMemoryProcessStore,
    ProcessStore,
    SQLiteProcessStore,
)


def _make_process(
    *,
    process_id: str = "process-1",
    title: str = "Onboard new hire",
    owning_document_id: str = "doc-1",
    version_id: str = "version-1",
    step_count: int = 3,
) -> Process:
    """Sample Process with the given number of steps.

    Each step gets distinct preconditions / outcomes / a referenced
    tool id so the round-trip assertions catch any field-mapping
    regressions.
    """
    steps = [
        ProcessStep(
            step_number=index + 1,
            title=f"Step {index + 1} — do thing",
            body=f"Body for step {index + 1}.",
            preconditions=[f"pre-{index}-a", f"pre-{index}-b"],
            outcomes=[f"outcome-{index}"],
            referenced_tool_id=f"tool-{index}" if index % 2 == 0 else None,
        )
        for index in range(step_count)
    ]
    return Process(
        id=process_id,
        title=title,
        owning_document_id=owning_document_id,
        version_id=version_id,
        steps=steps,
        created_at=datetime.now(UTC),
    )


# ─── Migration ─────────────────────────────────────────────────────


def test_migration_0013_creates_processes_tables(tmp_path: Path) -> None:
    """Booting the persistent services applies the migration."""
    build_persistent_services(tmp_path)

    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('processes', 'process_steps')"
        )
        names = {row[0] for row in cursor.fetchall()}
    finally:
        db.close()
    assert "processes" in names
    assert "process_steps" in names


def test_migration_0013_creates_indexes(tmp_path: Path) -> None:
    """The two read-path indexes are present so the store reads stay cheap."""
    build_persistent_services(tmp_path)

    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        idx_rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name = 'processes'"
        ).fetchall()
    finally:
        db.close()
    names = {row[0] for row in idx_rows}
    assert "idx_processes_owning_document_id" in names
    assert "idx_processes_version_id" in names


def test_migration_0013_step_pk_enforces_uniqueness(tmp_path: Path) -> None:
    """The compound PK ``(process_id, step_number)`` rejects duplicate steps."""
    build_persistent_services(tmp_path)
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    db.execute("PRAGMA foreign_keys = ON")
    try:
        db.execute(
            "INSERT INTO processes (id, title, owning_document_id, version_id, "
            "schema_version, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("p1", "t", "d1", "v1", "v0.1", "2026-01-01T00:00:00+00:00"),
        )
        db.execute(
            "INSERT INTO process_steps (process_id, step_number, title, body, "
            "preconditions_json, outcomes_json, referenced_tool_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("p1", 1, "step1", "body", "[]", "[]", None),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO process_steps (process_id, step_number, title, body, "
                "preconditions_json, outcomes_json, referenced_tool_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("p1", 1, "dup", "body", "[]", "[]", None),
            )
    finally:
        db.close()


def test_migration_0013_step_cascade_on_process_delete(tmp_path: Path) -> None:
    """Deleting the parent ``processes`` row cascades to ``process_steps``."""
    build_persistent_services(tmp_path)
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    db.execute("PRAGMA foreign_keys = ON")
    try:
        db.execute(
            "INSERT INTO processes (id, title, owning_document_id, version_id, "
            "schema_version, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("p1", "t", "d1", "v1", "v0.1", "2026-01-01T00:00:00+00:00"),
        )
        db.execute(
            "INSERT INTO process_steps (process_id, step_number, title, body, "
            "preconditions_json, outcomes_json, referenced_tool_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("p1", 1, "step1", "body", "[]", "[]", None),
        )
        db.commit()
        db.execute("DELETE FROM processes WHERE id = ?", ("p1",))
        db.commit()
        leftover = db.execute(
            "SELECT COUNT(*) FROM process_steps WHERE process_id = ?",
            ("p1",),
        ).fetchone()[0]
    finally:
        db.close()
    assert leftover == 0


# ─── Store contract parity ─────────────────────────────────────────


@pytest.fixture(params=["inmemory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ProcessStore:
    if request.param == "inmemory":
        return InMemoryProcessStore()
    # SQLite store needs the schema in place — boot the persistent
    # services to trigger every migration and then point the store
    # at the same database file.
    build_persistent_services(tmp_path)
    return SQLiteProcessStore(tmp_path / "catalog.sqlite3")


def test_store_get_returns_none_for_missing(store: ProcessStore) -> None:
    assert store.get("does-not-exist") is None


def test_store_round_trips_a_process_with_multiple_steps(store: ProcessStore) -> None:
    process = _make_process(step_count=3)
    store.save_process(process)

    fetched = store.get(process.id)
    assert fetched is not None
    assert fetched.id == process.id
    assert fetched.title == process.title
    assert fetched.owning_document_id == process.owning_document_id
    assert fetched.version_id == process.version_id
    assert fetched.schema_version == PROCESS_SCHEMA_VERSION
    assert len(fetched.steps) == 3
    assert [s.step_number for s in fetched.steps] == [1, 2, 3]
    # Field-by-field on one step so we catch any silent column drop.
    first = fetched.steps[0]
    expected_first = process.steps[0]
    assert first.title == expected_first.title
    assert first.body == expected_first.body
    assert first.preconditions == expected_first.preconditions
    assert first.outcomes == expected_first.outcomes
    assert first.referenced_tool_id == expected_first.referenced_tool_id


def test_store_save_returns_steps_in_step_number_order(store: ProcessStore) -> None:
    """Steps inserted out of order still come back ordered."""
    process = _make_process(step_count=3)
    # Reverse the steps to prove the store re-orders on read.
    process = process.model_copy(update={"steps": list(reversed(process.steps))})
    store.save_process(process)

    fetched = store.get(process.id)
    assert fetched is not None
    assert [s.step_number for s in fetched.steps] == [1, 2, 3]


def test_store_save_overwrites_existing_process(store: ProcessStore) -> None:
    """Replace semantics: re-saving the same id replaces the prior payload."""
    first = _make_process(step_count=3)
    store.save_process(first)
    replacement = _make_process(
        process_id=first.id,
        title="Replaced title",
        step_count=1,
    )
    store.save_process(replacement)

    fetched = store.get(first.id)
    assert fetched is not None
    assert fetched.title == "Replaced title"
    assert len(fetched.steps) == 1


def test_store_save_overrides_created_at_with_server_clock(
    store: ProcessStore,
) -> None:
    """``Process.created_at`` is set server-side; client values are ignored."""
    process = _make_process()
    process = process.model_copy(
        update={"created_at": datetime(2000, 1, 1, tzinfo=UTC)},
    )
    before = datetime.now(UTC)
    store.save_process(process)
    fetched = store.get(process.id)
    assert fetched is not None
    # SQLite codec round-trip drops microseconds in some Python
    # versions; allow a generous window.
    assert fetched.created_at >= before.replace(microsecond=0)


def test_store_list_paginates(store: ProcessStore) -> None:
    """Listing with ``limit=2`` returns 2 rows + a cursor that pages."""
    for index in range(5):
        store.save_process(
            _make_process(
                process_id=f"p{index}",
                version_id=f"v{index}",
                title=f"Process {index}",
            )
        )

    page_one, cursor = store.list(limit=2)
    assert len(page_one) == 2
    assert cursor is not None

    page_two, cursor_two = store.list(cursor=cursor, limit=2)
    assert len(page_two) == 2
    assert cursor_two is not None
    assert {p.id for p in page_one}.isdisjoint({p.id for p in page_two})

    page_three, cursor_three = store.list(cursor=cursor_two, limit=2)
    assert len(page_three) == 1
    assert cursor_three is None


def test_store_list_returns_only_summaries(store: ProcessStore) -> None:
    """``list`` returns :class:`ProcessSummary` rows — no ``steps`` field."""
    process = _make_process(step_count=3)
    store.save_process(process)
    summaries, _ = store.list(limit=10)
    assert len(summaries) == 1
    summary = summaries[0]
    # ProcessSummary has no ``steps`` field — confirm via attribute.
    assert not hasattr(summary, "steps")
    assert summary.id == process.id
    assert summary.title == process.title
    assert summary.owning_document_id == process.owning_document_id
    assert summary.version_id == process.version_id
    assert summary.schema_version == PROCESS_SCHEMA_VERSION


def test_store_list_empty_returns_empty_page_and_no_cursor(store: ProcessStore) -> None:
    summaries, cursor = store.list(limit=10)
    assert summaries == []
    assert cursor is None


def test_store_delete_for_version_removes_owned_processes(store: ProcessStore) -> None:
    """``delete_for_version`` drops every Process for the version."""
    store.save_process(_make_process(process_id="p1", version_id="vA"))
    store.save_process(_make_process(process_id="p2", version_id="vA"))
    store.save_process(_make_process(process_id="p3", version_id="vB"))

    deleted = store.delete_for_version("vA")
    assert deleted == 2
    assert store.get("p1") is None
    assert store.get("p2") is None
    # Other versions untouched.
    assert store.get("p3") is not None


def test_store_delete_for_version_zero_when_unknown(store: ProcessStore) -> None:
    assert store.delete_for_version("never-existed") == 0


def test_store_delete_for_version_cascades_step_rows_in_sqlite(
    tmp_path: Path,
) -> None:
    """SQLite-specific: the FK CASCADE actually drops step rows on
    ``delete_for_version``."""
    build_persistent_services(tmp_path)
    sqlite_store = SQLiteProcessStore(tmp_path / "catalog.sqlite3")
    sqlite_store.save_process(_make_process(process_id="p1", version_id="vA"))

    sqlite_store.delete_for_version("vA")

    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        leftover = db.execute(
            "SELECT COUNT(*) FROM process_steps WHERE process_id = ?",
            ("p1",),
        ).fetchone()[0]
    finally:
        db.close()
    assert leftover == 0


# ─── Schema validation ─────────────────────────────────────────────


def test_process_step_rejects_step_number_below_one() -> None:
    """``step_number`` is 1-indexed; ``0`` is rejected at schema load."""
    with pytest.raises(ValidationError):
        ProcessStep(
            step_number=0,
            title="bad",
            body="b",
            preconditions=[],
            outcomes=[],
        )


def test_process_step_defaults_preconditions_and_outcomes_to_empty_lists() -> None:
    """Omitted ``preconditions`` / ``outcomes`` default to empty lists."""
    step = ProcessStep(step_number=1, title="t", body="b")
    assert step.preconditions == []
    assert step.outcomes == []
    assert step.referenced_tool_id is None


# ─── Routes ────────────────────────────────────────────────────────


def _client_with_process(process: Process) -> TestClient:
    """Build a TestClient whose process_store has ``process`` saved."""
    services = build_services()
    services.process_store.save_process(process)
    return TestClient(create_app(services))


def test_route_get_processes_paginates() -> None:
    services = build_services()
    for index in range(3):
        services.process_store.save_process(
            _make_process(
                process_id=f"p{index}",
                version_id=f"v{index}",
            )
        )
    client = TestClient(create_app(services))

    response = client.get("/processes?limit=2")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == PROCESS_SCHEMA_VERSION
    assert len(body["items"]) == 2
    assert body["next_cursor"]

    response_two = client.get(f"/processes?cursor={body['next_cursor']}&limit=2")
    assert response_two.status_code == 200
    body_two = response_two.json()
    assert len(body_two["items"]) == 1
    assert body_two["next_cursor"] is None


def test_route_get_processes_returns_empty_envelope_when_store_empty() -> None:
    services = build_services()
    client = TestClient(create_app(services))
    response = client.get("/processes")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


def test_route_get_processes_400_for_invalid_cursor() -> None:
    services = build_services()
    client = TestClient(create_app(services))
    response = client.get("/processes?cursor=not-a-real-cursor")
    assert response.status_code == 400


def test_route_get_process_returns_full_body_with_steps() -> None:
    process = _make_process(step_count=3)
    client = _client_with_process(process)

    response = client.get(f"/processes/{process.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == process.id
    assert body["title"] == process.title
    assert body["owning_document_id"] == process.owning_document_id
    assert body["version_id"] == process.version_id
    assert body["schema_version"] == PROCESS_SCHEMA_VERSION
    assert len(body["steps"]) == 3
    assert [step["step_number"] for step in body["steps"]] == [1, 2, 3]
    first_step = body["steps"][0]
    expected_first = process.steps[0]
    assert first_step["title"] == expected_first.title
    assert first_step["body"] == expected_first.body
    assert first_step["preconditions"] == expected_first.preconditions
    assert first_step["outcomes"] == expected_first.outcomes
    assert first_step["referenced_tool_id"] == expected_first.referenced_tool_id


def test_route_get_process_404_when_missing() -> None:
    services = build_services()
    client = TestClient(create_app(services))
    response = client.get("/processes/does-not-exist")
    assert response.status_code == 404


# ─── Boot wiring ───────────────────────────────────────────────────


def test_build_services_wires_in_memory_process_store() -> None:
    services = build_services()
    assert isinstance(services.process_store, InMemoryProcessStore)


def test_build_persistent_services_wires_sqlite_process_store(
    tmp_path: Path,
) -> None:
    services = build_persistent_services(tmp_path)
    assert isinstance(services.process_store, SQLiteProcessStore)
    # The store points at the catalog database (single SQLite file
    # carries every governance table per ADR-031).
    sqlite_store = cast(SQLiteProcessStore, services.process_store)
    assert sqlite_store._db_path == tmp_path / "catalog.sqlite3"
