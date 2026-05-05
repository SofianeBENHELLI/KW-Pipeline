"""Ordered schema migrations for the SQLite catalog database.

Contract
--------
Each entry in ``MIGRATIONS`` is a ``(migration_id, callable)`` pair where
``migration_id`` is a unique, lexicographically-ordered string (e.g.
``"0001_initial"``) and the callable accepts a single :class:`sqlite3.Connection`
and performs the DDL for that migration step.

``_run_migrations(conn)`` is the public entry point:

1. Ensures the ``schema_migrations`` tracking table exists.
2. Reads the set of already-applied IDs.
3. For each unapplied migration, runs the callable inside its own
   ``SAVEPOINT``/``RELEASE``/``ROLLBACK TO SAVEPOINT`` transaction so a
   failure rolls back only that migration and does not corrupt work done by
   earlier steps.
4. Inserts the migration ID into ``schema_migrations`` on success.

Backwards-compatibility bootstrap
----------------------------------
If ``schema_migrations`` is empty **and** any of the legacy tables already
exist (created by the old ``_initialize`` ad-hoc approach), all current
migration IDs are recorded as applied without re-running their callables.
This lets existing on-disk databases be adopted cleanly.

Adding a new migration
----------------------
1. Define a function ``def _migrate_NNNN_name(conn: sqlite3.Connection) -> None``.
2. Append ``("NNNN_name", _migrate_NNNN_name)`` to ``MIGRATIONS``.
   The list is the authoritative order — do not renumber existing entries.
"""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Individual migration steps
# ---------------------------------------------------------------------------


def _migrate_0001_initial(conn: sqlite3.Connection) -> None:
    """Baseline schema: documents, document_versions (with review columns),
    sha256 index, raw_extractions, and semantic_documents tables."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            original_filename TEXT NOT NULL,
            latest_version_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_versions (
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
            created_at TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_versions_sha256
        ON document_versions (sha256)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_extractions (
            document_version_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (document_version_id) REFERENCES document_versions(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_documents (
            document_version_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (document_version_id) REFERENCES document_versions(id)
        )
        """
    )


def _migrate_0002_add_review_columns(conn: sqlite3.Connection) -> None:
    """Add ``reviewer_note`` and ``reviewed_at`` to pre-existing databases
    that were created before those columns were introduced.

    ``CREATE TABLE IF NOT EXISTS`` in migration 0001 already includes these
    columns for brand-new databases, so this migration is a no-op for fresh
    installs. For legacy databases that skipped 0001 via the bootstrap path,
    it adds the columns if they are absent (SQLite does not support
    ``ADD COLUMN IF NOT EXISTS``, so we inspect ``PRAGMA table_info`` first).

    Note: ``PRAGMA table_info`` returns rows as ``(cid, name, type,
    notnull, dflt_value, pk)`` — we use positional index 1 so this
    function does not depend on ``row_factory`` being set on the caller's
    connection.
    """
    # Index 1 is the column name in PRAGMA table_info output.
    existing = {row[1] for row in conn.execute("PRAGMA table_info(document_versions)").fetchall()}
    if "reviewer_note" not in existing:
        conn.execute("ALTER TABLE document_versions ADD COLUMN reviewer_note TEXT")
    if "reviewed_at" not in existing:
        conn.execute("ALTER TABLE document_versions ADD COLUMN reviewed_at TEXT")


def _migrate_0003_perf_indexes(conn: sqlite3.Connection) -> None:
    """Add indexes on hot read paths uncovered by the 2026-05-04 audit (#224).

    1. ``idx_document_versions_document_id`` — the ``list_documents`` page
       fetches versions for the returned slice via
       ``WHERE document_id IN (...)``. Without this index that secondary
       query scans the full ``document_versions`` table on every page.
    2. ``idx_documents_created_at_id`` — cursor pagination orders by
       ``(d.created_at, d.id)`` and predicates the cursor as
       ``(d.created_at, d.id) > (?, ?)``. The composite covers both the
       sort and the cursor predicate so the planner can serve pages
       directly from the index.

    Both indexes use ``IF NOT EXISTS`` so re-running this migration on a
    database where the indexes were created out-of-band is safe.
    """
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_versions_document_id "
        "ON document_versions (document_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_created_at_id ON documents (created_at, id)"
    )


def _migrate_0005_document_scopes_removed_at(conn: sqlite3.Connection) -> None:
    """Add ``removed_at`` to ``document_scopes`` for soft-remove semantics.

    Per the no-delete policy (no real deletion of document source data —
    flag-only, real purge handled by a future Archive/Purge Admin tool),
    ``CatalogStore.remove_scope`` no longer deletes the row but flags
    it with a ``removed_at`` timestamp. ``add_scope`` reactivates a
    flagged row instead of failing the PK constraint.

    SQLite does not support ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``,
    so we inspect ``PRAGMA table_info`` first.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(document_scopes)").fetchall()}
    if "removed_at" not in existing:
        conn.execute("ALTER TABLE document_scopes ADD COLUMN removed_at TEXT")


def _migrate_0004_document_scopes(conn: sqlite3.Connection) -> None:
    """Workspace scoping (ADR-020 §1, EPIC-D D.1, #218).

    Creates the ``document_scopes`` join table that links a document
    family to one or more scopes (``personal`` / ``swym_community`` /
    ``project``). A document can live in N scopes simultaneously; the
    primary key ``(document_id, scope_kind, scope_ref)`` keeps the
    same scope from being recorded twice for the same document.

    The single supporting index covers the read pattern "list documents
    in scope X" used by the future EPIC-D D.5 filter on every list /
    search / graph endpoint. The reverse pattern ("list scopes for
    document Y") is already covered by the primary key prefix.

    Both DDL statements use ``IF NOT EXISTS`` so the migration is
    idempotent — re-running on a database where the structures were
    created out-of-band is safe.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_scopes (
            document_id TEXT NOT NULL,
            scope_kind  TEXT NOT NULL,
            scope_ref   TEXT NOT NULL,
            added_at    TEXT NOT NULL,
            added_by    TEXT NOT NULL,
            PRIMARY KEY (document_id, scope_kind, scope_ref),
            FOREIGN KEY (document_id) REFERENCES documents(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_scopes_lookup "
        "ON document_scopes (scope_kind, scope_ref)"
    )


# ---------------------------------------------------------------------------
# Ordered registry — append only, never renumber
# ---------------------------------------------------------------------------

MIGRATIONS: list[tuple[str, Callable[[sqlite3.Connection], None]]] = [
    ("0001_initial", _migrate_0001_initial),
    ("0002_add_review_columns", _migrate_0002_add_review_columns),
    ("0003_perf_indexes", _migrate_0003_perf_indexes),
    ("0004_document_scopes", _migrate_0004_document_scopes),
    ("0005_document_scopes_removed_at", _migrate_0005_document_scopes_removed_at),
]

# The set of table names that the legacy ``_initialize`` approach created.
# Used by the bootstrap detection to decide whether to stamp the
# bootstrap-eligible migrations without running them.
_LEGACY_TABLES = frozenset({"documents", "document_versions"})

# Migrations whose effects are guaranteed to already be present in any DB
# that triggers the legacy bootstrap path (i.e. the original ``_initialize``
# created these structures). New migrations added after the bootstrap rule
# was written must NOT be added here — they need to actually run on legacy
# databases too. Per audit #224, the perf indexes (0003) are intentionally
# not bootstrap-eligible because they did not exist before this commit.
_BOOTSTRAP_STAMPED_MIGRATION_IDS = frozenset(
    {
        "0001_initial",
        "0002_add_review_columns",
    }
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply every unapplied migration in order.

    This function is **not** wrapped in a transaction itself — each migration
    runs in its own savepoint so a failure in migration N does not roll back
    migrations 1..N-1 that already succeeded.

    Parameters
    ----------
    conn:
        An open :class:`sqlite3.Connection`.  The caller is responsible for
        the outer ``commit`` / ``rollback`` life-cycle (the
        :meth:`SQLiteCatalogStore._connect` context manager handles this).
    """
    # Ensure the tracking table exists.  This is idempotent and safe to run
    # on every startup.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )

    # Use positional index 0 (the id column) so this works regardless of
    # whether row_factory is set on the connection.
    applied: set[str] = {
        row[0] for row in conn.execute("SELECT id FROM schema_migrations").fetchall()
    }

    # --- Backwards-compatibility bootstrap --------------------------------
    # If schema_migrations is empty but the legacy tables already exist,
    # stamp the migrations whose effects are known to already be present
    # (``_BOOTSTRAP_STAMPED_MIGRATION_IDS``) without running their callables.
    # Any later migration drops through to the normal path so it actually
    # runs on the legacy database — required for additive migrations like
    # the perf indexes added by 0003 (audit #224).
    if not applied:
        existing_tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if _LEGACY_TABLES.issubset(existing_tables):
            now = datetime.now(UTC).isoformat()
            conn.executemany(
                "INSERT OR IGNORE INTO schema_migrations (id, applied_at) VALUES (?, ?)",
                [
                    (migration_id, now)
                    for migration_id, _ in MIGRATIONS
                    if migration_id in _BOOTSTRAP_STAMPED_MIGRATION_IDS
                ],
            )
            applied = set(_BOOTSTRAP_STAMPED_MIGRATION_IDS)

    # --- Normal path: run unapplied migrations in order -------------------
    for migration_id, migrate_fn in MIGRATIONS:
        if migration_id in applied:
            continue
        savepoint = f"sp_{migration_id}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            migrate_fn(conn)
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO schema_migrations (id, applied_at) VALUES (?, ?)",
                (migration_id, now),
            )
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
