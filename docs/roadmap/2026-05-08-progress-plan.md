# Progress Plan — 2026-05-08

A pragmatic next-three-sprints plan, written against the state of
`main` at `d51ed06` and rolling forward from
[`2026-05-04-backlog-restructure.md`](2026-05-04-backlog-restructure.md).

The restructure doc framed sprints S+1 through S+6. Most of S+1 and
parts of S+2 have shipped since it was written. This doc updates the
picture and proposes the next three sprints.

---

## A. What changed since 2026-05-04

### A.1 In-flight work that landed on `main`

```
ASYNC EXTRACTION QUEUE (#40 / ADR-006)                       partial
─ ADR-006 written and accepted (commit d43b8f4)
─ PR-1: extraction worker harness behind KW_EXTRACTION_INLINE (#309)
─ PR-2: 202 Accepted + QUEUED_FOR_EXTRACTION FSM (#329)
─ PR-3: inline default flipped to false (#330)
   remaining: retry/failure FSM, queue-depth + retry counters,
              reconciliation operator surface (D8 / #124 residual)

AUTH + IDENTITY (#83)                                        slice 3 closed
─ #324 closed slice-3 auth gaps on per-version content reads
   remaining: actor.id backfill on audit events,
              identity propagation to remaining unscoped routes

WORKSPACE SCOPING (#91)                                      slices 1–2
─ #325 GET /documents/{id}/scopes (slice 1)
─ #333 scope filter on GET /knowledge/graph (slice 2, ADR-020 §2)
   remaining: scope predicate on /documents,
              /knowledge/{search,chat,atlas}, neighborhood

EXPLORER LARGE-CORPUS (ADR-028)                              backend complete
─ #328 graph relevance, bridge & outlier scoring policy (#314)
─ #331 relation explanation + evidence API (#311)
─ #332 focused knowledge-neighborhood API (#310)
─ #334 multi-kind Explorer search route (#313)
─ #335 corpus atlas summary route GET /knowledge/atlas (#312)
   remaining: apps/explorer UI must consume these routes

LLM PROVIDER BREADTH                                         shipped
─ #276 Gemini provider behind LLMClient Protocol (ADR-013 §6)
─ #295 chat 503 remediation accepts Gemini OR Anthropic key

HITL ROUTING + SPC SAMPLING (#215, ADR-023)                  shipped
─ Slices 2 + 3 + drift detector + admin dashboard

ARCHIVE / PURGE ADMIN (ADR-027)                              shipped
─ #269 / #273 / #274 / #277 / #279 / #280 admin surface
```

### A.2 ADRs written since the restructure

```
ADR-006  Async extraction queue + retry/failure policy        accepted
ADR-017  Taxonomy and ontology                                accepted
ADR-019  Authentication and authorization                     accepted
ADR-020  Workspace scoping                                    accepted
ADR-023  HITL routing + SPC sampling                          accepted
ADR-025  Document similarity and supersede                    accepted
ADR-026  SwYM membership integration                          accepted
ADR-027  Archive / purge admin tool                           accepted
ADR-028  KW Explorer large-corpus UX                          accepted
```

This resolves D1, D2, D5, D12, and D13 from the restructure doc.

### A.3 Decisions still open

D3 audit retention + tamper-evidence (still blocks ADR-021)
D4 reviewer claim model (still blocks #88)
D7 first 3DEXPERIENCE container size + auth/context model
D9 duplicate uploads without `document_id` — new family or attach
D10 customer-facing audience for `/knowledge/chat`
D11 SQLite → Postgres production trajectory (still blocks ADR-022)
D14 `(:Section)` vs `(:Chunk)` deprecation in KG payload v0.3

---

## B. Recommended sprint plan

The hard sequencing constraint is the same as in the restructure
doc: **finish the in-flight epics before opening new ones**. Three
epics are mid-flight, not one — closing them out is the cheapest way
to move forward.

### Sprint S+3 (next) — close the in-flight epics

Goal: #40, #91, and ADR-028 stop appearing in handovers as "partial".

1. **#40 async queue tail** — implement retry/failure FSM per
   ADR-006 §4–5; emit queue-depth + retry counters via the
   structured-logs vocabulary; ship the reconciliation operator
   surface (D8 / #124 residual). Pick HTTP `/admin/reconcile` —
   the admin viewer pattern from #280 is already the right shape.
2. **#91 scope predicate sweep** — apply the workspace-scope
   predicate to the remaining list/search routes that don't have
   it yet: `GET /documents`, `GET /knowledge/search`, `POST
   /knowledge/chat`, `GET /knowledge/atlas`, neighborhood. Backfill
   `actor.id` on audit events now that #83 propagates identity to
   the request scope.
3. **Explorer UI consumes ADR-028 routes** — `apps/explorer` reads
   atlas / neighborhood / evidence / scoring. Pure frontend work,
   parallelizable with #1 and #2.

Decisions to take in the architecture review meeting this sprint:
**D3** (audit retention shape), **D11** (Postgres trajectory), **D14**
(Section vs Chunk in payload v0.3). All three unblock ADRs sitting
on the runway.

### Sprint S+4 — RAG hardening + taxonomy bootstrap

Goal: the chat surface stops being a trust hazard, and EPIC 1
starts producing visible output on the demo corpus.

1. **EPIC 4 trust gap** — the highest-leverage correctness items:
   - 4.1 server-side citation validation on `/knowledge/chat` (today
     the model can emit a `[chunk_id]` it never saw);
   - 4.2 empty-retrieval short-circuit (deterministic "no relevant
     content" reply);
   - 3.2 embedding cache hit/miss counters (one-line emit in
     `KnowledgeProjector.project_chunks`).
2. **Write ADR-018** (taxonomy versioning lifecycle) and **ADR-021**
   (audit retention + tamper-evidence). Both are small and blocking.
3. Begin EPIC 1 backend slices: 1.1 deterministic taxonomy extractor,
   1.2 business taxonomy schema + persistence, 1.3 LLM allocation,
   1.4 gap analysis.
4. EPIC 1 frontend slice 1.9 (taxonomy mode indicator) so the
   backend work is visible end-to-end.

### Sprint S+5 — production-shape

Goal: the operational posture that would let a second consumer
onboard.

1. **Write ADR-022** (SQLite → Postgres trajectory; resolves D11)
   and a `docs/architecture/deployment_matrix.md` covering the four
   frontends (`apps/web`, `apps/widget`, `apps/explorer`,
   `apps/widget-preview`).
2. #94 backup / restore script + runbook.
3. #96 runtime metrics + readiness probes + ingestion SLAs (depends
   on the structured logs and audit store, both already shipped).
4. #84 retention / purge policy (now unblocked once ADR-021 lands).
5. #85 malware scanning gate (no-op default + opt-in real scanner).

### Continuous backlog

- EPIC 1 frontend slices 1.10, 1.11, 1.13, 1.14 ladder up after
  the backend bootstrap in S+4.
- EPIC 4 items 4.3 (BM25 hybrid), 4.4 (rerank), 4.5 (eval harness)
  ladder up once D10 (customer-facing audience) is taken.
- EPIC 8 quality/DX items run as side-quests.
- EPIC 7 breadth (#47 OCR, 7.1 tables, 7.2 XLSX) when the customer
  demo corpus needs them.

---

## C. Why this order

The temptation is to start EPIC 1 (taxonomy) immediately because
ADR-017 just landed and it is the visible product story. The boring
choice — finish the three in-flight epics first, then patch the
chat-citation trust gap — protects everything that ships afterward.

Concretely:

- **#40 retries** — until the retry FSM and reconciliation surface
  exist, every async-extraction failure is a silent operator
  problem. Owners cannot tell whether a stuck document is queued,
  retrying, or dead.
- **#91 scope sweep** — every list/search/graph route without the
  scope predicate is a multi-tenant data-leak waiting for the first
  second consumer.
- **Chat citation validation (4.1)** — the chat surface today can
  fabricate `[chunk_id]` references the LLM never saw. This is the
  single highest-leverage correctness fix in the codebase.

Each of these takes days, not weeks. Doing them first means EPIC 1
ships on a foundation that doesn't need to be revisited.

---

## D. What this document does *not* fix

The restructure doc's §G list still applies: the 3DEXPERIENCE
platform-side decisions, D10 (chat audience), product naming, and
the deployment-matrix gap are unresolved. ADR-022 + a deployment
matrix in S+5 closes the last of those internally.

---

*Generated 2026-05-08, rolling forward from
`2026-05-04-backlog-restructure.md`. Next review: after Sprint
S+3 closes and ADR-018 / ADR-021 land.*
