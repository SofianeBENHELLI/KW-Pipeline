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

### Core ingestion + review (always on)

- `DocumentService` owns catalog behavior, SHA-256 duplicate detection, and
  lifecycle status updates through a catalog-store boundary.
- `InMemoryCatalogStore` is used for fast tests and demos.
- `SQLiteCatalogStore` persists document and version metadata for the local MVP.
- `InMemoryStorageService` stores raw bytes in memory for tests.
- `FileSystemStorageService` stores raw bytes on disk and returns `file://`
  handles.
- `PlainTextParser`, `DocxParser` (python-docx), `PdfParser` (pdfplumber, see
  ADR-010) — deterministic parsers registered by content type in
  `ParserRegistry`. Each preserves source lineage.
- `ExtractionJobService` coordinates parser execution and status transitions.
- `SemanticExtractor` converts raw extraction output into schema-validated
  semantic JSON that remains `needs_review`.
- `SemanticEnricher` Protocol (ADR-009): an LLM enricher can be plugged in
  without bypassing the review gate. The `SemanticExtractor` re-validates and
  forces `review_status="needs_review"` on all enricher output.
- `MarkdownGenerator` renders deterministic Markdown with required frontmatter
  and source lineage.
- `SemanticOutputService` generates and persists semantic JSON and Markdown so
  API retrieval endpoints can serve review output without re-running generation.
- `IdempotencyStore` Protocol with `InMemoryIdempotencyStore` and
  `SQLiteIdempotencyStore` impls — protects POST routes against client
  retries (`Idempotency-Key` header).

### Knowledge layer (opt-in, see ADR-012 / ADR-013)

Located in `app/services/knowledge/`. Inactive unless
`KW_KNOWLEDGE_LAYER_ENABLED=true`; with no env vars set, the in-memory
graph store is empty and the projector is `None`.

- `GraphStore` Protocol — the only seam between the rest of the codebase and
  a concrete graph backend. Two impls: `InMemoryGraphStore` (used by all
  default unit tests) and `Neo4jGraphStore` (lazy-imports the `neo4j`
  driver; exercised behind `pytest -m integration`). Cypher MERGE patterns
  + deadlock retry are adapted from
  `neo4j-labs/llm-graph-builder` (Apache-2.0).
- `KnowledgeProjector` — turns a `VALIDATED` `SemanticDocument` into
  `Document → Version → Section` nodes joined by `PART_OF` edges. Fires as
  a fire-and-log side-effect of the validate route; never rolls back
  validation. Re-projecting is safe (the version's prior subgraph is
  deleted first, so renamed/dropped sections don't leave orphans).
- `LLMClient` Protocol — single method `complete_with_tool` that takes a
  Pydantic-derived JSON schema and returns parsed structured output plus
  token usage. `AnthropicLLMClient` (real, opt-in) and `FakeLLMClient`
  (test fake with a recorded response queue) are the two impls.
- `EntityExtractor` — wraps `LLMClient` with an Anthropic tool-use prompt,
  sanitizes prompt-injection prefixes, and rejects triples whose
  `source_reference_ids` aren't a subset of the parent section's set.
  Adds extracted `(:Entity)` nodes (id = stable hash of
  `(subject, subject_type)` so cross-document references converge) and
  `HAS_ENTITY` edges that carry the `source_reference_id`.

The knowledge-layer routes are `GET /documents/{id}/graph` and
`GET /knowledge/graph` (cursor paginated). They return empty payloads
when the layer is disabled.

## Schemas

Pydantic schemas define the current API and storage contracts. All API-bound
schemas inherit from `app.schemas.APISchemaModel` (see ADR-011 / PR #107),
whose `json_schema_serialization_defaults_required=True` makes Pydantic list
defaults appear as required fields in OpenAPI — so generated TypeScript
clients see `T[]` instead of `T[] | undefined`.

Core:

- `Document` and `DocumentVersion`
- `RawExtraction` and `SourceReference`
- `SemanticDocument`, `SemanticSection`, and `SemanticAsset`

Knowledge layer:

- `GraphNode`, `GraphEdge` — the wire shape for the graph projection.
- `KnowledgeGraphProjection` — per-document subgraph (used by
  `GET /documents/{id}/graph`).
- `KnowledgeGraphPage` — cursor-paginated catalog walk.
- `EntityTriple`, `EntityExtractionResult` — internal Phase 2 shapes
  (not bound to any route's `response_model`).

The schema layer enforces important quality rules: `source_backed`
semantic assets must include source references, and Phase 2
`EntityTriple`s require `source_reference_ids` (`Field(min_length=1)`)
so the audit gate carries into the graph.

## Local Persistence

Persistent mode stores files under `.kw-pipeline/` by default:

- `.kw-pipeline/catalog.sqlite3`
- `.kw-pipeline/raw/`

The directory is ignored by Git and can be deleted to reset local state.

See `docs/architecture/persistence.md` for the persistence boundary and adapter
details.

## Frontend

`apps/web/` contains the Orbital reviewer UI. It uses Vite, React 18,
TypeScript, and Vitest. The frontend should be developed as an operational
workbench that can later be embedded as a compact 3DEXPERIENCE-compatible
widget. See `docs/architecture/orbital_widget_ux.md` for the frontend UX
direction and widget constraints.

The API client (`apps/web/src/api/client.ts`) is built on
[`openapi-fetch`](https://openapi-ts.dev/openapi-fetch); paths, methods,
path parameters, and response shapes are checked at compile time against
`apps/web/src/api/generated/schema.ts`, regenerated from
`apps/api/openapi.json` (see ADR-011 and
`docs/workflows/openapi_codegen.md`).

Feature slices:

- `features/pipeline/` — compact dashboard widget.
- `features/review/` — expanded review workspace; the audit surface.
- `features/graph/` — `<KnowledgeGraphView />` wrapping
  `@neo4j-nvl/react`. Reads the projection via
  `GET /documents/{id}/graph`; renders an empty-state when the
  knowledge layer is disabled or the version isn't validated yet.

`@neo4j-nvl/base` is heavyweight (canvas-based graph layout); a
follow-up PR will lazy-load the graph slice via `React.lazy` to keep
the initial bundle small for reviewers who don't open the graph tab.
