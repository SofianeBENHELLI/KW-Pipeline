# KW Pipeline

KW Pipeline is a SaaS-oriented Document Intelligence MVP focused on turning shared documents into governed semantic Markdown assets.

## First MVP Scope

The first implementation phase is intentionally limited to document ingestion and semantic extraction:

1. Upload or connect documents.
2. Compute a SHA-256 hash for every document.
3. Store the document in a catalog.
4. Detect duplicates and versions.
5. Extract text, tables, images, metadata, and semantic structure.
6. Generate one Markdown file per document version.
7. Expose extraction status and Markdown preview to a UI.

## Out of Scope for the First MVP

- No chatbot.
- No AURA integration yet.
- No full knowledge graph yet.
- No vector search until the semantic Markdown foundation is reliable.

## Agent Roles

- **Blueprint**: architect and challenger.
- **Harvester**: ingestion and semantic extraction developer.
- **Orbital**: UI and frontend developer.

## Recommended Technical Direction

- Backend: FastAPI + Python.
- Frontend: Next.js + TypeScript + Tailwind + shadcn/ui.
- Catalog: PostgreSQL.
- Object storage: local filesystem for MVP, MinIO/S3 abstraction later.
- Queue: Redis + RQ/Celery for MVP.
- Document parsing: Docling primary, MarkItDown or Apache Tika as fallback.
- Output: semantic JSON + governed Markdown with YAML frontmatter.

## Definition of Done for First MVP

A user uploads a document, the system computes its hash, stores it in a catalog, detects duplicates, extracts semantic content, generates a Markdown file, and lets the user review the result in the UI.
