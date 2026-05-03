# MVP Backlog Review

Last reviewed: 2026-05-01 (post knowledge-layer pivot + audit pass).

## Current Health

- Backend default tests: **506 passed**, 1 deselected (`llm_integration` marker), coverage **95.31%**.
- Backend integration tests: **5 passing** against a real Neo4j 5.23 Community service container in CI (`pytest -m integration`).
- Frontend tests: **22 passed** (Vitest + Testing Library; `<KnowledgeGraphView />` covered).
- Frontend production build: passed. Initial JS dropped to **53 KB gz** after the lazy-split (#114); the graph chunk loads only when the panel mounts.
- Ruff: passed.
- Mypy: clean (34 source files, 0 errors).
- Vite/esbuild dev-server advisory (#79) resolved by the Vite 6.4.2 + Vitest 3.2.4 bump. The remaining `npm audit` advisories are transitive lodash/uuid through `@neo4j-nvl/*` and need a separate NVL bump.

## Code Audit Summary

The backend is no longer just an in-memory MVP:

- SQLite persistence covers catalog, raw extraction, semantic JSON, and Markdown payloads.
- Semantic payloads have schema-version loading/migration policy (ADR-008).
- Lifecycle transitions are enforced through the FSM with a server-side concurrent-write guard on the SQL `WHERE` predicate.
- Upload guardrails cover size and content-type allowlisting.
- All env-var configuration flows through a typed `Settings(BaseSettings)` model (#43).
- Structured JSON logging available via `KW_LOG_FORMAT=json` with a documented event catalogue (#42).
- `python-typecheck` (mypy) job in CI alongside ruff and pytest (#44).
- Cursor pagination on `GET /documents` (#38), streaming SHA-256 uploads (#41), idempotency keys (#60), and the openapi-codegen pipeline (#80) are all live on `main`.

The frontend is driving the live API:

- Compact pipeline widget + expanded review workspace render real data via the typed `openapi-fetch` client (#77, #80).
- `<KnowledgeGraphView />` panel in the review workspace reads from `GET /documents/{id}/graph`.
- Bundle is split: `@neo4j-nvl/base` ships only when the graph panel mounts.

The **knowledge layer** has shipped end-to-end behind opt-in env vars:

- Phase 0 (#108): ADR-012, ADR-013, architecture overview.
- Phase 1a (#109): `GraphStore` Protocol + in-memory + Neo4j impls, `KnowledgeProjector`, two read endpoints, full unit-test contract.
- Phase 1b (#110): Docker compose (`docker/docker-compose.yml` with Neo4j 5.23 Community + the API), CI integration job. The integration job caught two real Phase 1a Cypher bugs the in-memory tests couldn't see; both fixed in the same PR.
- Phase 1c (#111): `<KnowledgeGraphView />` wrapping `@neo4j-nvl/react`. Lazy-split on top in #114.
- Phase 2 (#112): `LLMClient` Protocol + Anthropic impl + `EntityExtractor` with section-level citation enforcement; ADR-014 covers prompt design and cost guardrails.
- Phase 2.1 (#115 + 2026-05-04 closure PR): Anthropic prompt caching for the entity extractor, exponential-backoff retry on 429/5xx (ADR-014 §4), and per-document `input_tokens` circuit breaker (ADR-014 §3). Phase 2 is **closed** as of 2026-05-04. Residual deferred follow-up: section batching to amortise cache hits ([#195](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/195)).

## Audit findings — 2026-05-01

A code-walk audit produced a punch-list. The bulk of findings are now tracked as new tickets:

| # | Title | Bucket |
|---|---|---|
| #120 | Backend — Register `install_error_handlers` in `create_app` (custom error envelope is dormant) | Correctness |
| #121 | Frontend — `ReviewWorkspace` and `PipelineWidget` have no component tests | Test gap |
| #122 | Frontend — Add request-level abort + dedup on review actions | Correctness |
| #123 | Frontend — Accessibility pass on review surface and pipeline widget | UX / a11y |
| #124 | Backend — Reconciliation endpoint for missed knowledge-layer projection / entity extraction | Robustness |
| #125 | Frontend — Bundle size budget + visualizer in CI | Tooling / performance |

**Audit claims that turned out to be false alarms** (verified in code, no ticket filed):

- `InMemoryGraphStore` thread-safety: a `threading.RLock()` is initialized in `__init__` and used in every mutating method (`apps/api/app/services/knowledge/graph_store.py:121`).
- `project_entities` null-deref risk: `_maybe_build_entity_extractor` only returns non-None when `_maybe_build_knowledge_layer` returns a non-None projector, so the route's existing guard on `entity_extractor is not None` implicitly covers `knowledge_projector is not None`. Worth a defensive assert at some point but not a real bug.

## Backlog Hygiene Findings

**2026-05-01 hygiene pass (issue #81) complete.** All 12 candidate issues were resolved.

**Issues closed since the 2026-04-30 audit doc:**

| PR / Commit | Closes | Description |
|---|---|---|
| `#119` | #44 | Add mypy static type checking to CI |
| `#118` | #42 | Structured logging and per-action audit trail |
| `#117` | #43 | Configuration via Pydantic Settings |
| `#116` | #79 | Clear Vite/esbuild dev-server audit advisory |
| `#115` | (Phase 2.1) | Prompt caching for the entity extractor |
| `#114` | (NVL bundle split) | Lazy-load the knowledge-graph view |
| `#113` | (docs) | Catch up with the knowledge-layer pivot |
| `#112` | #48 (in spirit) | LLM-driven entity extraction |
| `#111` | (Phase 1c) | Frontend graph view |
| `#110` | (Phase 1b) | Docker compose + integration CI |
| `#109` | (Phase 1a) | Graph projection backend |
| `#108` | (no issue — pivot ADRs) | Knowledge-layer Phase 0 |
| `#107` | #80 | OpenAPI codegen pipeline + typed openapi-fetch client |
| `#106` | #45 | PDF parser via pdfplumber (ADR-010) |
| `#105` | #63 | Real schema migration system |
| `#104` | #77 | Wire Orbital workbench to Harvester API |
| `#103` | #60 | Idempotency keys on POST endpoints |

## Recommended Work Order (now)

### 1. Audit-driven follow-ups (small, isolated, mechanical)

1. **#120** — Register error handlers in `create_app`. ~30 LOC + 1 test + OpenAPI re-snapshot.
2. **#125** — Bundle visualizer + size budget in CI. Mechanical.
3. **#121** — Add `ReviewWorkspace` and `PipelineWidget` tests. Adds ~10 cases.
4. **#122** — Abort + dedup on review actions. ~80 LOC + tests.
5. **#123** — A11y pass + axe-core dev gate. Adds devDep, fixes ~6 sites.
6. **#124** — Reconciliation endpoint. The biggest item in this group; needs a small admin route + a detection query + an integration test.

### 2. 3DEXPERIENCE widget readiness

1. **#78** widget embedding + brand token adapter. Open product decisions: first 3DEXPERIENCE container size, auth/context model. Bundle budget from #125 lands first.
2. Lodash/uuid advisories transitive through `@neo4j-nvl/*` — needs an NVL version bump or a fork. Probably one ticket below.

### 3. Phase 3 — vector RAG ([#186](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/186)), then chat surface

Per ADR-012 §3 last row + ADR-015 (Voyage AI as the embedding provider):

1. **Embedding provider — decided.** ADR-015 picks Voyage AI; the configuration scaffold + `EmbeddingClient` Protocol + `VoyageEmbeddingClient` + `FakeEmbeddingClient` already shipped in [#178](https://github.com/SofianeBENHELLI/KW-Pipeline/pull/178).
2. **First Phase 3 PR** ([#186](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/186)): provision the Neo4j HNSW vector index on `(:Chunk {embedding})`, write embeddings during projection, expose `GET /knowledge/search`. Tests stay on `FakeEmbeddingClient`; real Voyage gated by `pytest -m embedding_integration`.
3. **Chat surface (next sprint).** `chat_service.py` with mode dispatch (RAG / GraphRAG / Hybrid). The mode taxonomy comes from `neo4j-labs/llm-graph-builder/backend/src/QA_integration.py` but reimplemented directly against the Anthropic SDK (no LangChain). `<ChatPanel />` + `<ChatModeToggle />` in `apps/web/src/features/chat/`.

### 4. Robustness and operations

1. **#40** async parser queue + background jobs. More important now that validation has an LLM hop.
2. **#47** OCR for scanned PDFs.
3. **#20** revisit Docling (rejected at MVP per ADR-010, future evaluator).
4. **#82** bulk document loading.
5. **#87** retry / reprocess for failed uploads (overlaps with #124; consolidate scope).
6. **#96** runtime metrics, readiness probes, ingestion SLAs (depends on the structured-logs from #118 / #42).

### 5. Governance, security, and multi-tenant

These were filed during a forward-looking sweep and aren't urgent today, but they are real obligations once the platform has more than one consumer:

- **#83** auth + 3DEXPERIENCE user context.
- **#85** malware scanning and quarantine.
- **#84** retention / purge policy.
- **#91** workspace / project scoping.
- **#92** sensitive-data detection + redaction.
- **#94** backup / restore / DR.

### 6. Knowledge / handoff

- **#22** canonical knowledge-asset taxonomy (Phase 2 emits triples; #22 is the schema for what triples mean across documents).
- **#23** chunking + RAG export package — Phase 3 prerequisite.
- **#90** export validated assets and downstream handoff package.

### 7. Quality + DX

- **#66** strengthen test shape (assert contracts, not just exercise).
- **#24** golden document fixtures + regression snapshots.
- **#51** repo cleanup (unused deps, LICENSE, OpenAPI metadata).
- **#26** structured ingestion / extraction event logs — superseded by #42 in part; this issue still has scope around persisting events to a table (vs. the logger).
- **#93** customer demo dataset + end-to-end smoke script.

## Open Decisions

- Should duplicate bytes uploaded without `document_id` create a new family or attach to the original family? Issue #59 says the current behavior is wrong, but product semantics should be confirmed before changing it.
- Should review status and semantic validation status be committed in one catalog transaction? The route now avoids the obvious partial-mutation path, but a stronger service-level transaction may be warranted later — especially with the new knowledge-layer side effects in the same handler. (Tracked partially by #124.)
- Should unsupported content types fail during upload only, extraction only, or both? Current behavior supports configurable upload allowlists and parser registry failure at extraction time.
- What is the first 3DEXPERIENCE container size and authentication / context model? This drives `#78` and the graph view's compact mode.
- Phase 3 chat: which embedding provider? OpenAI text-embedding-3, Anthropic when available, or a local sentence-transformers model? Drives the deployment footprint.
- Reconciliation surface (#124): admin HTTP endpoint or CLI script?
