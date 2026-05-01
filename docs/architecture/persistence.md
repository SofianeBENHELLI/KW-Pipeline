# Persistence Architecture

The first persistent backend slice keeps the API contract stable while moving
catalog metadata and raw file bytes out of process memory.

## Goals

- Preserve the existing upload, catalog, detail, extraction, and semantic API
  behavior.
- Keep tests fast with in-memory adapters.
- Allow local MVP demos to survive app restarts.
- Keep the storage boundaries compatible with PostgreSQL and S3/MinIO later.

## Catalog Storage

Catalog persistence is accessed through `CatalogStore`.

Implementations:

- `InMemoryCatalogStore`: fast tests and ephemeral local demos.
- `SQLiteCatalogStore`: local persistent MVP catalog.

SQLite stores:

- `documents`
- `document_versions`

The `document_versions.sha256` index supports duplicate detection by immutable
file bytes. Duplicate detection does not use filenames.

## Raw File Storage

Raw file bytes are accessed through `StorageService`.

Implementations:

- `InMemoryStorageService`: fast tests and ephemeral local demos.
- `FileSystemStorageService`: local persistent MVP file storage.

Filesystem storage returns `file://` URIs and rejects:

- absolute storage keys;
- parent traversal in storage keys;
- `file://` reads outside the configured storage root.

## Runtime Modes

In-memory mode:

```python
from app.main import create_app

app = create_app()
```

Persistent mode:

```python
from app.main import create_app

app = create_app(persistent=True, data_dir=".kw-pipeline")
```

Default persistent layout:

```text
.kw-pipeline/
  catalog.sqlite3
  raw/
```

## Schema Migrations

The SQLite catalog uses an ordered, code-driven migration system rather than
ad-hoc `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE` patches.

### Tracking table

```sql
CREATE TABLE schema_migrations (
    id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
```

Every applied migration is recorded here. The presence of this row is what
prevents a migration from being re-run.

### Registry

All migrations live in `apps/api/app/services/migrations.py` as an ordered
list:

```python
MIGRATIONS: list[tuple[str, Callable[[sqlite3.Connection], None]]] = [
    ("0001_initial", _migrate_0001_initial),
    ("0002_add_review_columns", _migrate_0002_add_review_columns),
    # append new entries here — never renumber existing ones
]
```

Each entry is `(migration_id, callable)`. IDs are lexicographically ordered
strings (`"NNNN_description"`). The callable receives an open
`sqlite3.Connection` and performs all necessary DDL.

### How migrations run

`_run_migrations(conn)` is called once per `SQLiteCatalogStore.__init__`:

1. Creates `schema_migrations` if it does not exist.
2. Reads the set of already-applied IDs.
3. For each migration whose ID is not yet recorded, runs the callable inside
   its own `SAVEPOINT` so a failure rolls back only that step.
4. Inserts the migration ID on success.

### Backwards-compatibility bootstrap

Existing on-disk databases that were created before the migration system was
introduced do not have a `schema_migrations` table. On first open, if the
table is empty **and** the legacy `documents` / `document_versions` tables
already exist, all current migration IDs are stamped as applied without
executing their callables. This bootstraps existing demos cleanly without
re-running DDL against a schema that is already in the target state.

### Adding a new migration

1. Define a function:

   ```python
   def _migrate_NNNN_name(conn: sqlite3.Connection) -> None:
       conn.execute("ALTER TABLE ...")
   ```

2. Append it to `MIGRATIONS`:

   ```python
   ("NNNN_name", _migrate_NNNN_name),
   ```

Never renumber or remove existing entries — doing so would break databases
that have already recorded those IDs.

## Current Limits

- SQLite is for local MVP usage, not the final production database.
- Migration callables are plain Python functions; SQL-file based migrations
  are not supported yet.
