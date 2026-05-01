# MVP Backlog Review

Last reviewed: 2026-05-01 (post knowledge-layer pivot).

## Current Health

- Backend default tests: **456 passed** (1 deselected — `llm_integration`
  marker), coverage **95.16%**.
- Backend integration tests: **5 passing** against a real Neo4j 5.23
  Community service container in CI (`pytest -m integration`).
- Frontend tests: **22 passed** (Vitest + Testing Library; Phase 1c added
  `<KnowledgeGraphView />` cases).
- Frontend production build: passed (bundle 2.0 MB / 600 KB gz —
  `@neo4j-nvl/base` dominates; lazy code-split is a planned follow-up).
- Ruff: passed.
- Python compileall: passed.
- Vite/esbuild development-server advisory (issue #79) resolved by bumping
  to Vite 6.4.2 + Vitest 3.2.4. `npm audit --audit-level=moderate` no longer
  reports any Vite/esbuild advisories; the remaining lodash/uuid advisories
  are transitive through `@neo4j-nvl/*` and are tracked separately.

## Code Audit Summary

The backend is no longer just an in-memory MVP:

- SQLite persistence covers catalog, raw extraction, semantic JSON, and
  Markdown payloads.
- Semantic payloads have schema-version loading/migration policy.
- Lifecycle transitions are enforced through the FSM.
- Upload guardrails cover size and content-type allowlisting.
- CORS is configurable for Orbital local development.
- Cursor pagination on `GET /documents` (#38), streaming SHA-256 uploads
  (#41), idempotency keys (#60), and the openapi-codegen pipeline (#80)
  are all live on `main`.

The frontend is now driving the live API:

- Compact pipeline widget + expanded review workspace render real data
  via the typed `openapi-fetch` client (#77, #80).
- A new `<KnowledgeGraphView />` panel (#111 / Phase 1c) is mounted in the
  review workspace and reads the projection from
  `GET /documents/{id}/graph`.

The **knowledge layer** has shipped end-to-end behind opt-in env vars:

- Phase 0 (#108): ADR-012, ADR-013, architecture overview.
- Phase 1a (#109): `GraphStore` Protocol + in-memory + Neo4j impls,
  `KnowledgeProjector`, two read endpoints, full unit-test contract.
- Phase 1b (#110): Docker compose (`docker/docker-compose.yml` with
  Neo4j 5.23 Community + the API), CI integration job. The integration
  job caught **two real Phase 1a Cypher bugs** the in-memory tests
  couldn't see; both fixed in the same PR.
- Phase 1c (#111): `<KnowledgeGraphView />` wrapping `@neo4j-nvl/react`.
- Phase 2 (#112): `LLMClient` Protocol + Anthropic impl + `EntityExtractor`
  with section-level citation enforcement; ADR-014 covers prompt design
  and cost guardrails.

## Backlog Hygiene Findings

**2026-05-01 hygiene pass (issue #81) complete.** All 12 candidate issues
were resolved:

- #1, #2, #4, #5, #9, #13, #17, #19, #28, #57, #61 — closed as **completed**
  (acceptance criteria fully satisfied on `main`).
- #18 — closed as **superseded** by #63 (real schema migration system).

The backlog is now clean. No further triage needed from the original
hygiene list.

## Closed since the 2026-04-30 audit doc

| PR / Commit | Closes | Description |
|---|---|---|
| `#107` / `9b1e1ec` | #80 | OpenAPI codegen pipeline + typed openapi-fetch client |
| `#108` / `d1297fa` | (no issue — pivot ADRs) | Knowledge-layer Phase 0 |
| `#109` / `0f7130d` | (Phase 1a) | Graph projection backend |
| `#110` / `5ba8f5f` | (Phase 1b) | Docker compose + integration CI |
| `#111` / `4db392b` | (Phase 1c) | Frontend graph view |
| `#112` / `313ddd0` | #48 (in spirit) | LLM-driven entity extraction |
| `#106` / `9fc1462` | #45 | PDF parser via pdfplumber (ADR-010) |
| `#104` / `25cb005` | #77 | Wire Orbital workbench to Harvester API |
| `#105` / `b556780` | #63 | Real schema migration system |
| `#103` / `66ae52d` | #60 | Idempotency keys on POST endpoints |
| `e15bf69` | #58 | Reject whitespace-only uploads + wire ParserRegistry |
| `dc0b7ca` | #49 | SemanticEnricher Protocol and ADR-009 |
| `d1c3e92` | #46 | DOCX parser via python-docx |
| `67fa258` | #41 | Stream uploads and SHA-256 |
| `d252c67` | #38 | Cursor pagination on GET /documents |

## Recommended Work Order (now)

### 1. Knowledge layer follow-ups

These are mechanical wins that build directly on what just landed:

1. **Lazy code-split the graph slice.** `apps/web/src/features/graph/`
   should `React.lazy(() => import("./KnowledgeGraphView"))` so reviewers
   who never open the graph tab don't pay the 600 KB-gz NVL cost.
   Small, isolated PR.
2. **Phase 2.1 — prompt caching.** Wire `cache_control: {"type":
   "ephemeral"}` per ADR-014 §2 to the static system block of the
   entity-extraction prompt. Token-cost reductions on repeat sections.
3. **`#43` Pydantic Settings.** Replace the `os.environ.get` reads
   for `KW_*` and `ANTHROPIC_API_KEY` with a settings model. Also
   normalises the env-var prefix story (existing `MAX_UPLOAD_BYTES`,
   `ALLOWED_CONTENT_TYPES`, `CORS_ALLOWED_ORIGINS` are unprefixed; the
   knowledge-layer ones are `KW_`).
4. **`#42` structured logging / audit trail.** The knowledge-layer
   side-effects already log `knowledge.projection.written` /
   `knowledge.projection.failed`; #42 turns those into structured JSON.

### 2. 3DEXPERIENCE widget readiness

1. **`#78` widget embedding + brand token adapter.** The graph and
   review surfaces both need to fit a small container. Open decisions:
   first 3DEXPERIENCE container size, auth/context model.
2. ~~**`#79` Vite/esbuild audit remediation.**~~ Resolved: Vite 5.4.21 →
   6.4.2 + Vitest 2.1.9 → 3.2.4. Lodash/uuid advisories transitive through
   `@neo4j-nvl/*` remain and need a separate NVL bump.

### 3. Phase 3 — chat surface (deferred until Phase 2 has been used in anger)

Per ADR-012 §3 last row + a Phase 3 ADR (TBD):

1. Embedding model + vector index decision (deferred to a Phase 3 ADR;
   either a remote embeddings endpoint or local sentence-transformers).
2. `chat_service.py` with mode dispatch (RAG / GraphRAG / Hybrid). The
   mode taxonomy comes from `neo4j-labs/llm-graph-builder/backend/src/QA_integration.py`
   but reimplemented directly against the Anthropic SDK (no LangChain).
3. `<ChatPanel />` + `<ChatModeToggle />` in `apps/web/src/features/chat/`.

### 4. Robustness items still queued

After the knowledge-layer work settles:

1. `#40` async parser queue + background jobs (becomes more important
   once entity extraction adds an LLM hop to validation).
2. Real parsers beyond PDF/DOCX/plain text: `#47` (OCR), `#20`
   (Docling integration was rejected for the MVP — ADR-010 — but a
   future evaluator).
3. `#44` mypy/pyright in CI.

## Open Decisions

- Should duplicate bytes uploaded without `document_id` create a new
  family or attach to the original family? Issue #59 says the current
  behavior is wrong, but product semantics should be confirmed before
  changing it.
- Should review status and semantic validation status be committed in
  one catalog transaction? The route now avoids the obvious
  partial-mutation path, but a stronger service-level transaction may
  be warranted later — especially with the new knowledge-layer side
  effects in the same handler.
- Should unsupported content types fail during upload only, extraction
  only, or both? Current behavior supports configurable upload
  allowlists and parser registry failure at extraction time.
- What is the first 3DEXPERIENCE container size and authentication /
  context model? This drives `#78` and the graph view's compact mode.
- Phase 3 chat: which embedding provider? OpenAI text-embedding-3,
  Anthropic when available, or a local sentence-transformers model?
  Drives the deployment footprint.
