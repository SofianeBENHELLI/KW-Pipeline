# Decisions Index

Architecture Decision Records live at [`docs/adr/`](https://github.com/SofianeBENHELLI/KW-Pipeline/tree/main/docs/adr) — those are the canonical, version-locked documents. This page is a one-line index for fast navigation.

| ADR | Title | One-line summary |
|---|---|---|
| [ADR-001](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/adr/ADR-001-document-intelligence-mvp.md) | Document Intelligence MVP | Scope: ingestion + parsing + semantic extraction + review. No chatbot. No KG (originally — see ADR-012). |
| [ADR-002](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/adr/ADR-002-hash-versioning-and-duplicate-detection.md) | Hash Versioning + Dup Detection | SHA-256 on the immutable byte stream, never on the filename. |
| [ADR-003](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/adr/ADR-003-semantic-markdown-output.md) | Semantic Markdown Output | One Markdown file per `DocumentVersion`, deterministic Jinja2 template, frontmatter required. |
| [ADR-004](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/adr/ADR-004-orbital-frontend-stack.md) | Orbital Frontend Stack | Vite + React 18 + TypeScript + Vitest. Not Next.js. |
| [ADR-008](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/adr/ADR-008-semantic-schema-versioning.md) | Semantic Schema Versioning | Every payload carries `schema_version: Literal[...]`; ordered migrators upgrade in place. |
| [ADR-009](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/adr/ADR-009-semantic-enricher-boundary.md) | SemanticEnricher Boundary | LLM enrichers plug in via Protocol; output is always re-validated and forced to `needs_review`. |
| [ADR-010](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/adr/ADR-010-pdf-parser.md) | PDF Parser | `pdfplumber` for the MVP. Docling deferred (deps + cold-start cost). |
| [ADR-011](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/adr/ADR-011-openapi-codegen.md) | OpenAPI Codegen | Generate the typed frontend client from a committed `openapi.json` snapshot. CI fails on drift. |
| [ADR-012](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/adr/ADR-012-knowledge-graph-layer.md) | Knowledge Graph Layer | Neo4j behind `GraphStore` Protocol. Projection runs *after* `mark_validated`, never before. |
| [ADR-013](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/adr/ADR-013-llm-provider-and-no-langchain.md) | LLM Provider + No LangChain | Anthropic Claude via official SDK; vendor patterns from llm-graph-builder, not the package. |
| [ADR-014](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/adr/ADR-014-entity-extraction-prompt-and-cost.md) | Entity Extraction Prompt + Cost | Tool-use schema with citation enforcement; ephemeral prompt caching for the static system block. |

## Numbering

ADRs 005, 006, 007 are reserved gaps (drafts that never landed). New ADRs use the next available number. Don't renumber.

## When to write a new ADR

- A choice that constrains future code (graph store, LLM provider, codegen pipeline).
- A choice with a non-obvious trade-off worth recording for the next reader.
- A boundary or Protocol that the rest of the system has to honour.

## When *not* to write one

- Implementation details that change every quarter (prompt strings, model versions, threshold values).
- Pure refactors that don't change a contract.

If a planned change might violate an existing ADR, link the ADR in the PR description and either propose a superseding ADR or argue for an amendment.
