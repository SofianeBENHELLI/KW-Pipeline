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

## Current Limits

- Raw extraction output is still held in memory.
- Semantic output is still returned directly and not persisted.
- There is no migration system yet.
- SQLite is for local MVP usage, not the final production database.

The next persistence slice should store extraction runs, raw extraction JSON,
semantic JSON, and generated Markdown artifacts.
