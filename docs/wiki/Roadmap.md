<!-- $PublishToSwym{ "parent": "./Home.md" }$ -->

# Roadmap

The canonical, version-locked roadmap is [`docs/roadmap/mvp_backlog_review.md`](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/roadmap/mvp_backlog_review.md). This page summarizes status for newcomers.

## Shipped this quarter

**Core ingestion pipeline (May 2026):**
- SHA-256 streaming uploads (#41), cursor pagination (#38), idempotency keys (#60), real schema migrations (#63), PDF parser (#45), DOCX parser (#46).
- Pydantic Settings (#43), structured logging + audit trail (#42), mypy in CI (#44), ruff + tests + coverage gate.
- OpenAPI codegen pipeline (#80) — typed `openapi-fetch` frontend client kept in sync via CI diff.

**Knowledge layer (May 2026):**
- Phase 0: ADRs 012/013 + architecture overview (#108).
- Phase 1a: `GraphStore` Protocol + `KnowledgeProjector` + endpoints (#109).
- Phase 1b: Docker compose + integration CI job (#110). Caught two real Cypher bugs the in-memory tests couldn't see.
- Phase 1c: `<KnowledgeGraphView />` wrapping `@neo4j-nvl/react` (#111).
- Phase 2: LLM entity extraction with citation enforcement (#112).
- Phase 2.1: Anthropic prompt caching (#115), backoff retry on 429/5xx (ADR-014 §4), per-document input-token circuit breaker (ADR-014 §3) — Phase 2 closed 2026-05-04.
- Bundle lazy-split: NVL ships only when the panel mounts (#114).

## In flight

**Audit-driven follow-ups** (filed during the 2026-05-01 audit pass):

- [#120](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/120) Backend — Register `install_error_handlers` in `create_app`.
- [#121](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/121) Frontend — Add `ReviewWorkspace` and `PipelineWidget` component tests.
- [#122](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/122) Frontend — Add request-level abort + dedup on review actions.
- [#123](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/123) Frontend — Accessibility pass on review surface and pipeline widget.
- [#124](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/124) Backend — Reconciliation endpoint for missed knowledge-layer side-effects.
- [#125](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/125) Frontend — Bundle size budget + visualizer in CI.

## Next phases

- **3DEXPERIENCE widget readiness** (#78). Container size + auth/context model are open product decisions; #125 lays the bundle-budget groundwork.
- **Phase 3 — vector RAG** ([#186](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/186)). Embedding scaffold (ADR-015, Voyage AI) is on `main`; first PR provisions the Neo4j HNSW vector index, wires a `KnowledgeSearchService`, and exposes `GET /knowledge/search`. Chat surface follows.
- Phase-2.1 follow-up: section batching for the LLM extractor ([#195](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/195)) — final residual deferred from ADR-014.

## Deferred

The longer-tail backlog is grouped in `docs/roadmap/mvp_backlog_review.md`:

- Robustness / ops: #40 (async parser queue), #47 (OCR), #82 (bulk loading), #87 (retry), #96 (metrics + SLAs), #94 (DR).
- Governance / security: #83 (auth), #85 (malware scan), #84 (retention), #91 (workspace scoping), #92 (sensitive-data detection).
- Knowledge / handoff: #22 (taxonomy), #23 (chunking + RAG export), #90 (export package).
- Quality / DX: #66 (test-shape strengthening), #24 (golden fixtures), #51 (repo cleanup).

## Versioning policy

- Every Pydantic API model carries an explicit `schema_version: Literal[...]` (ADR-008). Migrations are append-only.
- Every PR that changes the HTTP contract regenerates `apps/api/openapi.json` and `apps/web/src/api/generated/schema.ts`. CI fails on drift.
- Lifecycle FSM transitions are constrained to the `ALLOWED_TRANSITIONS` map in `apps/api/app/models/document.py`. Adding a new state is a deliberate ADR-shaped change.
