# Overview

## What KW Pipeline is

A **document-intelligence MVP** that puts auditability and source lineage first. Three slices:

1. **Harvester** — FastAPI backend. Ingests files, hashes them, stores them, parses them, generates schema-validated semantic JSON, renders Markdown. Every claim carries source-line references.
2. **Orbital** — Vite + React + TypeScript reviewer workbench. Lists documents, shows extraction + semantic output side by side, lets a human validate or reject each version.
3. **Knowledge Layer (opt-in)** — Neo4j-backed graph + Anthropic LLM entity extraction. Activates *after* a reviewer validates a version. Every node and edge in the graph has provenance.

## What it isn't (yet)

- Not a chatbot or RAG product (Phase 3, separate ADR first).
- Not a multi-tenant platform (no auth, no workspace scoping yet).
- Not a managed-service offering — local dev + a small Docker compose for the demo path.
- Not LangChain-based — patterns from [`neo4j-labs/llm-graph-builder`](https://github.com/neo4j-labs/llm-graph-builder) are vendored as direct Python (ADR-013).

## Document lifecycle (FSM)

```
UPLOADED → HASHED → STORED → EXTRACTING → EXTRACTED → SEMANTIC_READY
                                                          │
                                          ┌───────────────┼───────────┐
                                          ▼               ▼           ▼
                                  NEEDS_REVIEW       FAILED   DUPLICATE_DETECTED
                                          │
                                ┌─────────┴─────────┐
                                ▼                   ▼
                          VALIDATED            REJECTED
                                │
                                │ (knowledge-layer side-effect, fire-and-log)
                                ▼
                         graph projection + entity extraction
```

The catalog is the source of truth. A failure in projection or entity extraction never rolls back validation.

## Trust rules

- SHA-256 is computed before storage decisions are finalized.
- Duplicate detection uses the hash, never the filename.
- Every semantic asset points back to source references when lineage is available.
- Missing lineage is surfaced as a warning.
- Unsupported semantic claims are marked `needs_review`.
- LLM enrichers plug in via the `SemanticEnricher` Protocol (ADR-009) — `SemanticExtractor` re-validates and forces `review_status="needs_review"` on every output.
- Nothing without provenance reaches the knowledge graph. Phase 2 entity-extraction triples without `source_reference_ids` are dropped to `warnings`.
- Knowledge-layer side-effects of validation never roll back the catalog.

## Tech stack at a glance

| Layer | Tech |
|---|---|
| Backend | FastAPI 0.115, Pydantic 2.10, Pydantic Settings, Python 3.11+ |
| Frontend | Vite 6, React 18, TypeScript 5, Vitest 3, openapi-fetch (typed client) |
| Catalog | SQLite (in-memory or file) |
| Object storage | local filesystem; S3/MinIO abstraction later |
| Parsers | `pdfplumber` (PDF), `python-docx` (DOCX), built-in `PlainTextParser` |
| Validation | Pydantic 2 + `APISchemaModel` base for serialization-strict OpenAPI |
| Markdown | Deterministic Jinja2 template |
| API client | Generated TypeScript types from FastAPI OpenAPI snapshot (ADR-011) |
| Logging | Stdlib JSON formatter; `KW_LOG_FORMAT=json` for production |
| Static analysis | ruff (lint + format), mypy (type-check), pytest with coverage gate |
| Knowledge graph (opt-in) | Neo4j 5.x Community via `neo4j` Python driver, behind a `GraphStore` Protocol |
| LLM (opt-in) | Anthropic Claude via the `anthropic` SDK, behind an `LLMClient` Protocol |
| Graph viz (opt-in) | `@neo4j-nvl/react`, lazy-loaded |

See **[Architecture](Architecture)** for module layout.
