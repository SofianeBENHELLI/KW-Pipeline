"""Demo/operator provenance on documents (Explorer enterprise Sprint 1).

Covers the four layers of the demo/production separation:

* Migration ``0016_document_origin`` — column + backfill of
  pre-existing demo-named rows.
* ``CatalogStore.mark_documents_origin`` — both store backends,
  idempotency, archived-row inclusion.
* ``GET /documents?include_demo=false`` — the read-surface filter.
* ``DemoDatasetService.reset`` — re-stamps origin before archiving so
  crashed mid-flight loads can't strand mistagged rows.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.schemas.document import Document, DocumentVersion
from app.schemas.scope import Scope
from app.services import migrations as mig_module
from app.services.catalog_store import InMemoryCatalogStore, SQLiteCatalogStore
from app.services.demo_dataset import DEMO_FIXTURE_FILENAMES, DemoDatasetService
from app.services.migrations import _run_migrations

# Any real fixture filename works for the demo-named cases; pin one so
# the assertions read clearly.
_DEMO_NAME = "quality_iso9001_handbook.txt"


def _make_version(
    document_id: str,
    version_id: str,
    *,
    filename: str,
    sha256: str,
) -> DocumentVersion:
    from app.models.document import DocumentVersionStatus

    return DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=1,
        filename=filename,
        content_type="text/plain",
        file_size=10,
        sha256=sha256,
        storage_uri=f"memory://documents/{version_id}/{filename}",
        status=DocumentVersionStatus.STORED,
    )


def _save(store, *, document_id: str, filename: str, sha256: str) -> Document:
    version = _make_version(document_id, f"{document_id}-v1", filename=filename, sha256=sha256)
    document = Document.with_first_version(version)
    store.save_document_with_version(document, version)
    return document


# ─── Migration 0016 ────────────────────────────────────────────────────


def test_fresh_db_has_origin_column_defaulting_to_operator(tmp_path):
    store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")
    _save(store, document_id="doc-1", filename="real_report.txt", sha256="a" * 64)
    fetched = store.get_document("doc-1")
    assert fetched is not None
    assert fetched.origin == "operator"


def test_migration_backfills_existing_demo_named_rows(tmp_path, monkeypatch):
    """Rows that predate the origin column flip to 'demo' by filename."""
    db_path = tmp_path / "catalog.sqlite3"
    original = mig_module.MIGRATIONS[:]
    pre_0016 = [m for m in original if m[0] != "0016_document_origin"]

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    monkeypatch.setattr(mig_module, "MIGRATIONS", pre_0016)
    _run_migrations(conn)

    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO documents (id, original_filename, latest_version_id, created_at)"
        " VALUES (?, ?, ?, ?)",
        ("doc-demo", _DEMO_NAME, "ver-demo", now),
    )
    conn.execute(
        "INSERT INTO documents (id, original_filename, latest_version_id, created_at)"
        " VALUES (?, ?, ?, ?)",
        ("doc-real", "real_report.txt", "ver-real", now),
    )

    monkeypatch.setattr(mig_module, "MIGRATIONS", original)
    _run_migrations(conn)

    rows = {
        row["id"]: row["origin"]
        for row in conn.execute("SELECT id, origin FROM documents").fetchall()
    }
    conn.close()
    assert rows == {"doc-demo": "demo", "doc-real": "operator"}


# ─── mark_documents_origin (both backends) ─────────────────────────────


@pytest.fixture(params=["in_memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "in_memory":
        return InMemoryCatalogStore()
    return SQLiteCatalogStore(tmp_path / "catalog.sqlite3")


def test_mark_documents_origin_stamps_matching_rows(store):
    _save(store, document_id="doc-demo", filename=_DEMO_NAME, sha256="a" * 64)
    _save(store, document_id="doc-real", filename="real_report.txt", sha256="b" * 64)

    changed = store.mark_documents_origin(frozenset({_DEMO_NAME}), origin="demo")

    assert changed == 1
    assert store.get_document("doc-demo").origin == "demo"
    assert store.get_document("doc-real").origin == "operator"


def test_mark_documents_origin_is_idempotent(store):
    _save(store, document_id="doc-demo", filename=_DEMO_NAME, sha256="a" * 64)
    assert store.mark_documents_origin(frozenset({_DEMO_NAME}), origin="demo") == 1
    assert store.mark_documents_origin(frozenset({_DEMO_NAME}), origin="demo") == 0


def test_mark_documents_origin_includes_archived_rows(store):
    document = _save(store, document_id="doc-demo", filename=_DEMO_NAME, sha256="a" * 64)
    store.flag_document_archived(document.id, archived_at=datetime.now(UTC), actor="test")

    assert store.mark_documents_origin(frozenset({_DEMO_NAME}), origin="demo") == 1


def test_mark_documents_origin_empty_set_is_noop(store):
    _save(store, document_id="doc-demo", filename=_DEMO_NAME, sha256="a" * 64)
    assert store.mark_documents_origin(frozenset(), origin="demo") == 0


def test_sqlite_origin_survives_round_trip(tmp_path):
    """origin='demo' persisted at save time is read back as-is."""
    store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")
    version = _make_version("doc-1", "ver-1", filename=_DEMO_NAME, sha256="a" * 64)
    document = Document.with_first_version(version).model_copy(update={"origin": "demo"})
    store.save_document_with_version(document, version)
    assert store.get_document("doc-1").origin == "demo"


# ─── GET /documents?include_demo= ──────────────────────────────────────


def _link_personal_scope(services, document_id: str, user_id: str = "dev") -> None:
    services.documents.catalog.add_scope(
        document_id,
        Scope(
            kind="personal",
            ref=user_id,
            added_at=datetime.now(UTC),
            added_by=user_id,
        ),
    )


def _upload(services, filename: str) -> str:
    version = services.documents.upload(
        filename=filename,
        content_type="text/plain",
        content=(filename + " body").encode("utf-8"),
    )
    _link_personal_scope(services, version.document_id)
    return version.document_id


@pytest.fixture
def app_and_services():
    services = build_services()
    app = create_app(services=services)
    return app, services


def test_list_documents_includes_demo_rows_by_default(app_and_services):
    app, services = app_and_services
    _upload(services, _DEMO_NAME)
    _upload(services, "real_report.txt")
    services.documents.catalog.mark_documents_origin(frozenset({_DEMO_NAME}), origin="demo")

    client = TestClient(app)
    response = client.get("/documents")
    assert response.status_code == 200, response.text
    names = {item["original_filename"] for item in response.json()["items"]}
    assert names == {_DEMO_NAME, "real_report.txt"}


def test_list_documents_include_demo_false_hides_demo_rows(app_and_services):
    app, services = app_and_services
    _upload(services, _DEMO_NAME)
    _upload(services, "real_report.txt")
    services.documents.catalog.mark_documents_origin(frozenset({_DEMO_NAME}), origin="demo")

    client = TestClient(app)
    response = client.get("/documents?include_demo=false")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    names = {item["original_filename"] for item in items}
    assert names == {"real_report.txt"}
    assert all(item["origin"] == "operator" for item in items)


def test_document_payload_carries_origin_field(app_and_services):
    app, services = app_and_services
    _upload(services, "real_report.txt")
    client = TestClient(app)
    items = client.get("/documents").json()["items"]
    assert items[0]["origin"] == "operator"


# ─── DemoDatasetService.reset stamping ─────────────────────────────────


def test_reset_stamps_origin_before_archiving(tmp_path):
    """Rows from a crashed mid-flight load (never post-load stamped)
    still leave the catalog tagged origin='demo'."""
    store = InMemoryCatalogStore()
    document = _save(store, document_id="doc-demo", filename=_DEMO_NAME, sha256="a" * 64)
    assert document.origin == "operator"  # simulates the missed stamp

    service = DemoDatasetService(catalog_store=store, data_dir=tmp_path)
    status = service.reset()

    assert store.documents["doc-demo"].origin == "demo"
    assert store.documents["doc-demo"].archived_at is not None
    assert status.demo_doc_count == 0  # archived rows leave the active count


def test_demo_fixture_filenames_match_migration_snapshot():
    """The 0016 backfill list must stay a superset-equal copy of the
    live loader constants — a fixture rename has to touch both."""
    assert frozenset(mig_module._0016_DEMO_FIXTURE_FILENAMES) == DEMO_FIXTURE_FILENAMES
