# KW Pipeline Agent Instructions

These instructions guide AI coding agents working on this repository.

## Product Boundary

This repository starts with the **Document Intelligence MVP** only.

The MVP transforms documents into governed semantic Markdown assets.

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

### Out of Scope

- Chatbot experience.
- AURA integration.
- MCP integration.
- Knowledge graph.
- Vector search.
- Advanced multi-tenant administration.

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
2. Do not implement chatbot features in the first MVP.
3. Every document must have a SHA-256 hash.
4. Every generated Markdown file must include YAML frontmatter.
5. Every semantic claim should include lineage when available.
6. Failed jobs must be visible and persisted.
7. Prefer deterministic extraction before LLM extraction.
8. LLM-generated outputs must be schema-validated before storage.
9. Avoid AGPL/GPL/server-side reciprocal dependencies unless explicitly approved.
10. Add tests for core catalog and hashing behavior.

## Recommended Stack

- Backend: FastAPI + Python.
- Frontend: Next.js + TypeScript + Tailwind + shadcn/ui.
- Database: PostgreSQL.
- Object storage: local filesystem for MVP; S3/MinIO abstraction later.
- Queue: Redis + RQ or Celery.
- Parsing: Docling as primary parser; MarkItDown or Apache Tika as fallback.
- Validation: Pydantic.
- Markdown generation: Jinja2 templates or deterministic renderer.

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
