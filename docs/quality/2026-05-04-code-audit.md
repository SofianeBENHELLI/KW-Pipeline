# Code audit — 2026-05-04

Comprehensive audit of the KW Pipeline codebase. Two parallel passes:

- **Backend** (`apps/api/`): ~95 findings, sorted by severity.
- **Frontend** (`apps/{web,widget,explorer,widget-preview}/`): 71
  findings, with cross-app duplication as the dominant theme (12 of
  71 findings).

The audit was scoped against an already-strong baseline: 95% backend
coverage, mypy strict, ruff clean, ~750 backend tests + 105 web
tests. Findings target what would make the codebase **faster**,
**more maintainable**, and **easier to onboard a new contributor**
to — not what is broken today.

This doc is the canonical reference. Per-finding rows live in
[backend appendix](#backend-appendix) and
[frontend appendix](#frontend-appendix). Follow-up issues are filed
for the P0 items.

---

## 1. Headline findings

### 1.1 Top 10 priorities — backend

1. **Split `routes.py`** (1040 lines, 16 handlers + helpers) into
   `routes/upload.py`, `routes/lifecycle.py`, `routes/knowledge.py`,
   `routes/admin.py`. Single biggest readability win.
2. **Move validation side-effects out of the route handler.**
   `_record_review` couples FSM, semantic save, projector, entity
   extraction in one route handler — should live in a `ReviewService`.
3. **Add the missing SQLite indexes.** `document_versions.document_id`
   and `documents(created_at, id)` — listing pages will degrade
   linearly with corpus size today.
4. **Bulk embedding write-back.** `set_chunk_embedding` is currently
   called once per chunk on Neo4j; one UNWIND batch instead. Critical
   path for Phase 3 RAG performance.
5. **Reverse-index for `delete_subgraph_for_version`.** Current impl
   is O(versions × nodes) per re-projection; trivially fixable with a
   reverse refcount map.
6. **Deduplicate `build_services` / `build_persistent_services`.**
   90% identical wiring, the kind of thing a new contributor breaks
   accidentally.
7. **Snapshot test for `MarkdownGenerator`.** The markdown is a
   public artifact and there is **zero** protection against silent
   format drift.
8. **Property-based tests for `chunk_relations` + `topic_clustering`.**
   Both are deterministic algorithms perfect for Hypothesis; current
   tests only assert on hand-picked fixtures.
9. **Type the `dict[str, Any]` boundaries.** `audit_event.payload`,
   `LLMClient.tool_input`, chat `triples` 4-tuples — typed dataclasses
   surface 80% of latent shape bugs at mypy time.
10. **Wire idempotency-store TTL purge.** `purge_expired` exists, no
    scheduler calls it, both stores grow forever.

### 1.2 Top 10 priorities — frontend

1. **Cross-app duplication is the #1 concern.** ApiError class,
   `asApiError`, envelope-parsing helper, base-URL resolution,
   StatusBadge, icon registries, `DocumentVersionStatus` literal —
   all reimplemented 2-3 times. Bug fixes silently miss whichever
   widget the maintainer forgot.
2. **Hand-curated widget/explorer types will drift.** The file
   headers admit it. Either run `openapi-typescript` through their
   webpack toolchain or publish `@kw-pipeline/api-types` from
   `apps/web/src/api/generated/schema.ts`.
3. **`apps/explorer/App.tsx` (1093 LOC) + `GraphCanvas.tsx` (1320
   LOC).** Riskiest single files in the repo. Extract focus-history,
   hash-deep-link, and search hooks; split GraphCanvas into
   layout/viewport/renderers files.
4. **Widget and Explorer have zero component tests.** Vitest doesn't
   even exist in their devDependencies. UploadQueue (concurrency
   drain + drag-drop) is the most stateful component in the repo and
   is uncovered.
5. **No ESLint anywhere.** No `react-hooks` rule means `useEffect`
   dependency mistakes go uncaught.
6. **GraphCanvas layout-recompute on every Set toggle.**
   `expandedClusters` / `expandedDocs` are cloned on each toggle so
   `useLayout` always invalidates — sluggish at 1000+ nodes.
7. **A11y gaps in `apps/explorer`.** Clickable `<div>` rows, `<span>`
   checkboxes, `<a>` without href, `<li onClick>`. ~8 violations all
   sharing the same fix pattern.
8. **Two different bundlers, near-identical configs.** Webpack
   configs in `apps/widget` and `apps/explorer` are 95% identical —
   the explorer comment even flags it. Extract a base config.
9. **No bundle budgets on widget / explorer.** `performance: { hints:
   false }`, no chunking, no code splitting.
10. **`apps/explorer` has no README.** New contributors land on a
    1093-LOC App.tsx with no signposting.

### 1.3 The single highest-leverage refactor

Across the 71 frontend findings, **12 of them collapse into one
recommendation**: create a shared package (`apps/_shared/` or a
workspace package) that owns:

- API base URL + ApiError class + `asApiError` envelope parser
- Generated OpenAPI types (consumed everywhere)
- StatusBadge + status taxonomy
- Icon registry
- Format helpers (`formatTimestamp`, `truncate`, `latestVersion`)
- Domain types accessor helpers (`stringProp`, `numberProp`)
- Webpack base config

This single piece of work resolves duplication, drift risk, and the
"bug fix forgotten in one of three apps" pattern.

---

## 2. Severity breakdown

### 2.1 Backend

| Severity | Count | Themes |
|---|---|---|
| **P0** | 8 | routes.py split, side-effect coupling in routes, missing indexes, bulk embedding write, graph_store reverse-index, FSM property tests |
| **P1** | 38 | refactor (decorator-based idempotency, dataclass returns, bulk graph queries), perf (chunk_relations O(n²), audit batch flush, idempotency TTL), test (snapshots, property tests, error envelope assertions), typing (audit payloads, LLM tool input, chat triples), observability (cache hit/miss, latency breakdown) |
| **P2** | 35 | refactor (smaller extracts, registry-based dispatch), perf (LRU caps, sequential I/O), test (concurrent writes, dim mismatch), typing (cursor cache shape), observability (extra fields), security (sanitize control chars), simplify (state machine helper, FSM precondition) |
| **P3** | 14 | doc (module docstrings, decision history capture), simplify (regex doctests, generator patterns), typing (single-pass iterators) |
| **Total** | **95** | |

### 2.2 Frontend

| Severity | Count | Themes |
|---|---|---|
| **P0** | 8 | cross-app duplication (ApiError, types, StatusBadge, icons), App.tsx and GraphCanvas size, no tests in widget/explorer |
| **P1** | 24 | hand-curated types drift, refactor (extract hooks, sub-components), perf (gradient explosion, layout recompute, cache miss), a11y (clickable divs/spans/anchors), tooling (no ESLint, tsconfig drift), test (no widget/explorer tests, future placeholders) |
| **P2** | 26 | refactor (key={i}, fallback duplication, search index extraction), typing (any casts, abort-error helpers), perf (debounce dedupe, batch fetches), bundle (no chunking, no budget enforcement), test (axe coverage, e2e gap) |
| **P3** | 13 | refactor (constant duplication), doc (READMEs, design lineage), a11y (incomplete ARIA roles), typing (color tokens leaking) |
| **Total** | **71** | |

---

## 3. Cross-cutting recommendations

### 3.1 Shared frontend package — the meta-action

Create `apps/_shared/` (or workspace package `@kw-pipeline/shared`)
exporting:

```
apps/_shared/
├── package.json
├── tsconfig.base.json          # extends in all 3 apps
├── webpack.base.js             # widget + explorer extend
├── api-core/
│   ├── ApiError.ts             # shared class so instanceof works
│   ├── asApiError.ts           # envelope parser
│   ├── baseUrl.ts              # KW_API_BASE_URL resolution
│   └── isAbortError.ts
├── api-types/
│   └── schema.ts               # re-export from openapi-typescript output
├── ui/
│   ├── StatusBadge.tsx         # one canonical impl + status table
│   └── icons/
│       └── registry.tsx        # one IconName union, one glyph table
├── domain/
│   ├── document.ts             # latestVersion, version helpers
│   └── graph.ts                # stringProp, numberProp accessors
├── format/
│   ├── timestamp.ts            # formatTimestamp
│   ├── truncate.ts
│   └── filesize.ts
└── constants.ts                # DEFAULT_TOP_K, SEARCH_DEBOUNCE_MS, etc.
```

Migration order (lowest risk first):
1. `asApiError` + `ApiError` (pure functions; widget = explorer
   byte-for-byte today).
2. Constants + format helpers.
3. API types via openapi-typescript codegen step in webpack.
4. StatusBadge unification (involves CSS reconciliation).
5. Icons (largest payload; needs a glyph audit).
6. Webpack base config.

Effort: 3-5 days end-to-end. Saves ~600 LOC of duplication,
eliminates "bug fix forgotten in one app" risk class.

### 3.2 Performance hot path — Phase 3 KG projection

Five P0/P1 perf findings concentrate on the same code path:

```
mark_validated()
  └─► KnowledgeProjector.project()
        ├─► delete_subgraph_for_version(version_id)            ← O(versions × nodes)
        ├─► project_chunks()
        │     └─► _embed_and_store_chunks()
        │           └─► set_chunk_embedding(chunk_id, vector)  ← N round-trips
        ├─► project_chunk_relations()
        │     └─► _ordered_pairs() + _classify_pair()          ← O(chunks²)
        └─► (writes through GraphStore one stage at a time)
```

This is the **production hot path** for any deployment that turns
on the knowledge layer. Five bottlenecks chained means a 1000-chunk
document re-projection is currently O(N² + N + N × neo4j_latency).
Fixable end-to-end in one focused PR: bulk embedding write, reverse
index, inverted-index for chunk relations.

### 3.3 Test shape — assert contracts

The 95% coverage is misleading: many tests **exercise** without
**asserting** the contract. Concrete actions:

- **Snapshot the markdown generator output** to detect format drift
  (P1 test #1 in the audit).
- **Add Hypothesis tests** on `chunk_relations.relations_for`,
  `topic_clustering`, `compute_sha256`, the lifecycle FSM (P0/P1).
- **Replace `assert response.status_code == 404`** with the
  `_ErrorEnvelopeAssert` helper that already exists in
  `test_error_contract.py` — every error-path test should use it.
- **Add the test that proves graph-projection failure during
  validation does NOT roll back the FSM** — the contract is
  documented in ADRs but never asserted (P2 test).

### 3.4 Observability — fill the cache hit/miss gap

The audit-followups handover (2026-05-04) flagged "embedding cache
hit-rate observability" as a known gap. The audit confirms it plus
adjacent gaps:

- `knowledge.search.queried` event has no cache hit/miss field.
- `knowledge.llm.retrying` fires on retry but there's no
  `knowledge.llm.exhausted` when retries are exhausted.
- `knowledge.projection.written` doesn't carry `latency_ms`.
- `chat.answered` measures end-to-end latency but doesn't break it
  down (vector / graph / llm).

These are all one-line `extra={...}` additions; they are the
foundation of the SLO discipline EPIC-3 (#96) needs to land.

---

## 4. Recommended sequencing

### Sprint S+1 (this audit's payload)

Goal: clear the P0s on both sides without expanding scope.

**Backend (one PR each)**

1. `apps/api/app/routes/` package extraction — 1040 LOC → 4 files
   ~250 LOC each (split #1, P0 above).
2. `ReviewService.handle_validation()` extraction — moves
   `_record_review` side-effects out of the route (split #2).
3. SQLite index migration — `document_versions.document_id` +
   `documents(created_at, id)` (split #3).
4. Bulk embedding write-back — `GraphStore.bulk_set_chunk_embeddings`
   + Neo4j UNWIND impl (split #4).
5. `delete_subgraph_for_version` reverse-index (split #5).

**Frontend (one PR for the shared-package skeleton, then migrations)**

6. Skeleton `apps/_shared/` package + first migration:
   `asApiError` + `ApiError` extracted from widget + explorer.
7. ESLint flat config wired into all three apps — react-hooks,
   jsx-a11y, @typescript-eslint.
8. Vitest scaffolding for widget + explorer (no test bodies yet,
   just the harness).

### Sprint S+2

Goal: kill the typing drift and fix the explorer's largest files.

9. `openapi-typescript` codegen wired into widget + explorer; drop
   hand-curated `api/types.ts`.
10. Extract hooks from `apps/explorer/App.tsx` (`useFocusHistory`,
    `useHashDeepLink`, `useCorpusSearch`) → App.tsx <250 LOC.
11. Split `apps/explorer/GraphCanvas.tsx` into layout / viewport /
    renderer modules.
12. Split `apps/web/PipelineWidget.tsx` 485 LOC → 5 sub-components.

### Sprint S+3 (test depth)

13. MarkdownGenerator snapshot test.
14. Hypothesis tests on chunk_relations + topic clustering + FSM.
15. Component tests on the 6 most state-heavy widgets/explorer
    components.
16. Property tests on the chat citation validator.

### Continuous backlog

The 60+ P2/P3 findings are filed against the audit doc as a single
hygiene tracker. They get picked up as side-quests during epic work
(any PR that already touches a service should land its P2 fixes in
the same diff).

---

## 5. Findings that overlap with planned EPICs

Several P0/P1 findings are pre-conditions for in-flight planning:

| Finding | Overlaps with | Why it matters |
|---|---|---|
| `routes.py` split | EPIC-A HITL routing (#215) | New `routes/knowledge.py` is the natural home for the HITL router endpoints |
| ReviewService extraction | EPIC-B Iterop (#216) | The `IteropAdapter` callback path lives in `ReviewService`, not in a route handler |
| SQLite indexes | EPIC-C catalog (#217) | The new `GET /knowledge/catalog` endpoint is a pagination hot path; needs the indexes |
| Shared frontend package | EPIC-D scoping (#218) + EPIC-C catalog | Scope picker (D) and catalog view (C) both need StatusBadge + types — better to land them once in shared |
| Snapshot test on Markdown | EPIC-1 taxonomy (#210/#211) | The taxonomy work changes Markdown frontmatter; without a snapshot we won't notice drift |

This gives a strong reason to **land the audit-driven cleanup
before** EPICs A–D start: every EPIC benefits.

---

## 6. Backend appendix

The full ~95 backend findings are in the agent transcript. Top
40 reproduced here (P0 + P1 only); P2/P3 in the appendix issue
([P2/P3 backend tracker](#)).

### 6.1 P0 (8)

```
P0 | REFACTOR    | apps/api/app/routes.py:156-1040     | routes.py is 1040 lines — one factory function holds 16 endpoint handlers + 4 inline helpers          | Split into routes/upload.py, routes/lifecycle.py, routes/knowledge.py, routes/admin.py
P0 | REFACTOR    | apps/api/app/routes.py:824-922      | _record_review mixes FSM, semantic save, projector, and entity extraction in one route handler        | Move into ReviewService.handle_validation()
P0 | PERFORMANCE | apps/api/app/services/catalog_store.py:404-477 | document_versions.document_id has no index — N+1 reads grow linearly with corpus               | Add CREATE INDEX migration
P0 | PERFORMANCE | apps/api/app/services/migrations.py:46-102 | No index on documents.created_at — full sort per page above ~10k docs                            | Add composite (created_at, id) index
P0 | PERFORMANCE | apps/api/app/services/knowledge/graph_store.py:217-222 | delete_subgraph_for_version is O(versions × node_ids) per re-projection                | Maintain reverse refcount map node_id -> set[version_id]
P0 | PERFORMANCE | apps/api/app/services/knowledge/projector.py:226-287 | _embed_and_store_chunks writes one Voyage embedding per chunk via one round-trip per chunk | Add GraphStore.bulk_set_chunk_embeddings with UNWIND on Neo4j
P0 | PERFORMANCE | apps/api/app/services/knowledge/chat_service.py:241-269 | _collect_triples_for_documents calls find_subgraph_for_document once per seed doc — N round-trips | Add GraphStore.find_subgraphs_for_documents
P0 | TEST        | apps/api/tests/test_lifecycle_fsm.py | No Hypothesis-based property test asserts FSM reachability or failure_reason invariants            | Add hypothesis strategies + walk transitions
```

### 6.2 P1 — top 32 (full list in audit transcript)

(Reproduced verbatim from the agent — see the audit transcript
attached to PR for the full table.)

```
P1 | REFACTOR    | dependencies.py:327-453             | build_services and build_persistent_services 90% duplicate
P1 | REFACTOR    | knowledge/chat_service.py:271-294   | hits + triples 4-tuples — no Protocol or model
P1 | REFACTOR    | services/markdown_generator.py:67-84| source_references stored as raw dict — type-erased
P1 | REFACTOR    | knowledge/entity_extractor.py:244-331 | _extract_section + _extract_batch duplicate ~100 lines
P1 | REFACTOR    | services/extraction_job_service.py:55-118 | extract repeats mark_failed + log 3 times
P1 | REFACTOR    | routes.py:578-657                   | extract / retry / generate_semantic share idempotency boilerplate
P1 | REFACTOR    | services/document_service.py:129-202 | _upload_new_family + _append_new_version + upload_stream replicate logic
P1 | PERFORMANCE | knowledge/chunk_relations.py:265-277 | _ordered_pairs is O(n²) — 500k pairwise scans per 1000-chunk projection
P1 | PERFORMANCE | knowledge/graph_store.py:230-265    | find_subgraph_for_document scans every node and edge
P1 | PERFORMANCE | routes.py:732-786                   | get_raw_file reads entire file into memory before responding
P1 | PERFORMANCE | knowledge/projector.py:170-189      | Stage outputs concatenated then re-Pydantic-validated
P1 | PERFORMANCE | services/catalog_store.py:455-477   | list_documents opens 2 sequential queries — every page = 2× SQLite latency
P1 | PERFORMANCE | services/idempotency_store.py:163-170 | purge_expired exists but is never invoked anywhere
P1 | PERFORMANCE | services/audit_event_store.py:205-219 | SQLiteAuditEventStore.append serialises every log event under a coarse RLock
P1 | PERFORMANCE | knowledge/projector.py:128          | _embedding_cache is unbounded process-local
P1 | TEST        | tests/test_markdown_generation.py   | No snapshot test pins exact rendered Markdown bytes
P1 | TEST        | tests/test_chunk_relation_service.py| No property-based test on relations_for invariants
P1 | TEST        | tests/test_knowledge_projector.py   | No round-trip test: project + find_subgraph
P1 | TEST        | tests/test_routes_errors.py:29-84   | Tests assert status_code only, not error.code/retryable/remediation
P1 | TEST        | tests/test_idempotency.py           | No TTL expiry test — purge logic uncovered
P1 | TEST        | tests/test_knowledge_chat.py        | No property test on _validate_citations false negatives
P1 | TYPING      | services/markdown_generator.py:75-84 | _format_location takes ref: dict — implicit Any leaking
P1 | TYPING      | services/document_service.py:212-245 | list_documents_page returns tuple — define DocumentPage dataclass
P1 | TYPING      | knowledge/chat_service.py:161-165   | triples: list[tuple[str, GraphNode, GraphEdge, GraphNode]] — anonymous 4-tuple
P1 | TYPING      | services/audit_event_store.py:61    | payload: dict[str, Any] — no schema enforcement on event vocabulary
P1 | TYPING      | knowledge/llm_client.py:80-86       | complete_with_tool returns tuple[dict, dict] — both halves opaque
P1 | DOC         | knowledge/projector.py:97-103       | "Stateless" docstring contradicts the mutable _embedding_cache field
P1 | DOC         | knowledge/chat_service.py:64        | EMPTY_RETRIEVAL_ANSWER references "deferred decision" — undocumented
P1 | DOC         | knowledge/embedding_client.py:51-58 | VOYAGE_MODEL_DIMS hardcodes 6 ids — no policy when probe is hit
P1 | OBSERVABILITY | knowledge/search.py:93-102        | knowledge.search.queried lacks cache hit/miss data
P1 | OBSERVABILITY | knowledge/entity_extractor.py     | No cumulative usage logged on success — only on budget_exceeded
P1 | OBSERVABILITY | knowledge/llm_client.py:318-327   | knowledge.llm.retrying fires on retry; no knowledge.llm.exhausted on giving up
P1 | SIMPLIFY    | services/catalog_store.py:526-571   | update_version_status uses sentinel __no_legal_predecessor__ — unreachable in practice
P1 | SIMPLIFY    | routes.py:824-866                   | _record_review redundant ValueError handling vs mark()
P1 | SIMPLIFY    | knowledge/chunk_relations.py:295-365| _classify_pair is 70 lines of nested conditionals — express as rule list
P1 | SIMPLIFY    | services/extraction_job_service.py:11-19 | ExtractionFailed.__init__ duplicates args[0]
P1 | SECURITY    | knowledge/entity_extractor.py:568-583 | _sanitize line-by-line lstrip — no   / BOM / RTL / zero-width handling
```

### 6.3 P2 + P3 (49)

Tracked under one umbrella issue (see §7). Themes: smaller refactor
extracts, registry-based dispatch, cursor module, type-erased
unicode in storage, pure-function-buried-in-service, observability
LRU caps, etc.

---

## 7. Frontend appendix

### 7.1 P0 (8)

```
P0 | DUPLICATION | apps/widget/src/api/client.ts vs apps/explorer/src/api/client.ts vs apps/web/src/api/client.ts | 3 near-identical ApiError + asApiError + envelope-parser implementations | Extract to apps/_shared/api-core/
P0 | DUPLICATION | apps/widget/src/api/types.ts vs apps/explorer/src/api/types.ts                  | DocumentVersionStatus literal hand-duplicated in 2 places, drifts | Generate from OpenAPI everywhere
P0 | DUPLICATION | apps/web/src/ui/StatusBadge.tsx vs apps/widget/src/components/StatusBadge.tsx   | Two divergent StatusBadge impls with different status→variant tables | Move to apps/_shared/ui/StatusBadge
P0 | DUPLICATION | apps/widget/src/components/icons.tsx (190 LOC) vs apps/explorer/src/components/icons.tsx (388 LOC) | Two SVG icon registries, overlapping glyphs, different IconName unions | Consolidate apps/_shared/icons/
P0 | DUPLICATION | apps/widget/src/api/client.ts:96-119 vs apps/explorer/src/api/client.ts:88-111  | asApiError byte-for-byte identical | First slice — pure function, no React, zero risk
P0 | REFACTOR    | apps/explorer/src/App.tsx:70-911                                                | App.tsx is 1093 LOC, 19 useState + 30 hook calls in one component         | Extract hooks; aim for App.tsx < 250 LOC
P0 | REFACTOR    | apps/explorer/src/components/GraphCanvas.tsx:1-1320                             | GraphCanvas is 1320 LOC, mixes layout/viewport/event/renderers/defs/HUD   | Split into 4 files
P0 | TEST        | apps/widget/src/**/*.tsx + apps/explorer/src/**/*.tsx                           | Zero component tests in widget OR explorer                                | Add Vitest + jsdom; cover the most stateful components
```

### 7.2 P1 — top 24 (full list in audit transcript)

(Reproduced verbatim from the agent — see the audit transcript
attached to PR for the full table.)

```
P1 | TYPING       | apps/widget/src/api/types.ts (148 LOC) + apps/explorer/src/api/types.ts (168 LOC) | Hand-curated types acknowledged stale-prone in file headers | Wire openapi-typescript through webpack
P1 | REFACTOR     | apps/web/src/App.tsx:51-204                            | useDocumentCatalog has two parallel filter codepaths (useEffect + refreshAll)
P1 | REFACTOR     | apps/web/src/features/pipeline/PipelineWidget.tsx:50-485 | 485 LOC mixes upload state, batch reporting, metrics, list, filter bar
P1 | REFACTOR     | apps/web/src/features/graph/KnowledgeGraphView.tsx:73-242 | Component handles fetch+empty+filter+selection+lazy+inspector all in one
P1 | REFACTOR     | apps/explorer/src/state/use-explorer-data.ts:65-199    | useEffect mutates module-level CLUSTERS singleton from inside the effect
P1 | PERFORMANCE  | apps/web/src/App.tsx:147-184                          | Filter useEffect duplicates refreshAll body; 3 serial fetches after each mutation
P1 | PERFORMANCE  | apps/explorer/src/components/GraphCanvas.tsx:1127-1148 | <linearGradient> emitted per edge into <defs> — N gradients per render
P1 | PERFORMANCE  | apps/explorer/src/components/GraphCanvas.tsx:902-929   | visibleSet rebuilds adjacency Record on every render
P1 | PERFORMANCE  | apps/explorer/src/components/GraphCanvas.tsx:160-410   | useLayout recomputes 1000+ node layout on every Set toggle (Sets cloned)
P1 | PERFORMANCE  | apps/web/src/App.tsx:186                              | documents.find linear-scan in render
P1 | PERFORMANCE  | apps/widget/src/sections/UploadQueue.tsx:72-108        | drain() fragile — schedules uploads inside setItems closure with setTimeout(drain, 0)
P1 | UX           | apps/widget/src/components/StatusBadge.tsx:38         | STATUS_PRESET[status] ?? fallback silently swallows unknown statuses
P1 | A11Y         | apps/explorer/src/App.tsx:946                         | <span onClick> as a checkbox — not keyboard reachable
P1 | A11Y         | apps/explorer/src/App.tsx:677-702                     | Document <div onClick> rows — not keyboard activatable
P1 | A11Y         | apps/explorer/src/App.tsx:968                         | Search popover <div onClick> — not keyboard accessible
P1 | A11Y         | apps/explorer/src/components/DetailPanel.tsx          | <li onClick> + <a onClick> without href — not keyboard activatable
P1 | A11Y         | apps/widget/src/sections/DocumentsList.tsx:222-238    | <li onClick> rows lack tabIndex + keyDown
P1 | DOC          | apps/widget/README.md                                 | Build-focused, missing "where to put X" guide
P1 | DOC          | apps/explorer/                                       | No README — new contributors land on 1093 LOC App.tsx
P1 | TOOLING      | apps/widget/tsconfig.json + apps/explorer/tsconfig.json | tsconfigs near-identical; missing strict flags from web/tsconfig.json
P1 | TOOLING      | repo root                                             | No ESLint config in any of the three apps
P1 | TEST         | apps/web/tests/document-review.future.tsx + apps/web/e2e/document-ingestion.future.ts | .future.* placeholders, never landed
```

### 7.3 P2 + P3 (39)

Tracked under one umbrella issue (see §7). Themes: refactor (key={i},
fallback duplication), typing (any casts, abort-error helpers),
perf (debounce dedupe), bundle (no chunking, no budget),
test (axe coverage, e2e gap), a11y (incomplete ARIA roles).

---

## 8. Issues filed

| Issue | Title | Scope |
|---|---|---|
| (filed alongside this doc) | Audit P0 — Split routes.py into 4 sub-routers | Backend P0 #1 |
| | Audit P0 — Extract ReviewService.handle_validation | Backend P0 #2 |
| | Audit P0 — Add SQLite indexes (document_versions.document_id, documents(created_at,id)) | Backend P0 #3 |
| | Audit P0 — Bulk embedding write-back via UNWIND | Backend P0 #4 |
| | Audit P0 — Reverse-index for delete_subgraph_for_version | Backend P0 #5 |
| | Audit P0 — Shared frontend package (apps/_shared) | Frontend P0 #1-5 |
| | Audit P0 — Generate widget/explorer types from OpenAPI | Frontend P1 #1-2 |
| | Audit P0 — Split apps/explorer App.tsx + GraphCanvas | Frontend P0 #3 |
| | Audit P0 — Add Vitest scaffolding to widget + explorer | Frontend P0 #4 |
| | Audit P0 — Add ESLint flat config (react-hooks + jsx-a11y) | Frontend P1 #5 |
| (umbrella) | Audit hygiene tracker — backend P2/P3 | Backend P2/P3 (49) |
| (umbrella) | Audit hygiene tracker — frontend P2/P3 | Frontend P2/P3 (39) |

---

## 9. What this audit does NOT cover

- **Security review.** No threat model exercised. The
  `_sanitize` finding (P1 #38) is a single signal that a real
  security review would surface more.
- **Production load testing.** Performance findings are based on
  static analysis (O(...) reasoning, missing indexes) — none of
  them are validated against measurements.
- **Dependency licence audit.** ADR-013 forbids LangChain;
  ADR-015 caps Voyage SDK to <0.3 because of LangChain
  transitive — but no audit of the full dep tree.
- **Frontend a11y audit beyond manual code reading.** axe-core is
  configured in apps/web only on App.test.tsx; running the full
  suite under axe is a separate task.
- **Customer-facing UX review.** The audit is technical, not
  product.

These are explicit follow-ups for separate audits.

---

*Generated 2026-05-04 by parallel backend+frontend audit agents,
synthesised in this doc. Top 10 priorities each. ~95 backend
findings and 71 frontend findings catalogued.*
