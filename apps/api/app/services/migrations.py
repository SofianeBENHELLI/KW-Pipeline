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


# ---------------------------------------------------------------------------
# Ordered registry — append only, never renumber
# ---------------------------------------------------------------------------

MIGRATIONS: list[tuple[str, Callable[[sqlite3.Connection], None]]] = [
    ("0001_initial", _migrate_0001_initial),
    ("0002_add_review_columns", _migrate_0002_add_review_columns),
]

# The set of table names that the legacy ``_initialize`` approach created.
# Used by the bootstrap detection to decide whether to stamp all migrations
# without running them.
_LEGACY_TABLES = frozenset({"documents", "document_versions"})


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
    # stamp all current migrations as applied without running their callables.
    # This adopts existing on-disk databases without touching their data.
    if not applied:
        existing_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if _LEGACY_TABLES.issubset(existing_tables):
            now = datetime.now(UTC).isoformat()
            conn.executemany(
                "INSERT OR IGNORE INTO schema_migrations (id, applied_at) VALUES (?, ?)",
                [(migration_id, now) for migration_id, _ in MIGRATIONS],
            )
            return

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
