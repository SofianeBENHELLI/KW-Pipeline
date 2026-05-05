<!-- $PublishToSwym{ "parent": "./Home.md" }$ -->

# Architecture

The repo is a monorepo of two apps and a shared knowledge layer that activates on demand.

```
.
├── apps/
│   ├── api/                  Harvester — FastAPI backend (Python 3.11+)
│   │   └── app/
│   │       ├── main.py             create_app() entry
│   │       ├── settings.py         Pydantic-Settings model (ADR-011 / #43)
│   │       ├── logging_config.py   text/json formatter (ADR-014's sibling, #42)
│   │       ├── routes.py           HTTP surface (operation_ids drive ADR-011 codegen)
│   │       ├── dependencies.py     PipelineServices DI container
│   │       ├── errors.py           ApiError envelope (registration: #120)
│   │       ├── models/document.py  lifecycle FSM
│   │       ├── schemas/            Pydantic API + storage models
│   │       │   ├── document.py
│   │       │   ├── extraction.py
│   │       │   ├── semantic_document.py
│   │       │   └── knowledge.py    GraphNode, GraphEdge, EntityTriple, ...
│   │       └── services/
│   │           ├── catalog_store.py        InMemoryCatalogStore + SQLiteCatalogStore
│   │           ├── document_service.py     upload, dedup, FSM transitions, audit log
│   │           ├── document_parser.py      Parser Protocol + ParserRegistry
│   │           ├── parsers/                PlainTextParser, DocxParser, PdfParser
│   │           ├── extraction_job_service.py   raw extraction orchestration
│   │           ├── semantic_extractor.py   raw → semantic JSON (ADR-009 boundary)
│   │           ├── enrichers/              SemanticEnricher Protocol
│   │           ├── markdown_generator.py   Jinja2 template
│   │           ├── semantic_output_service.py   persist semantic + markdown
│   │           ├── idempotency_store.py    in-memory + SQLite
│   │           ├── hash_service.py         SHA-256 streaming
│   │           ├── storage_service.py      InMemory + FileSystem
│   │           ├── migrations.py           SQLite catalog migrations
│   │           └── knowledge/              ←— opt-in (ADR-012, ADR-013)
│   │               ├── graph_store.py      GraphStore Protocol + InMemory + Neo4j
│   │               ├── projector.py        VALIDATED → graph nodes + edges
│   │               ├── llm_client.py       LLMClient Protocol + Anthropic + Fake
│   │               └── entity_extractor.py LLM tool-use w/ citation enforcement
│   └── web/                  Orbital — Vite + React + TypeScript
│       └── src/
│           ├── App.tsx
│           ├── main.tsx
│           ├── api/
│           │   ├── client.ts                    typed openapi-fetch wrapper
│           │   ├── types.ts                     re-exports of generated types
│           │   └── generated/schema.ts          generated; do not edit
│           ├── domain/document.ts
│           ├── features/
│           │   ├── pipeline/PipelineWidget.tsx  compact dashboard widget
│           │   ├── review/ReviewWorkspace.tsx   audit surface
│           │   └── graph/                       lazy-loaded knowledge-graph view
│           ├── ui/
│           ├── fixtures/
│           └── styles.css
├── docker/
│   └── docker-compose.yml    Neo4j 5.23 Community + the API for the demo path
├── docs/
│   ├── architecture/
│   ├── adr/                  ADR-001 .. ADR-014
│   └── roadmap/
└── .github/workflows/ci.yml  workflow-lint, ruff, mypy, pytest, openapi-contract,
                              integration (Neo4j service), frontend
```

## Boundary protocols

The system uses a small number of Python `Protocol`s as integration seams. Tests use in-memory fakes; production deploys swap in the real backend.

| Protocol | In-memory fake | Production impl |
|---|---|---|
| `CatalogStore` | `InMemoryCatalogStore` | `SQLiteCatalogStore` |
| `StorageService` | `InMemoryStorageService` | `FileSystemStorageService` |
| `Parser` | `PlainTextParser`, `DocxParser`, `PdfParser` (all real, all deterministic) | same — no LLM in the parser path |
| `IdempotencyStore` | `InMemoryIdempotencyStore` | `SQLiteIdempotencyStore` |
| `SemanticEnricher` (ADR-009) | n/a (default `[]`) | future LLM-backed enricher (the entity extractor lives one layer up, not as an enricher) |
| `GraphStore` (ADR-012) | `InMemoryGraphStore` | `Neo4jGraphStore` (lazy-imports the `neo4j` driver) |
| `LLMClient` (ADR-013) | `FakeLLMClient` (queue of recorded responses) | `AnthropicLLMClient` |

## OpenAPI codegen pipeline (ADR-011)

The frontend's typed client is generated from the FastAPI app's `app.openapi()` snapshot via `openapi-typescript`. CI fails if the snapshot or the generated TypeScript drift from `main`.

```
backend route changes
        │
        ▼
python scripts/export_openapi.py     →  apps/api/openapi.json (committed)
        │
        ▼
npm run openapi:generate             →  apps/web/src/api/generated/schema.ts (committed)
        │
        ▼
client.ts uses openapi-fetch          →  compile-time path/method/param checks
```

`apps/web/src/api/types.ts` is a thin alias re-export so feature code keeps importing stable names.

## Audit guarantees that flow through the system

1. **Hash before status**: SHA-256 is computed during streaming upload before any FSM decision is made.
2. **No filename-based dedup**: only the hash matters. Two uploads with different filenames but identical bytes produce a `DUPLICATE_DETECTED` version that points at the original.
3. **Schema validation everywhere**: every `SemanticAsset` is re-validated; every `EntityTriple` is re-validated; failures are dropped to `warnings`, never silently swallowed.
4. **Source-line lineage on every asset**: `SourceReference(document_version_id, section_id, page_number, line_start, line_end, snippet)` follows each claim from the parser through to the graph.
5. **Review gate enforced once at the FSM, propagated everywhere**: the knowledge layer fires only after `mark_validated`. There is no path that lands LLM-claimed entities in the graph without a human signature.
6. **Idempotency on writes**: `Idempotency-Key` header dedups POST replays to upload, extract, and semantic-generate routes.
7. **Structured audit log**: a documented event catalogue (`docs/architecture/logging.md`) captures every status change, every review action, every knowledge-layer side-effect.
8. **No edge in the graph without `source_reference_id`**: Phase 2 enforces this at the boundary.
9. **Catalog integrity over graph completeness**: a graph or LLM outage logs and is retried later. The catalog stays correct.

See [Knowledge Layer](Knowledge-Layer) for how the audit guarantees extend into the graph.
