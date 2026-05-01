# KW Pipeline Agent Instructions

These instructions guide AI coding agents working on this repository.

## Product Boundary

This repository starts with the **Document Intelligence MVP** plus an
**opt-in Knowledge Layer** that sits behind the human review gate.
The MVP turns documents into governed semantic Markdown assets; the
knowledge layer projects validated documents into a graph and (with an
LLM key configured) extracts typed entities with section-level citations.

### In Scope

- Document upload or source registration.
- SHA-256 hash computation.
- Catalog storage.
- Duplicate and version detection.
- Raw file storage.
- Document parsing.
- Semantic JSON extraction.
- One Markdown file per document version.
- Extraction status API.
- UI for upload, catalog, status, and Markdown review.
- **Knowledge graph projection** of `VALIDATED` documents into Neo4j
  (Document/Version/Section nodes + `PART_OF` edges). Behind a
  `GraphStore` Protocol, in-memory by default, opt-in via
  `KW_KNOWLEDGE_LAYER_ENABLED` (ADR-012).
- **LLM-driven entity extraction** as a fire-and-log side-effect of
  validation, behind `ANTHROPIC_API_KEY`. Triples without
  `source_reference_ids` are dropped (ADR-013).
- **Frontend graph view** (`@neo4j-nvl/react`) embedded in the review
  workspace.

### Out of Scope (today)

- Chatbot / RAG / GraphRAG surfaces (Phase 3 — separate ADRs first).
- LangChain or `langchain-experimental` dependencies — explicitly
  rejected by ADR-013; reimplement the patterns we want directly.
- Multi-LLM-provider abstractions in v1 — Anthropic only until a
  second provider is justified.
- AURA / MCP integrations.
- Vector search / embeddings (deferred to Phase 3 ADR).
- Advanced multi-tenant administration / workspace isolation
  (tracked under #91).

## Agent Roles

### Blueprint — Architect and Challenger

Blueprint owns architecture, contracts, acceptance criteria, and review quality.

Blueprint must challenge:

- missing hash/version logic;
- missing source lineage;
- vague AI extraction;
- untested ingestion paths;
- tools with unclear commercial licensing;
- unnecessary scope expansion.

### Harvester — Ingestion and Semantic Extraction

Harvester owns the backend ingestion pipeline.

Harvester must implement:

- file upload;
- SHA-256 hashing;
- catalog persistence;
- duplicate detection;
- document versioning;
- raw file storage;
- document parsing;
- semantic JSON generation;
- Markdown generation;
- extraction status tracking.

### Orbital — Frontend and UX

Orbital owns the UI.

Orbital must implement:

- upload page;
- catalog page;
- document details page;
- extraction status page;
- Markdown preview;
- semantic review UX.

## Engineering Rules

1. Keep PRs small and reviewable.
2. Do not implement chatbot / RAG features yet (Phase 3, separate ADR).
3. Every document must have a SHA-256 hash.
4. Every generated Markdown file must include YAML frontmatter.
5. Every semantic claim should include lineage when available.
6. Failed jobs must be visible and persisted.
7. Prefer deterministic extraction before LLM extraction.
8. LLM-generated outputs must be schema-validated before storage.
9. **No edge in the knowledge graph without a `source_reference_id`.**
   The Phase 2 entity extractor drops triples without provenance into
   `warnings`; nothing without a citation reaches the graph (ADR-012 §4).
10. **Knowledge-layer side-effects must never roll back validation.**
    Graph projection and entity extraction fire after `mark_validated`
    and log on failure; the SQLite catalog stays the source of truth.
11. **No LangChain.** `langchain`, `langchain-experimental`,
    `langchain-anthropic`, etc. are forbidden in `pyproject.toml`. We
    vendor patterns from `neo4j-labs/llm-graph-builder` (Apache-2.0)
    as auditable Python directly (ADR-013).
12. Avoid AGPL/GPL/server-side reciprocal dependencies unless
    explicitly approved.
13. Add tests for core catalog and hashing behavior. Knowledge-layer
    code uses `InMemoryGraphStore` + `FakeLLMClient` so default
    `pytest` runs without Docker or a network LLM call. Real
    Neo4j / Anthropic exercises live behind `pytest -m integration`
    and `pytest -m llm_integration`, both opt-in.

## Recommended Stack

- Backend: FastAPI + Python (3.11+).
- Frontend: Vite + React 18 + TypeScript (see ADR-004 — the original
  Next.js suggestion was reconsidered).
- Catalog database: SQLite for the MVP; a `CatalogStore` Protocol
  keeps the Postgres path open. Persistent mode lives under
  `.kw-pipeline/`.
- Knowledge graph (optional): Neo4j 5.x Community via Docker compose,
  behind a `GraphStore` Protocol. In-memory fake by default.
- LLM (optional): Anthropic Claude via the `anthropic` SDK, behind an
  `LLMClient` Protocol. `FakeLLMClient` for tests.
- Object storage: local filesystem for MVP; S3/MinIO abstraction later.
- Queue: deferred (#40 — async parser queue + background jobs).
- Parsing: pdfplumber for PDFs (ADR-010), python-docx for DOCX,
  built-in PlainTextParser. Docling was evaluated and rejected for
  the MVP (ADR-010).
- Validation: Pydantic 2 (`APISchemaModel` base flips
  `json_schema_serialization_defaults_required=True` so list defaults
  surface as required in OpenAPI).
- Markdown generation: deterministic Jinja2 template.
- API client: typed `openapi-fetch` generated from the FastAPI
  OpenAPI snapshot (ADR-011).

## Minimal Lifecycle States

- UPLOADED
- HASHED
- DUPLICATE_DETECTED
- STORED
- QUEUED_FOR_EXTRACTION
- EXTRACTING
- EXTRACTED
- NEEDS_REVIEW
- VALIDATED
- FAILED

## First MVP Definition of Done

A user can upload a document, see it in the catalog, see its SHA-256 hash, trigger extraction, preview generated semantic Markdown, and validate or reject the extraction.