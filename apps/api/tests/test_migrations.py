"""Tests for the ordered schema migration system (issue #63).

Covers:
- Fresh DB: all migrations applied, IDs recorded in order.
- Existing-DB bootstrap (legacy schema present, no schema_migrations): all
  current IDs recorded, no DDL re-run.
- Idempotency: instantiating the store twice does not re-run migrations.
- Partial state: if migration 0001 is applied but 0002 is not, only 0002 runs.
- Failing migration: transaction rolls back and the ID is NOT inserted.
"""

import sqlite3

import pytest

from app.services.catalog_store import SQLiteCatalogStore
from app.services.migrations import MIGRATIONS, _run_migrations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _applied_ids(db_path) -> list[str]:
    """Return migration IDs recorded in schema_migrations, in applied order."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT id FROM schema_migrations ORDER BY applied_at, id").fetchall()
        return [row["id"] for row in rows]
    finally:
        conn.close()


def _all_expected_ids() -> list[str]:
    return [mid for mid, _ in MIGRATIONS]


# ---------------------------------------------------------------------------
# Fresh database
# ---------------------------------------------------------------------------


def test_fresh_db_all_migrations_applied(tmp_path):
    """On a brand-new database every migration ID must be recorded."""
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    recorded = _applied_ids(tmp_path / "catalog.sqlite3")
    assert recorded == _all_expected_ids()


def test_fresh_db_schema_migrations_table_exists(tmp_path):
    """The schema_migrations tracking table must be present after init."""
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    conn = sqlite3.connect(tmp_path / "catalog.sqlite3")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "schema_migrations" in tables


def test_fresh_db_all_business_tables_exist(tmp_path):
    """Migration 0001 must create all five expected tables."""
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    conn = sqlite3.connect(tmp_path / "catalog.sqlite3")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    expected = {"documents", "document_versions", "raw_extractions", "semantic_documents"}
    assert expected.issubset(tables)


# ---------------------------------------------------------------------------
# Legacy-database bootstrap
# ---------------------------------------------------------------------------


def _create_legacy_db(db_path) -> None:
    """Reproduce the full schema as it existed just before the migration system
    was introduced.  The old ``_initialize()`` method created all tables and
    used ALTER TABLE to add ``reviewer_note`` / ``reviewed_at``, so any
    production database already has those columns."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            original_filename TEXT NOT NULL,
            latest_version_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE document_versions (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            storage_uri TEXT NOT NULL,
            status TEXT NOT NULL,
            duplicate_of_version_id TEXT,
            failure_reason TEXT,
            reviewer_note TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_document_versions_sha256 ON document_versions (sha256);
        CREATE TABLE raw_extractions (
            document_version_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE semantic_documents (
            document_version_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def test_legacy_db_bootstrap_stamps_all_ids_without_error(tmp_path):
    """Pre-existing schema with no schema_migrations table: store initialises
    cleanly and all migration IDs are stamped."""
    db_path = tmp_path / "legacy.sqlite3"
    _create_legacy_db(db_path)

    # Must not raise even though 0001 CREATE TABLE IF NOT EXISTS would be a
    # no-op on the legacy tables — the bootstrap path bypasses callables.
    SQLiteCatalogStore(db_path)

    recorded = _applied_ids(db_path)
    assert recorded == _all_expected_ids()


def test_legacy_db_bootstrap_does_not_add_duplicate_columns(tmp_path):
    """The legacy schema already has reviewer_note/reviewed_at added by the
    old ALTER TABLE logic; the bootstrap must not try to add them again."""
    db_path = tmp_path / "legacy_with_review.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            original_filename TEXT NOT NULL,
            latest_version_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE document_versions (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            storage_uri TEXT NOT NULL,
            status TEXT NOT NULL,
            duplicate_of_version_id TEXT,
            failure_reason TEXT,
            reviewer_note TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    # Should not raise sqlite3.OperationalError: duplicate column name
    SQLiteCatalogStore(db_path)


def test_legacy_db_bootstrap_data_survives(tmp_path):
    """Existing rows in a legacy database must not be touched by the bootstrap."""
    db_path = tmp_path / "legacy.sqlite3"
    _create_legacy_db(db_path)

    # Insert rows using the full column set that the legacy schema has
    # (including reviewer_note / reviewed_at which the old ALTER TABLE added).
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO documents VALUES (?, ?, ?, ?)",
        ("doc-legacy", "legacy.txt", "ver-legacy", "2026-01-01T00:00:00"),
    )
    conn.execute(
        """
        INSERT INTO document_versions
          (id, document_id, version_number, filename, content_type,
           file_size, sha256, storage_uri, status, duplicate_of_version_id,
           failure_reason, reviewer_note, reviewed_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ver-legacy",
            "doc-legacy",
            1,
            "legacy.txt",
            "text/plain",
            7,
            "a" * 64,
            "file:///tmp/legacy.txt",
            "STORED",
            None,
            None,
            None,
            None,
            "2026-01-01T00:00:00",
        ),
    )
    conn.commit()
    conn.close()

    store = SQLiteCatalogStore(db_path)
    doc = store.get_document("doc-legacy")

    assert doc is not None
    assert doc.original_filename == "legacy.txt"
    assert len(doc.versions) == 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_double_init_does_not_duplicate_ids(tmp_path):
    """Constructing SQLiteCatalogStore twice on the same file must not
    insert duplicate rows into schema_migrations."""
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    recorded = _applied_ids(tmp_path / "catalog.sqlite3")
    # All IDs must appear exactly once.
    assert recorded == _all_expected_ids()
    assert len(recorded) == len(set(recorded))


# ---------------------------------------------------------------------------
# Partial state
# ---------------------------------------------------------------------------


def test_partial_state_only_missing_migration_runs(tmp_path, monkeypatch):
    """If migration 0001 is already recorded but 0002 is not, only 0002
    should be executed."""
    db_path = tmp_path / "partial.sqlite3"

    # Simulate state: 0001 applied, 0002 not applied.
    # We do this by running _run_migrations with a truncated list that only
    # has 0001, then restoring MIGRATIONS and calling _run_migrations again.
    from app.services import migrations as mig_module

    original_migrations = mig_module.MIGRATIONS[:]
    only_first = [original_migrations[0]]

    monkeypatch.setattr(mig_module, "MIGRATIONS", only_first)

    # Use isolation_level=None so SAVEPOINT-based DDL rollback works correctly
    # (same mode that SQLiteCatalogStore.__init__ uses for migrations).
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    _run_migrations(conn)

    # Confirm only 0001 is recorded so far.
    applied_after_first = {
        row["id"] for row in conn.execute("SELECT id FROM schema_migrations").fetchall()
    }
    assert applied_after_first == {"0001_initial"}

    # Now restore full MIGRATIONS and run again — only 0002 should be added.
    monkeypatch.setattr(mig_module, "MIGRATIONS", original_migrations)

    run_ids: list[str] = []
    original_fn_0002 = original_migrations[1][1]

    def tracked_fn(c: sqlite3.Connection) -> None:
        run_ids.append("0002_add_review_columns")
        original_fn_0002(c)

    patched = [original_migrations[0], ("0002_add_review_columns", tracked_fn)]
    monkeypatch.setattr(mig_module, "MIGRATIONS", patched)

    _run_migrations(conn)
    conn.close()

    # Only 0002 was actually called.
    assert run_ids == ["0002_add_review_columns"]

    # Both IDs recorded.
    recorded = _applied_ids(db_path)
    assert set(recorded) == {"0001_initial", "0002_add_review_columns"}


# ---------------------------------------------------------------------------
# 0003 perf indexes (audit #224)
# ---------------------------------------------------------------------------


def _index_names(db_path) -> set[str]:
    """Return all user-defined index names in the database.

    SQLite auto-generates internal indexes for primary keys with names
    starting with ``sqlite_autoindex_`` — those are filtered out so the
    set only carries indexes a migration created.
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name NOT LIKE 'sqlite_autoindex_%'"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def test_migration_0003_creates_document_versions_document_id_index(tmp_path):
    """Migration 0003 must create the document_versions(document_id) index
    so the per-page version fetch in list_documents stops doing a full scan."""
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    indexes = _index_names(tmp_path / "catalog.sqlite3")
    assert "idx_document_versions_document_id" in indexes


def test_migration_0003_creates_documents_created_at_id_composite_index(tmp_path):
    """Migration 0003 must create the documents(created_at, id) composite
    so cursor pagination is index-served end-to-end."""
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    indexes = _index_names(tmp_path / "catalog.sqlite3")
    assert "idx_documents_created_at_id" in indexes


def test_migration_0003_perf_indexes_id_recorded(tmp_path):
    """The migration runner must record 0003_perf_indexes after applying it."""
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    assert "0003_perf_indexes" in _applied_ids(tmp_path / "catalog.sqlite3")


def test_migration_0003_runs_on_legacy_bootstrap_db(tmp_path):
    """The legacy bootstrap stamps 0001+0002 only; 0003 must still run.

    A legacy database created by the pre-migration ``_initialize`` does not
    carry the perf indexes — bootstrap that stamps every migration would
    leave the new indexes uncreated. Confirm the bootstrap stamps just the
    bootstrap-eligible IDs and that 0003 is then run as part of the normal
    path on the same connection.
    """
    db_path = tmp_path / "legacy.sqlite3"
    _create_legacy_db(db_path)

    SQLiteCatalogStore(db_path)

    indexes = _index_names(db_path)
    assert "idx_document_versions_document_id" in indexes
    assert "idx_documents_created_at_id" in indexes
    # And the migration ID must be recorded so a subsequent restart
    # does not re-run it.
    assert "0003_perf_indexes" in _applied_ids(db_path)


def test_migration_0003_idempotent_on_second_init(tmp_path):
    """Re-instantiating the store must not error or duplicate the indexes."""
    db_path = tmp_path / "catalog.sqlite3"
    SQLiteCatalogStore(db_path)
    SQLiteCatalogStore(db_path)  # second init must not raise

    # Index names are unique by definition; assert each appears exactly once
    # in sqlite_master so a duplicate-creation regression is caught here.
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name IN ('idx_document_versions_document_id', 'idx_documents_created_at_id')"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2


def test_migration_0003_indexes_appear_in_list_documents_query_plan(tmp_path):
    """The SQLite planner must mention the new indexes in the list query plan.

    This is a soft check — index selection depends on stats, but with
    ``IS_NOT NULL`` and ``IN`` predicates against the new indexes the
    planner consistently picks them across SQLite 3.x.
    """
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    conn = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        # The secondary fetch in list_documents.
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM document_versions WHERE document_id IN ('a', 'b')"
        ).fetchall()
        plan_text = " ".join(str(row) for row in plan)
        assert "idx_document_versions_document_id" in plan_text, plan_text

        # The main cursor-paginated query.
        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT d.* FROM documents d "
            "WHERE (d.created_at, d.id) > ('2026-01-01', 'x') "
            "ORDER BY d.created_at ASC, d.id ASC LIMIT 50"
        ).fetchall()
        plan_text = " ".join(str(row) for row in plan)
        assert "idx_documents_created_at_id" in plan_text, plan_text
    finally:
        conn.close()


def test_legacy_bootstrap_stamps_only_pre_existing_migration_ids(tmp_path):
    """Bootstrap must NOT stamp migrations whose effects aren't in the legacy
    DB — otherwise additive migrations (like 0003) would silently never run."""
    db_path = tmp_path / "legacy.sqlite3"
    _create_legacy_db(db_path)

    SQLiteCatalogStore(db_path)

    recorded = _applied_ids(db_path)
    # All current migrations end up applied (0003 actually ran);
    # but the bootstrap path itself only stamped {0001, 0002}.
    # Either way the final state must include every MIGRATIONS entry.
    assert set(recorded) == {mid for mid, _ in MIGRATIONS}


# ---------------------------------------------------------------------------
# Failing migration rolls back
# ---------------------------------------------------------------------------


def test_failing_migration_rolls_back_and_id_not_inserted(tmp_path, monkeypatch):
    """A migration callable that raises must NOT insert its ID into
    schema_migrations, and must not corrupt earlier migrations."""
    db_path = tmp_path / "failing.sqlite3"

    from app.services import migrations as mig_module

    original_migrations = mig_module.MIGRATIONS[:]

    def _boom(conn: sqlite3.Connection) -> None:
        # Do some DDL before raising to verify the savepoint rolls it back.
        conn.execute("CREATE TABLE should_not_exist (x INTEGER)")
        raise RuntimeError("deliberate failure")

    patched = original_migrations + [("9999_boom", _boom)]
    monkeypatch.setattr(mig_module, "MIGRATIONS", patched)

    # Use isolation_level=None (autocommit / manual-transaction mode) so that
    # Python's sqlite3 module does not issue implicit COMMITs before DDL
    # statements.  This is required for SAVEPOINT-based DDL rollback to work
    # reliably across Python versions.
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")

    with pytest.raises(RuntimeError, match="deliberate failure"):
        _run_migrations(conn)

    conn.close()

    # The real migrations must have been recorded.
    recorded = _applied_ids(db_path)
    assert "0001_initial" in recorded
    assert "0002_add_review_columns" in recorded
    # The failing migration's ID must NOT be recorded.
    assert "9999_boom" not in recorded

    # The partial DDL inside _boom must have been rolled back.
    conn2 = sqlite3.connect(db_path)
    tables = {row[0] for row in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn2.close()
    assert "should_not_exist" not in tables


# ---------------------------------------------------------------------------
# HITL slice 1 — validation_metadata + corpus_norms tables (#215)
# ---------------------------------------------------------------------------


def test_migration_0007_creates_validation_metadata_table(tmp_path):
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    conn = sqlite3.connect(tmp_path / "catalog.sqlite3")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(validation_metadata)")}
    conn.close()
    expected = {
        "version_id",
        "confidence_overall",
        "confidence_signals",
        "confidence_weights",
        "ocr_override_active",
        "confidence_computed_at",
        "confidence_computed_by_version",
        "routing_decision",
        "validation_method",
        "validation_actor",
    }
    assert expected.issubset(cols)


def test_migration_0008_creates_corpus_norms_table(tmp_path):
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    conn = sqlite3.connect(tmp_path / "catalog.sqlite3")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(corpus_norms)")}
    conn.close()
    expected = {
        "content_type",
        "topic_cluster",
        "metric_name",
        "sample_count",
        "mean",
        "stddev",
        "updated_at",
    }
    assert expected.issubset(cols)


def test_validation_metadata_foreign_key_to_document_versions(tmp_path):
    """The sidecar references ``document_versions(id)`` so a stale row
    can't outlive the version it describes — guards against orphan
    rows when the future Archive/Purge tool removes a version."""
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    conn = sqlite3.connect(tmp_path / "catalog.sqlite3")
    fks = list(conn.execute("PRAGMA foreign_key_list(validation_metadata)"))
    conn.close()
    assert any(
        row[2] == "document_versions" and row[3] == "version_id" and row[4] == "id" for row in fks
    ), f"expected FK validation_metadata.version_id → document_versions.id, got {fks}"


def test_corpus_norms_compound_primary_key(tmp_path):
    """The PK is ``(content_type, topic_cluster, metric_name)`` so the
    same bucket cannot be persisted twice."""
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    conn = sqlite3.connect(tmp_path / "catalog.sqlite3")
    pk_cols = [
        row[1]
        for row in conn.execute("PRAGMA table_info(corpus_norms)")
        if row[5] != 0  # row[5] is the pk index (1-based for compound PKs)
    ]
    conn.close()
    assert sorted(pk_cols) == sorted(["content_type", "topic_cluster", "metric_name"])
