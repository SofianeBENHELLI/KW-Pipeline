# Progress Plan — 2026-05-14

Rolls forward from
[`2026-05-08-progress-plan.md`](2026-05-08-progress-plan.md). Written
against `main` at `fad7262`. Same sprint numbering: S+3 is **the next
sprint**, and it is the same S+3 the 2026-05-08 doc opened — most of
its goals slipped while a different theme (Knowledge Forge UI cutover)
absorbed the cycle.

The goal of this revision is to (a) capture what actually shipped,
(b) re-scope S+3 against what didn't, and (c) thread EPIC-1 taxonomy
in now that the slice issues exist on the tracker.

---

## A. What changed since 2026-05-08

### A.1 What shipped

```
KNOWLEDGE FORGE REDESIGN — cutover                          shipped
─ #414 full preview workbench at /orb*
─ #416 PR-1 foundation (chrome + atoms + /kf stub)
─ #417 PR-2 review workspace skeleton (rail + header + tabs)
─ #418 PR-3 Linked View (flagship cross-highlight)
─ #419 PR-4 FSM card + Review/Pipeline tabs + batch pipeline
─ #420 PR-5 catalog page + system banners
─ #421 PR-6 graph view (toolbar + canvas + inspector)
─ #422 PR-7 search + grounded chat panels
─ #423 PR-8 admin hub + purge dialogs + Settings + cleanup
─ #424 flipped `/` to Knowledge Forge — redesign cutover closed
─ #432 wired icon rail + top-bar nav to react-router
─ #430 merged Review + Pipeline into one "Pipeline & FSM" tab
─ #429 Linked View renders document structure (sections + chunks)
─ #435 manual lifecycle demote — re-open VALIDATED/REJECTED versions

PDF VIEWER (in /kf/review LinkedView)                       shipped
─ #443 bidirectional chunk highlight sync
─ #444 render real PDF in /kf/review LinkedView left pane
─ #445 drop credentials:include so /raw fetch passes CORS
─ #446 fit page width to container + collapse empty side column
─ #447 bidirectional chunk highlight sync in /kf/review
─ #448 resizable + collapsible rail + viewport-fit layout
─ #449 rect highlights render; rail toggle moves onto the rail
─ #450 coverage view + suppress cross-highlight tooltip storm

DOCUMENT TOPIC EXTRACTOR                                    shipped
─ #412 DocumentTopic data model + store + read API (#411 partial)
─ #413 LLM-driven document Topic extractor + projector hook
─ #439 adopt `instructor` for topic extractor structured-output

EXPLORER LARGE-CORPUS UX                                    front-end caught up
─ #397/#398/#400/#401 truncation banners across doc, cluster,
   chunk and concept detail (#321 closed)
─ #399 local-fallback concept search highlights evidence chunk
─ #396 wire topic-click in grouped search to open evidence chunk

OPS / DEPLOY                                                shipped
─ #404 deploy-orbital.sh (Orbital S3 deploy)
─ #403 bake KW_API_BASE_URL into widget+explorer bundle at build time
─ #434 mirror every orbital deploy to /latest/ alias
─ #405/#407 demo + landing pages tracked in repo, auto-updated on deploy
─ #427/#431/#433/#436/#442/#451 demo+landing pointer bumps to
   Knowledge Forge v0.1.1 → v0.1.5

REGRESSIONS / SMALL FIXES                                   shipped
─ #441 three /kf/* operator regressions (FSM buttons hidden,
   graph crash, HITL 403)
─ #428 batch pipeline polls between steps (async-extraction safe)
─ #426 KF shell height min-height: 100dvh
─ #425 widget "open in Orbital" URL via URL API
```

### A.2 What did **not** ship from the 2026-05-08 S+3 list

```
#40 ASYNC QUEUE TAIL                                        not started
─ retry/failure FSM per ADR-006 §4–5
─ queue-depth + retry counters in structured logs
─ reconciliation operator surface (D8 / #124 residual)

#91 SCOPE PREDICATE SWEEP                                   not started
─ workspace-scope predicate still missing on
   GET /documents, GET /knowledge/search,
   POST /knowledge/chat, GET /knowledge/atlas, neighborhood
─ multi-scope merge slice 3 (#327) still open
─ actor.id audit-event backfill still pending

EPIC 4 TRUST GAP                                            not started
─ 4.1 server-side citation validation on /knowledge/chat
─ 4.2 empty-retrieval short-circuit (deterministic "no content")
─ 3.2 embedding cache hit/miss counters

DECISIONS                                                   not taken
─ D3 audit retention + tamper-evidence (still blocks ADR-021)
─ D11 SQLite → Postgres trajectory (still blocks ADR-022)
─ D14 (:Section) vs (:Chunk) deprecation in KG payload v0.3
```

### A.3 New ADRs since 2026-05-08

```
ADR-029  AURA citation contract                              accepted
ADR-030  Companion is external                               accepted
ADR-031  Storage boundary — SQLite vs Neo4j                  accepted
```

ADR-021, ADR-022, ADR-018 still missing.

### A.4 New EPICs filed (parents + slices on the tracker)

- **EPIC 1 — Hybrid + Self-Learning Taxonomy** — parent #336 with
  fifteen sub-issues #338–#352 (1.1 through 1.15). The
  2026-05-04 restructure foreshadowed these; they now exist as
  actionable tickets.
- **EPIC — Chunk-level review pane** — #306 (true HITL gate, sits
  beneath the existing review FSM).
- **EPIC — AURA companion layer** — #373 (Step 6 "From Insight to
  Action"; covered conceptually by ADR-029 + ADR-030).
- **EPIC-D Swym community scoping** — #218 (resolves the
  workspace-unit half of D2 — pairs with #91).
- **EPIC-B External / ITEROP review workflow adapter** — #216.

---

## B. Recommended sprint plan

The sequencing constraint from 2026-05-08 still applies and is now
sharper: **the three deferred items from S+3 are still the cheapest
risk reductions in the codebase**. The Knowledge Forge UI work that
landed this cycle expanded the surface the trust gap and the
multi-tenant scope gap are exposed through, so the penalty for
deferring them again is strictly worse than it was on 2026-05-08.

### Sprint S+3 (next) — close the three deferred items, in scope-only mode

Goal: the chat surface stops being a trust hazard, the workspace
predicate is everywhere, async extraction has retries + an operator
surface. **No new epics opened during S+3.**

1. **EPIC 4 trust gap** — order them small → big:
   - 4.2 empty-retrieval short-circuit on `/knowledge/chat`
     (deterministic "no relevant content" reply; ~20 LOC + 2 tests).
   - 4.1 server-side citation validation on `/knowledge/chat`
     (drop or quarantine answer chunks the retrieval set didn't
     contain; emit `chat.citation.dropped` event; ~120 LOC + 4
     tests). This is the single highest-leverage correctness fix
     in the repo.
   - 3.2 embedding cache hit/miss counters (one-line emit in
     `KnowledgeProjector.project_chunks`; ~10 LOC).
2. **#40 async queue tail** — implement retry/failure FSM per
   ADR-006 §4–5; emit `extraction.retry`, `extraction.dead_letter`,
   `extraction.queue_depth` via the structured-logs vocabulary; ship
   the reconciliation operator surface as HTTP `/admin/reconcile`
   (D8) — the admin viewer pattern from #280 / KF PR-8 (#423) is
   the right shape.
3. **#91 scope predicate sweep** — apply the workspace-scope
   predicate to the remaining list/search/graph routes. Order them
   by blast radius:
   - `GET /documents` (catalog),
   - `GET /knowledge/search`,
   - `POST /knowledge/chat`,
   - `GET /knowledge/atlas`,
   - neighborhood.
   Backfill `actor.id` on audit events behind the same PR. Close
   `#327` (slice 3 — multi-scope merge in list routes).
4. **Architecture review meeting decisions** — make S+4 unblockable:
   take **D3** (audit retention shape), **D11** (Postgres
   trajectory), and **D14** (Section vs Chunk in payload v0.3).
   Write ADR-018 (taxonomy versioning lifecycle) and **draft**
   ADR-021 / ADR-022; final ADR-021 lands in S+4.
5. **Bug closeouts that block the demo loop**:
   - **#440** flaky vitest `KnowledgeGraphView` selection race —
     low cost, high noise reduction.
   - **#363** granular per-aspect document delete (semantic /
     chunks / relations / source) with confirmation — small backend
     + KF Settings dialog; lifts the admin hub from "purge all" to
     "purge what's wrong."

Out-of-scope for S+3 to keep the sprint small enough to actually
close: any new EPIC-1 taxonomy slice, any new EPIC, any 3DX work.

### Sprint S+4 — RAG hardening + EPIC-1 taxonomy bootstrap

Goal: EPIC-1 starts producing visible output on the demo corpus,
and the chat surface gains the retrieval-quality knobs that the
trust gap fix exposes the need for.

1. **ADR-018** (taxonomy versioning lifecycle) + **ADR-021** final
   (audit retention + tamper-evidence). Both small, both blocking.
2. **EPIC-1 backend slices** in order:
   - **#338** 1.1 deterministic taxonomy extractor per chunk.
     Reuses `chunk_relations.py` + `topic_clustering.py` — the
     Topic extractor that landed in #413 / #439 is the precedent
     for the structured-output shape.
   - **#339** 1.2 business taxonomy schema + persistence (depends
     on ADR-017, ADR-018).
   - **#340** 1.3 LLM business-taxonomy allocation per chunk +
     version pinning + prompt traceability.
   - **#341** 1.4 taxonomy gap analysis service.
3. **EPIC-1 frontend slice** **#346** (1.9 taxonomy mode indicator)
   so the backend bootstrap is visible end-to-end through the
   Knowledge Forge shell.
4. **EPIC 4 retrieval quality** once 4.1 has shipped:
   - 4.3 hybrid BM25 + vector retrieval (keyword-heavy queries),
   - 4.5 eval harness (golden Q&A pairs + CI gate on Recall@k /
     MRR).
   Defer 4.4 (rerank) to S+5 — only worth doing once 4.3 + 4.5
   tell us where retrieval is actually wrong.

### Sprint S+5 — production-shape + EPIC-1 completion

Goal: a second consumer could onboard; EPIC-1 reaches the
"validated business taxonomy" state.

1. **ADR-022** final (SQLite → Postgres trajectory, resolves D11)
   + `docs/architecture/deployment_matrix.md` covering the four
   frontends (`apps/web`, `apps/widget`, `apps/explorer`,
   `apps/widget-preview`).
2. **EPIC-1 corpus + completion + validation slices**:
   - **#342** 1.5 corpus-level emerging taxonomy aggregator,
   - **#343** 1.6 "create business taxonomy automatically" action,
   - **#344** 1.7 LLM taxonomy completion / improvement,
   - **#345** 1.8 taxonomy version + validation workflow.
3. **EPIC-1 frontend remainder**: **#347** 1.10 dashboard, **#348**
   1.11 graph view, **#350** 1.13 chunk inspector taxonomy panel.
4. **Operational backbone**:
   - **#94** backup / restore script + runbook,
   - **#96** runtime metrics + readiness probes + ingestion SLAs,
   - **#84** retention / purge policy (unblocked once ADR-021
     final has landed),
   - **#85** malware scanning gate (no-op default + opt-in real
     scanner).
5. **#88** reviewer assignment / locking / comments (depends on
   D4; if D4 is not taken in S+3 architecture review, push to
   S+6).

### Continuous backlog (parallelizable, off-sprint)

- **#229** apps/explorer App.tsx + GraphCanvas split (P0 audit).
  Pure refactor, large diff; assignable to anyone who needs a
  contained side-quest.
- **#437** KF reviewer "Select-all" on the document rail —
  ~30 LOC frontend.
- **#232 / #233** audit hygiene trackers — already scoped as
  P2/P3 sweeps; one item per side-quest.
- **#253** shared SettingsHub presentational components — ~600
  LOC of duplication; pairs with KF Settings panel work.
- **EPIC 7 breadth** (#47 OCR, 7.1 tables, 7.2 XLSX) — keep
  off-sprint until the customer demo corpus actually needs them.

---

## C. Why this order

The 2026-05-08 doc named three "boring" closeouts (#40, #91, EPIC-4
trust gap). The Knowledge Forge UI cutover absorbed the cycle
instead, which was the right call — Knowledge Forge is now the
default route (#424) and the demo loop runs through it — **but the
three closeouts did not move**. They are now strictly more urgent:

- **Chat citation validation (4.1)** — the chat panel is now the
  default landing surface for grounded answers (#422). Every day
  it can fabricate `[chunk_id]` references is a day a demo can
  surface an uncited claim.
- **#91 scope predicate sweep** — every list/search/graph route
  without the scope predicate is a multi-tenant data-leak waiting
  for the first second consumer. The KF catalog (#420) and grounded
  chat (#422) now expose those routes from the default UI.
- **#40 retries + reconciliation** — async extraction is on by
  default, so every silent failure is an operator problem with no
  way to detect it. The KF admin hub (#423) is the natural
  surface for `/admin/reconcile`.

EPIC-1 taxonomy is the visible product story for S+4. Starting it in
S+3 risks the closeouts slipping a third time.

---

## D. Decisions still open

D3 audit retention + tamper-evidence — **target: take in S+3 review**.
D4 reviewer claim model — blocks #88 (push to S+5 if not taken).
D7 first 3DEXPERIENCE container size + auth/context model — still
   external to this team.
D9 duplicate uploads without `document_id` — new family or attach.
D10 customer-facing audience for `/knowledge/chat` — informs whether
   4.1 needs to *strip* uncited answers or *quarantine* them.
D11 SQLite → Postgres production trajectory — **target: take in
   S+3 review**, final ADR-022 in S+5.
D14 `(:Section)` vs `(:Chunk)` deprecation in KG payload v0.3 —
   **target: take in S+3 review**.

---

## E. What this document does *not* fix

- The 3DEXPERIENCE platform-side conversation (D7) — external.
- Product naming across `Knowledge Forge` / `Knowledge Explorer` /
  `Orbital` / `KW Pipeline` — marketing call, not architecture.
- The `apps/explorer` refactor (#229) — kept off-sprint by design.
- The AURA / companion layer (#373) — ADR-029 + ADR-030 framed it
  as external; no implementation work scheduled here.

---

*Generated 2026-05-14, rolling forward from
`2026-05-08-progress-plan.md`. Next review: after Sprint S+3 closes
and ADR-018 / draft ADR-021 / draft ADR-022 land.*
