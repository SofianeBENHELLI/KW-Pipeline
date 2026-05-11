<!-- $PublishToSwym{ "parent": "./Home.md" }$ -->

# Orbital — Feature Catalog

This page is a **user-facing inventory of every feature surfaced by Orbital**, the React 19 + Vite + TypeScript reviewer/admin workbench at [`apps/web/`](https://github.com/SofianeBENHELLI/KW-Pipeline/tree/main/apps/web). Each entry pairs the **user value** (what the reviewer can do) with the **technical detail** (which component, which endpoint, which env gate).

All paths below are relative to [`apps/web/src/`](https://github.com/SofianeBENHELLI/KW-Pipeline/tree/main/apps/web/src). Every API call goes through `openapi-fetch` wrapped by `withRetry` from [`apps/_shared/api-core`](https://github.com/SofianeBENHELLI/KW-Pipeline/tree/main/apps/_shared/api-core) — idempotent GETs retry on transient 5xx; mutations never retry.

## A. Document catalog & navigation

### A1 · Document list with saved views

- **User value:** See every document in the corpus on the left rail, filtered to one of four saved views — **Recent**, **Review**, **Validated**, **Failed** — or search by filename substring.
- **Technical:** `features/pipeline/PipelineWidget.tsx`. Hits `GET /documents` with `status[]` + `q` params. Cursor-paginated, `limit=50`. State managed via `useDocumentCatalog()` hook with request dedup.

### A2 · Document deep-linking

- **User value:** Share a URL like `/?document=<id>` and the reviewer lands directly on that document's workspace with the row auto-scrolled into view.
- **Technical:** `App.tsx:267–311`. URL param read on mount, stripped via `history.replaceState`, scroll-into-view triggered by a token to avoid double-scroll. Shows `DeepLinkErrorBanner` (`App.tsx:799–818`) if the ID doesn't resolve.

### A3 · Status badge per version

- **User value:** At-a-glance lifecycle state on every row: STORED / EXTRACTING / EXTRACTED / SEMANTIC_READY / NEEDS_REVIEW / VALIDATED / REJECTED / FAILED / DUPLICATE_DETECTED. Color-coded.
- **Technical:** `ui/StatusBadge.tsx`. Maps the `DocumentVersionStatus` enum to a Tailwind class set.

### A4 · Scope chip (personal / community / project)

- **User value:** Knows immediately who can see a document.
- **Technical:** `ui/ScopeChip.tsx`. Reads `documentScopes()` from the domain helper; renders one chip per scope link.

## B. Review workspace (the daily-driver screen)

### B5 · Document detail panel

- **User value:** One pane shows everything about the selected document: ID, filename, version count, scope, current status, latest version metadata.
- **Technical:** `features/review/ReviewWorkspace.tsx`. Fires `GET /documents/{id}` + `GET /documents/{id}/versions/{vid}/extraction` + `GET /documents/{id}/versions/{vid}/semantic` in parallel. `AbortController` cancels in-flight fetches when the user switches documents.

### B6 · Raw extraction viewer

- **User value:** Inspect what the parser saw — useful when an extraction looks suspicious before moving to semantic.
- **Technical:** Renders `ApiRawExtraction` as a `<pre>` block. Only shown when the version has reached `EXTRACTED` or later.

### B7 · Markdown preview

- **User value:** See the generated semantic Markdown rendered before validating. Hard-stop sanity check.
- **Technical:** Reads `semantic?.markdown` from `ApiSemanticDocument`. Currently rendered as plain text in `<pre>`; switching to true Markdown rendering would be a single-component swap.

### B8 · Reviewer note input

- **User value:** Add free-text context when validating or rejecting — captured in the audit trail.
- **Technical:** Controlled `<textarea>`; value sent as `reviewer_note` in the validate/reject POST bodies.

### B9 · FSM action buttons (Extract / Semantic / Validate / Reject)

- **User value:** Drive the version through its lifecycle without leaving the screen. Buttons enable/disable based on current status; disabled buttons explain why via tooltip.
- **Technical:** `features/review/ReviewActions.tsx`. Endpoints: `POST /documents/{id}/versions/{vid}/extract`, `…/semantic-extract`, `…/validate`, `…/reject`. In-flight action dedup via an `inFlightActionsRef: Set<string>` so a double-click can't double-submit.

### B10 · Projection-status pill

- **User value:** Knows whether the knowledge graph for this version is being built / done / failed without refreshing.
- **Technical:** `features/review/ProjectionStatusPill.tsx`. Polls `GET /knowledge/projection_status/{vid}` via `useProjectionStatus()`. Stops on COMPLETED/FAILED. Driven by `lastMutationAt` so it restarts after every validate. Off by default in tests.

## C. Batch operations (the "process 50 docs at once" workflow)

### C11 · Row checkboxes + sticky failed selection

- **User value:** Select N documents to run a pipeline pass on them. Failed ones stay checked after the run so you can retry without re-selecting.
- **Technical:** `selectedBatchIds: Set<string>` in App state. After a batch run, only succeeded IDs are cleared; failures remain in the set.

### C12 · "Run selected pipeline" batch action

- **User value:** One click runs Extract → Semantic on every selected document, sequentially, with live progress.
- **Technical:** `App.tsx:553–661` `handleRunBatchPipeline`. Calls `extractVersion()` then `generateSemantic()` per doc. Progress tracked in a `Map<doc_id, {status, reason?}>`. Refresh done once at the end via `Promise.all([refreshSelected, refreshAll])`.

### C13 · Per-row progress pill

- **User value:** Watch the queue advance in real time — queued → extracting → semantic → done/failed.
- **Technical:** Reads `batchProgress` map; renders color-coded pill in `PipelineWidget.tsx`.

### C14 · Batch failure report

- **User value:** After a batch, see a structured list of which docs failed and why, instead of scrolling rows looking for red badges.
- **Technical:** `BatchFailure[]` aggregated during the run; rendered as a dismissible alert. Survives until selection cleared or new run started.

## D. Knowledge graph viewer

### D15 · Interactive Neo4j graph canvas

- **User value:** Explore the document's knowledge structure as a force-directed graph. Hover to see metadata, click to inspect.
- **Technical:** `features/graph/KnowledgeGraphView.tsx` using `<InteractiveNvlWrapper>` from `@neo4j-nvl/react`. Hits `GET /documents/{id}/graph`. Six node kinds (Document, Version, Section, Chunk, Topic, Entity) with deterministic colors; eight edge kinds (PART_OF, HAS_VERSION, HAS_CHUNK, BELONGS_TO, RELATED_TO, SHARES_KEYWORD, SAME_TOPIC_AS, HAS_ENTITY).

### D16 · Six-mode filter toolbar (All / Chunks / Topics / Entities / Relations / Source-backed)

- **User value:** Lens the graph by what you care about — see only the topic clusters, only the entities and their citations, only chunks with verified source spans, etc.
- **Technical:** Filter modes are pure-function projections of the response via `filterProjection()` (`features/graph/types.ts`). "Source-backed" filters by `source_reference_id` or `source_reference_count > 0`. Recomputed in `useMemo` on mode change — no API hit.

### D17 · Node inspector side panel

- **User value:** Click a node, see its heading, keywords, topic membership, score, and (for entities) reason + shared keywords. The audit trail for that node.
- **Technical:** Selection state is `{ kind: "node" | "edge"; id: string } | null`. Payload mapping via `toNvlNodes()` / `toNvlRelationships()` helpers; NVL canvas mock-friendly for jsdom tests.

### D18 · Auto-refresh after validate

- **User value:** Validate a document and the graph repopulates itself when projection completes — no manual reload.
- **Technical:** `useProjectionStatus()` keyed on `lastMutationAt`. Polls on a 30s interval (configurable, default off in tests).

## E. Phase 3 — Vector search & chat

### E19 · Vector search panel

- **User value:** Slide-out panel: type a natural-language query, get top-10 chunks ranked by semantic similarity across the whole corpus.
- **Technical:** `features/search/SearchPanel.tsx`. `GET /knowledge/search?q=…&limit=10`. 300 ms debounce (`SEARCH_DEBOUNCE_MS`); `AbortController` cancels stale in-flight requests. Renders an explicit remediation when the backend returns `KW_VECTOR_SEARCH_DISABLED` so the reviewer knows it's a config issue, not a bug.
- **Gated by:** `VOYAGE_API_KEY` set on the backend.

### E20 · Grounded RAG chat panel

- **User value:** Ask a question, get an answer cited to specific chunks. Click a citation to jump to the source document.
- **Technical:** `features/chat/ChatPanel.tsx`. `POST /knowledge/chat` with `{ question, mode, top_k }`. Returns `{ answer, citations: [{ document_id, version_id, chunk_id, snippet }] }`. Renders remediation on `KW_CHAT_DISABLED` (which includes env-var hints the reviewer can hand to ops).
- **Gated by:** `VOYAGE_API_KEY` + (`ANTHROPIC_API_KEY` or `GEMINI_API_KEY`).

### E21 · Chat mode toggle (RAG / GraphRAG / Hybrid)

- **User value:** Switch retrieval strategies live to compare quality. RAG = vector-only; GraphRAG = graph traversal; Hybrid = both.
- **Technical:** `features/chat/ChatModeToggle.tsx`. Mode enum `ApiChatMode`; passed through to the POST body.

## F. Admin surfaces (only sanctioned hard-delete lives here)

### F22 · Admin hub

- **User value:** `/admin` route. One page, four cards: Archive, HITL, Audit, Config.
- **Technical:** `features/admin/AdminHubView.tsx`. Lazy-loaded chunk (only fetched when route entered). No client-side role check — the backend gates each route with a 403 on non-admin.

### F23 · Archive viewer

- **User value:** See every archived (soft-removed) document. Per row: Unarchive (bring back), Relink scope (attach to a new scope when the original was removed), Purge artifacts (delete bytes but keep audit trail).
- **Technical:** `features/admin/AdminArchiveView.tsx`. `GET /admin/archive/archived_documents` (cursor-paginated, sorted by `archived_at DESC`). Modal flows for relink and bulk purge. Purge is dry-run-first: shows byte estimate before confirm.

### F24 · HITL routing dashboard

- **User value:** Operator-facing console for the auto-validation system. Shows: scorer state, auto-validate threshold, queue depth, per-bucket drift ratios (sorted hottest first), one-click "run auto-promote pass" button.
- **Technical:** `features/admin/AdminHITLView.tsx`. `GET /admin/hitl/state` + `POST /admin/hitl/run_auto_promote_pass`. 30s auto-refresh (toggleable, off in tests). Renders remediation on `KW_HITL_DISABLED`.

### F25 · Audit log viewer

- **User value:** Forensics screen. Filter every system event by name/actor/time, click any row to expand the full structured JSON payload.
- **Technical:** `features/admin/AdminAuditView.tsx`. `GET /admin/audit/events` cursor-paginated, default 50/page (max 200). Filter bar populated from `available_event_names` returned with the response. Click-to-expand renders the payload as pretty-printed JSON.

## G. Destructive operations (typed-confirmation gates)

### G26 · Single-document purge dialog

- **User value:** Permanently delete one document. Must type the exact original filename to confirm — prevents misclicks. Cascades to archive + bytes + extractions + semantic JSON + Markdown + KG subgraph + audit event.
- **Technical:** `features/purge/PurgeDialog.tsx`. `POST /admin/orbital/purge_document` with the typed confirmation string. Modal summary shows version count + audit event name before the operator commits.

### G27 · Bulk "purge everything" dialog

- **User value:** The nuclear option for resetting a corpus. Operator must type a secret phrase verbatim; backend re-checks the phrase on the wire (422 on mismatch).
- **Technical:** `features/purge/PurgeAllDialog.tsx`. Phrase constant `ORBITAL_PURGE_ALL_PHRASE`. `POST /admin/orbital/purge_all?confirm=true`. Modal copy says "irreversible" verbatim; confirm button stays disabled until phrase matches exactly.

## H. Settings & status

### H28 · Settings modal

- **User value:** Diagnostics in one place: backend version, health of Neo4j / SQLite / extraction worker, which Phase-3 features are on, plus a one-click "load demo dataset" and "reset corpus" for presenters.
- **Technical:** `features/settings/SettingsModal.tsx`. Lazy-loaded. Reads `AdminConfigResponse` via the shared `settings-hub` package. Status tiles: ok / off / warn. API base URL is read-only here because it's baked at build time via `VITE_API_BASE_URL`.

### H29 · Forced-auto-corpus banner

- **User value:** Non-dismissible warning when `KW_HITL_FORCE_AUTO_CORPUS=true` — the operator can't miss that every version is being auto-validated regardless of confidence.
- **Technical:** `ui/ForceAutoCorpusBanner.tsx`. Visibility derived from `forceAutoActive` in `/admin/config`. Stacks above the session-expiry banner.

### H30 · Session-expired banner

- **User value:** When the user's session lapses (any API call returns 401), a banner appears with "Sign in again" — no broken UI mid-action.
- **Technical:** `apps/_shared/auth/SessionExpiredBanner.tsx`. Module-level trigger hook fired by any `ApiError(401)`. Sign-in handler reloads the page; in `KW_AUTH_MODE=bearer` mode that bounces through the IdP. Dev test hook: `#force-session-expired` URL hash.

## I. Feature flags & env gates

| Feature | Gated by (backend env) | Frontend behavior when off |
|---|---|---|
| Knowledge graph | `KW_KNOWLEDGE_LAYER_ENABLED=true` | Graph panel hidden |
| Vector search | `VOYAGE_API_KEY` set | Search panel shows `KW_VECTOR_SEARCH_DISABLED` remediation |
| Chat | `VOYAGE_API_KEY` + LLM key | Chat panel shows `KW_CHAT_DISABLED` remediation with env-var hints |
| HITL dashboard | `KW_HITL_ENABLE_SCORER=true` | Admin HITL card shows "disabled" with remediation |
| Audit log | `KW_AUDIT_ENABLED=true` | Admin Audit card shows `KW_AUDIT_DISABLED` remediation |
| Force-auto banner | `KW_HITL_FORCE_AUTO_CORPUS=true` | Banner visible across the entire app |
| Projection polling | `KW_KNOWLEDGE_PROJECTION_ASYNC=true` | Pill skips polling; assumes inline projection |
| Auth | `KW_AUTH_MODE=dev\|bearer` | Dev mode auto-signs-in a fixed user; bearer mode bounces through IdP |
| API base URL | `VITE_API_BASE_URL` (build-time) | Defaults to `http://localhost:8000` |
