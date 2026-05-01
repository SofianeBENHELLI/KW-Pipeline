# Document Intelligence MVP

## Goal

Build a document pipeline that turns uploaded business documents into
auditable semantic Markdown assets. The MVP must make ingestion, extraction,
lineage, validation status, and failures visible instead of hiding uncertainty.

## Non-Goals

- Chatbot / RAG / GraphRAG UI (Phase 3 — separate ADR).
- AURA interface.
- Vector search.
- MCP integration.
- Silent semantic enrichment without review.

The original "knowledge graph" non-goal was retired in 2026-05 by
[ADR-012](../adr/ADR-012-knowledge-graph-layer.md): a knowledge graph
layer is now in scope as an **opt-in**, **post-review** projection of
validated documents. It does not bypass the review gate (graph
projection runs as a side-effect of `mark_validated`, never before),
and the layer is dormant unless `KW_KNOWLEDGE_LAYER_ENABLED=true`. See
[`knowledge_layer.md`](knowledge_layer.md).

## System Slices

### Harvester

Harvester owns ingestion and extraction:

1. Accept document uploads.
2. Compute SHA-256 from immutable file bytes.
3. Store raw file bytes through a storage abstraction.
4. Persist document, version, and ingestion metadata.
5. Detect duplicate uploads by hash.
6. Queue or run deterministic raw extraction.
7. Persist raw extraction JSON and parser metadata.
8. Convert raw extraction into semantic JSON.
9. Generate one Markdown file per document version.
10. Mark semantic output as `NEEDS_REVIEW`.

### Orbital

Orbital owns human review:

1. Upload documents.
2. Display catalog and document details.
3. Show hash, version, duplicate, lifecycle, and extraction status.
4. Preview generated Markdown.
5. Inspect semantic sections, warnings, and source lineage.
6. Validate or reject extraction results.
7. Show failed extraction states clearly.

Orbital should start as an efficient reviewer workbench rather than a fancy
application shell. The frontend must support a compact widget mode for future
3DEXPERIENCE dashboard embedding and an expanded mode for detailed review. The
same components should serve both modes where possible.

## Lifecycle

Document versions move through these states:

| State | Meaning |
| --- | --- |
| `UPLOADED` | File was received by the API. |
| `HASHED` | SHA-256 was computed from original bytes. |
| `DUPLICATE_DETECTED` | Same hash already exists in the catalog. |
| `STORED` | Raw file bytes and catalog metadata were persisted. |
| `EXTRACTING` | Parser is processing the stored file. |
| `EXTRACTED` | Raw extraction JSON was stored. |
| `SEMANTIC_READY` | Semantic JSON and Markdown were generated. |
| `NEEDS_REVIEW` | Semantic output requires human validation. |
| `VALIDATED` | Human reviewer accepted the semantic output. |
| `REJECTED` | Human reviewer rejected the semantic output. |
| `FAILED` | Upload, storage, parsing, semantic generation, or Markdown generation failed. |

## Trust Rules

- SHA-256 must be computed before storage decisions are finalized.
- Duplicate detection is based on file hash, not filename.
- Every generated semantic asset must point back to source references when
  source lineage is available.
- Missing lineage must be surfaced as a warning.
- Unsupported semantic claims must be marked `needs_review`.
- LLM assistance plugs in via the `SemanticEnricher` Protocol
  (ADR-009) and the `LLMClient` Protocol (ADR-013); both forbid
  bypassing schema validation. The `SemanticExtractor` re-validates
  every enricher output and forces `review_status="needs_review"`.
- **Nothing without provenance reaches the knowledge graph.** Phase 2
  entity-extraction triples without `source_reference_ids` are dropped
  to `warnings`. Graph edges always carry a `source_reference_id`.
- Knowledge-layer side-effects of validation **never roll back the
  catalog**. A graph or LLM outage logs and is retried later; the
  SQLite catalog is the source of truth for "this version is
  validated".
