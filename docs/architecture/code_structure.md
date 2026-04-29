# Backend Code Structure

The Harvester API is split into small modules so the MVP can move from
in-memory behavior to persistent infrastructure without changing route
contracts.

## Application Wiring

- `app/main.py` creates the FastAPI app.
- `app/dependencies.py` builds isolated `PipelineServices` containers for
  in-memory tests or local persistent execution.
- `app/routes.py` registers HTTP routes against a concrete service container.

Tests use `create_app()` to get a fresh in-memory catalog and extraction store
for each integration scenario. Local dev can use `create_app(persistent=True)`
to store catalog metadata in SQLite and raw uploads on disk.

## Services

- `DocumentService` owns catalog behavior, SHA-256 duplicate detection, and
  lifecycle status updates through a catalog-store boundary.
- `InMemoryCatalogStore` is used for fast tests and demos.
- `SQLiteCatalogStore` persists document and version metadata for the local MVP.
- `InMemoryStorageService` stores raw bytes in memory for tests.
- `FileSystemStorageService` stores raw bytes on disk and returns `file://`
  handles.
- `PlainTextParser` is a deterministic parser used until Docling is integrated.
  It preserves original line numbers for source lineage.
- `ExtractionJobService` coordinates parser execution and status transitions.
- `SemanticExtractor` converts raw extraction output into schema-validated
  semantic JSON that remains `needs_review`.
- `MarkdownGenerator` renders deterministic Markdown with required frontmatter
  and source lineage.
- `SemanticOutputService` caches generated semantic JSON and Markdown so API
  retrieval endpoints can serve review output without re-running generation.

## Schemas

Pydantic schemas define the current API and storage contracts:

- `Document` and `DocumentVersion`
- `RawExtraction` and `SourceReference`
- `SemanticDocument`, `SemanticSection`, and `SemanticAsset`

The schema layer enforces important quality rules, including that
`source_backed` semantic assets must include source references.

## Local Persistence

Persistent mode stores files under `.kw-pipeline/` by default:

- `.kw-pipeline/catalog.sqlite3`
- `.kw-pipeline/raw/`

The directory is ignored by Git and can be deleted to reset local state.

See `docs/architecture/persistence.md` for the persistence boundary and adapter
details.
