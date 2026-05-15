# Backlog Restructure & Status — 2026-05-04

This document is a single-pass restructure of the entire KW Pipeline
backlog. It rolls together:

- the closed-issue history (90 closed),
- the open backlog (21 open at 2026-05-04 17:00 UTC, including the
  freshly-filed taxonomy specs #210 / #211),
- the 13 ADRs in `docs/adr/`,
- the architecture docs in `docs/architecture/`,
- the latest two handovers (`2026-05-04-phase-2-closure.md` and
  `2026-05-04-audit-followups.md`),
- the long-term phase ladder in `long_term_vision.md`.

Goal: produce one place where *what is done*, *what is open*, *what
is missing*, and *what needs a decision* are all aligned, and where
the open issues are organised into coherent epics with explicit
dependencies.

---

## A. One-page status

### A.1 What is done on `main`

```
INGEST + REVIEW (Phase 0/1 of the product)        ✅ shipped
─ Document catalog, SHA-256 dedup, FSM, cursor pagination, idempotency
─ Parsers: TXT, PDF (pdfplumber), DOCX, PPTX
─ Semantic JSON contract + schema-version migration policy (ADR-008)
─ Markdown generator with YAML frontmatter
─ Validate / reject endpoints
─ Persisted SQLite catalog + filesystem artifacts + migration system
─ OpenAPI codegen → typed openapi-fetch client (ADR-011)
─ Structured JSON logs + event vocabulary
─ Persisted audit events (audit_event_store.py shipped 2026-05-04, #206)
─ Custom error envelope + handlers (#97/#120)
─ Static type checking, ruff, pre-commit, gitleaks

KNOWLEDGE LAYER PHASE 1 — graph projection                  ✅ shipped
─ GraphStore Protocol + Neo4jGraphStore + InMemoryGraphStore
─ KnowledgeProjector with stages (project_document_structure,
  project_chunks, project_chunk_relations, project_topics,
  project_entities)  (ADR-012)
─ Deterministic chunk relations + topic clustering (no LLM/Neo4j needed)
─ Graph endpoints: GET /documents/{id}/graph, GET /knowledge/graph
─ Frontend KnowledgeGraphView with NVL, lazy-split bundle

KNOWLEDGE LAYER PHASE 2 — entity extraction                 ✅ shipped
─ LLMClient Protocol + Anthropic adapter, no LangChain (ADR-013)
─ EntityExtractor with citations, prompt cache, retry, token cap (ADR-014)
─ spaCy NER opt-in enricher (#190)
─ Section-batching knob (#195)

KNOWLEDGE LAYER PHASE 3 — vector RAG + chat               🟡 in flight
─ Voyage embedding provider scaffold + FakeEmbeddingClient (ADR-015)
─ Chat skeleton: KnowledgeChatService + POST /knowledge/chat
  (ADR-016, single route, mode is body)
─ ChatPanel + ChatModeToggle in apps/web
─ SearchPanel UI on apps/web (#199)
─ Knowledge Explorer widget read-only surface (#207, 2026-05-04)
─ Demo dashboard, demo launchers, customer-demo smoke runner

DEMO POSTURE                                                ✅ shipped
─ docker-compose with Neo4j 5.23 Community + integration CI
─ Customer demo fixtures producing 3+ topics, 8+ chunk relations
```

### A.2 What is in flight or imminent

```
PHASE 3 close-out                                          owner: backend
─ Provision Neo4j HNSW vector index on (:Chunk {embedding})
─ Embedding write-path in KnowledgeProjector.project_chunks
─ Implement GET /knowledge/search (#186)
─ Server-side citation validation on chat answer (trust gap)
─ Empty-retrieval short-circuit on /knowledge/chat
─ Embedding cache hit/miss counters

WIDGET CHAT PANEL                                          owner: frontend
─ Mirror apps/web ChatPanel into apps/widget
─ Closes "Phase 3 reachable everywhere"
```

### A.3 What is open and material — by EPIC (see §C for full mapping)

```
EPIC 1  Hybrid + Self-Learning Taxonomy        7 issues to create from #210/#211 + #22
EPIC 2  Governance / Auth / Multi-tenant       #83  #91  #88  +  ADR needed
EPIC 3  Operational maturity                   #40  #94  #96  #84  #85  #92  +  ADR-006 (async queue)
EPIC 4  RAG / chat hardening                   citations, hybrid retrieval, rerank, eval
EPIC 5  Knowledge handoff / export             #22  #23  #90  +  entity canonicalization
EPIC 6  3DEXPERIENCE integration               #78  #89  #95
EPIC 7  Document intelligence breadth          #47  +  tables / XLSX / HTML / EML
EPIC 8  Quality / DX                           #24  #66
EPIC 9  Bug fixes                              #59 (duplicate uploads → wrong family)
EPIC 10 Production-shape decisions             SQLite→Postgres path, deployment matrix
```

### A.4 Decisions still open (blocks work below)

| # | Decision | Blocks | Owner |
|---|---|---|---|
| D1 | Auth model: 3DX SSO vs API tokens vs OIDC | EPIC 2 | product + arch |
| D2 | Workspace boundary unit (project / 3DX collab space / tenant) | EPIC 2 | product |
| D3 | Audit-event retention + tamper-evidence | EPIC 2, EPIC 3 | arch |
| D4 | Reviewer claim model: optimistic vs pessimistic | EPIC 2 (#88) | product |
| D5 | Async queue technology (SQLite / Redis / NATS / Postgres-as-queue) | EPIC 3 (#40) | arch |
| D6 | Chat answer surface: customer-facing or reviewer-only | EPIC 4 | product |
| D7 | First 3DEXPERIENCE container size + auth/context model | EPIC 6 (#78) | product + 3DX |
| D8 | Reconciliation surface: HTTP route vs CLI | #124 residual | arch |
| D9 | Duplicate uploads w/o `document_id` — new family or attach? | #59 | product |
| D10 | Customer-facing audience for /knowledge/chat | EPIC 4 | product |
| D11 | Persistence trajectory: SQLite → Postgres production path | EPIC 10 | arch |
| D12 | Taxonomy persistence layer (SQLite vs Neo4j vs both) | EPIC 1 | arch (new) |
| D13 | Taxonomy LLM provider strategy (reuse Anthropic? caching?) | EPIC 1 | arch (new) |
| D14 | `(:Section)` vs `(:Chunk)` deprecation in KG payload v0.3 | EPIC 5, EPIC 1 | arch |

---

## B. Epic graph and dependencies

```text
                    ┌─────────────────────────────────────┐
                    │  EPIC 2  Governance / Auth          │
                    │  D1, D2, D3, D4                     │
                    │  #83  #91  #88  #92                 │
                    └──────────┬──────────────────────────┘
                               │ (auth identity)
                               │
        ┌──────────────────────┼─────────────────────────────────┐
        ▼                      ▼                                 ▼
 EPIC 4 RAG hardening   EPIC 1 Taxonomy             EPIC 6 3DEXPERIENCE
 citation validation    #210 / #211 split           #78 #89 #95
 hybrid retrieval       D12, D13                    D7
 rerank / eval                                       │
        │                       │                    │
        │                       │                    │
        ▼                       ▼                    ▼
 EPIC 5 Knowledge handoff
 #22  #23  #90  entity canonicalization
        │
        ▼
 EPIC 3 Ops maturity
 #40 (D5)  #84  #94  #96  #85  +  ADR-006

 EPIC 7 Document intelligence breadth     EPIC 8 Quality / DX     EPIC 9 Bugs
 #47  tables  XLSX  HTML  EML             #24  #66                #59
        independent                       independent             D9 first

 EPIC 10 Production-shape (SQLite→Postgres, deployment matrix)
        large blast radius — needs ADR before EPIC 2 ships
```

The hard sequencing constraint is **EPIC 2 first**, because:

- Reviewer assignment (#88) needs `actor identity`.
- Workspace scoping (#91) is a query predicate on every list/search/graph endpoint.
- Audit events without `actor.id` are weaker than they look.

Everything else has at most two-layer dependencies.

---

## C. Issue-by-issue restructuring

Notation: `KEEP` (issue is right-sized as-is), `SPLIT` (break into
smaller issues), `MERGE` (fold into another issue), `SUPERSEDE`
(replace with a new EPIC + smaller issues), `RENAME` (clarify
title/scope), `CLOSE` (covered by something already shipped or out
of scope).

### EPIC 1 — Hybrid + Self-Learning Taxonomy (NEW)

`#210 Self-Learning Taxonomy for Knowledge Explorer` and
`#211 Hybrid Taxonomy Model for Knowledge Explorer` as filed are
both 200+-line specs that overlap heavily. They cover: deterministic
extraction, LLM-based business allocation, gap analysis, emerging
taxonomy aggregation, LLM completion, versioning, UI dashboards,
graph view, comparison, validation workflow.

They also overlap with **#22 (canonical knowledge-asset taxonomy)**
which was filed earlier with a different but adjacent meaning
(typed asset shapes, not domain-content classification).

**Verdict:** SUPERSEDE both with one EPIC and split into the slices
below. Keep #210 and #211 open as the canonical specs (link them as
parents) but stop using them as actionable tickets.

| New issue | Title | Depends on | Effort |
|---|---|---|---|
| EPIC-1.0 | EPIC — Hybrid + Self-Learning Taxonomy (parent, references #210 #211) | — | tracking |
| ADR-017 | ADR-017 — Hybrid taxonomy data model (3 layers; never collapsed) | D12, D13 | S |
| ADR-018 | ADR-018 — Taxonomy versioning lifecycle (Draft / V0 / V1 / Validated / Archived) | ADR-017 | S |
| 1.1 | Backend — Deterministic taxonomy extractor per chunk (keywords, NER, noun phrases, acronyms, headings) | reuses chunk_relations.py + topic_clustering.py | M |
| 1.2 | Backend — Business taxonomy schema + persistence (classes, subclasses, synonyms, version) | ADR-017, ADR-018 | M |
| 1.3 | Backend — LLM business taxonomy allocation per chunk + version pinning + prompt traceability | 1.1, 1.2 | M |
| 1.4 | Backend — Taxonomy gap analysis service (overlap, missing, weak, ambiguous, drift, redundant) | 1.1, 1.3 | M |
| 1.5 | Backend — Corpus-level emerging taxonomy aggregator (extends topic_clustering) | 1.1 | M |
| 1.6 | Backend — "Create business taxonomy automatically" action (LLM-driven from emerging) | 1.5 | M |
| 1.7 | Backend — LLM taxonomy completion / improvement suggestions | 1.5 | M |
| 1.8 | Backend — Taxonomy version + validation workflow (new/under_review/accepted/rejected/merged/deferred) | ADR-018 | M |
| 1.9 | Frontend — Taxonomy mode indicator (no taxonomy / self-learning / candidate / validated / update available) | 1.2, 1.8 | S |
| 1.10 | Frontend — Taxonomy dashboard (counts, coverage, orphans, low-confidence) | 1.4, 1.5 | M |
| 1.11 | Frontend — Taxonomy graph view (extension of KnowledgeGraphView with class/subclass/concept/chunk roles + colour coding) | 1.5 | M |
| 1.12 | Frontend — Compare taxonomy versions (V0/V1/V2/inferred-vs-business) | 1.8 | M |
| 1.13 | Frontend — Chunk inspector taxonomy panel (deterministic + business + gap + suggestions) | 1.4 | S |
| 1.14 | Frontend — Reviewer actions on taxonomy suggestions (accept/reject/rename/merge/alias) | 1.7, 1.8 | S |
| 1.15 | Export to RDF / SKOS / JSON-LD (Could-have, ladder up) | 1.8 | M |

**Aligns with #22 (canonical knowledge-asset taxonomy):** RENAME #22
to **"Canonical knowledge-asset *type* taxonomy"** to clarify it is
the schema for *what an extracted assertion is* (concept, business
rule, decision, risk, action item, contradiction…), not domain
content. EPIC 1 is the *content classification* layer; #22 is
orthogonal and feeds 1.1 with the asset typing vocabulary.

### EPIC 2 — Governance / Auth / Multi-tenant

| Existing | Verdict | Notes |
|---|---|---|
| #83 Auth + 3DX user context | KEEP, blocked on D1 | Drives every other governance issue. |
| #91 Workspace / project scoping | KEEP, blocked on D1, D2 | Add scope predicate to every list/search/graph route. |
| #88 Reviewer assignment / locking / comments | KEEP, blocked on D4 | Needs the auth identity to be useful. |
| #92 Sensitive data detection / redaction | KEEP, can run in parallel | Pluggable detector; deterministic MVP. |
| #84 Retention / purge policy | KEEP, blocked on D3 | Soft delete vs hard purge. |

**Missing items to file under EPIC 2:**

- **2.A — ADR-019 — Auth model and identity propagation** (resolves D1).
- **2.B — ADR-020 — Workspace scoping unit and predicate** (resolves D2).
- **2.C — ADR-021 — Audit retention, query surface, and tamper-evidence** (resolves D3, plus closes the audit-followups "audit retention" gap; observability.md flags this as out of scope today).
- **2.D — Backend — Reviewer assignment FSM, lock/release, comment thread** (carve out from #88 once D4 lands).

### EPIC 3 — Operational maturity

| Existing | Verdict | Notes |
|---|---|---|
| #40 Async background extraction queue | SPLIT, blocked on D5 | The current `extraction_job_service.py` runs inline. Split into 3.1 ADR-006 (decide queue tech), 3.2 worker harness, 3.3 SSE/long-poll feedback (optional). |
| #94 Backup / restore / DR | KEEP | Local script + restore validation + production direction. |
| #96 Runtime metrics + readiness probes + SLAs | KEEP | Depends on logs from #42 (shipped) + audit store (shipped). |
| #84 Retention / purge | belongs to EPIC 2 | (already listed above) |
| #85 Malware scanning | KEEP | Pluggable scanner + no-op dev impl + persisted quarantine state. |
| #92 Sensitive-data detection | belongs to EPIC 2 | (already listed above) |
| #59 Duplicate uploads → new family | EPIC 9 | Bug, separated below. |

**Missing items to file under EPIC 3:**

- **3.1 — ADR-006 — Async queue technology and failure/retry policy** (the `#40` issue body itself reserved this number; never written; resolves D5).
- **3.2 — Embedding cache hit-rate counters** (the audit-followups list flags this; one-line emit in `KnowledgeProjector.project_chunks`).
- **3.3 — Reconciliation operator surface** (resolves D8; #124 residual). HTTP `/admin/reconcile` or CLI.

### EPIC 4 — RAG / chat hardening

These items live in handovers but no issues exist yet. File them.

- **4.1 — Server-side citation validation on chat answer** (today the LLM can emit a `[chunk_id]` it never saw). Trust-critical.
- **4.2 — Empty-retrieval short-circuit on `/knowledge/chat`** (deterministic "no relevant content" reply).
- **4.3 — Hybrid retrieval (BM25 + vector)** for keyword-heavy queries.
- **4.4 — Reranker step** before the LLM call.
- **4.5 — Eval harness** (golden Q&A pairs + CI gate on retrieval quality + Recall@k / MRR).
- **4.6 — Widget chat panel** (mirror of `apps/web` ChatPanel into `apps/widget`).
- **4.7 — Decide D6** (audited wrapper that strips uncited claims) — informs whether 4.1 is mandatory or a tunable.

### EPIC 5 — Knowledge handoff / export

| Existing | Verdict | Notes |
|---|---|---|
| #22 Canonical knowledge-asset taxonomy | RENAME (see EPIC 1 note) + KEEP | Schema for asset *types*. |
| #23 Chunking + RAG export package | KEEP, scope-tighten | Phase 3 already chunks; this issue should now scope to *deterministic chunk export package* (stable IDs, checksums, pathing) — the *vector* part has been overtaken by Phase 3. |
| #90 Export validated assets / handoff package | KEEP | Customer-facing export action, governed (validated only by default). |

**Missing items to file:**

- **5.1 — Entity resolution / canonicalization across documents** (depends on #22). Today entity types are free-form strings; this is the cross-document canonical layer.

### EPIC 6 — 3DEXPERIENCE integration

| Existing | Verdict | Notes |
|---|---|---|
| #78 Widget embedding + brand token adapter | KEEP, blocked on D7 | First 3DX container size + auth/context model needed. |
| #89 Source metadata + 3DX object links | KEEP | Triples cannot trace back to PLM/CAD without this. |
| #95 Source-system import (3DX or external repos) | KEEP | Post-MVP integration; carries source metadata via #89. |

### EPIC 7 — Document intelligence breadth

| Existing | Verdict | Notes |
|---|---|---|
| #47 OCR for scanned PDFs | KEEP | Behind opt-in `[ocr]` extra; tesseract. |

**Missing items to file:**

- **7.1 — Table / structured-data extraction** (PDF tables, DOCX tables) → semantic asset type.
- **7.2 — XLSX parser** (`openpyxl`).
- **7.3 — HTML parser** (`selectolax` or `bs4` with sanitization).
- **7.4 — EML parser** (RFC-822 parsing + attachment dispatch).
- **7.5 — CSV parser**.
- **7.6 — Re-evaluate Docling** (#20-style) once OCR + tables are settled.

### EPIC 8 — Quality / DX

| Existing | Verdict | Notes |
|---|---|---|
| #24 Golden document fixtures + regression snapshots | KEEP | Snapshots for ingestion → semantic → markdown. |
| #66 Strengthen test shape (assert contracts) | KEEP | hypothesis property tests + try/except → pytest.raises. |

**Missing items to file:**

- **8.1 — Frontend bundle visualizer + budget enforcement** (audit-followups #125 — confirm whether already shipped or still open; mvp_backlog_review listed it as open).
- **8.2 — Component tests for ReviewWorkspace and PipelineWidget** (audit-followups #121 — confirm status; if shipped, close).
- **8.3 — Request-level abort + dedup on review actions** (audit-followups #122 — confirm status).
- **8.4 — Accessibility pass + axe-core dev gate** (audit-followups #123 — confirm status).

### EPIC 9 — Bug fixes (P1/P2 from audits)

| Existing | Verdict | Notes |
|---|---|---|
| #59 Duplicate uploads create new families | KEEP, blocked on D9 | Recommendation A in the issue body: append to original family. Confirm with product before changing. |

### EPIC 10 — Production-shape (NEW)

These are not on the open backlog today but several architecture
docs flag them as deferred. They should be filed.

- **10.1 — ADR-022 — Persistence trajectory (SQLite → Postgres)** (`persistence.md` "Current Limits" notes SQLite is for MVP; resolves D11).
- **10.2 — Deployment matrix doc** (web vs widget vs preview vs server; today three frontends exist with no deployment matrix).
- **10.3 — Customer-facing surface scope** (resolves D10; pairs with EPIC 4 hardening).

---

## D. Architecture decisions audit

### D.1 ADRs taken

```
ADR-001  Document Intelligence MVP architecture                accepted
ADR-002  Hash + versioning + duplicate detection               accepted
ADR-003  Semantic Markdown output                              accepted
ADR-004  Orbital frontend stack (Vite + React + TS)            accepted
                                                               (ADR-005, -006, -007 unused)
ADR-008  SemanticDocument schema versioning                    accepted
ADR-009  SemanticEnricher boundary                             accepted
ADR-010  PDF parser (pdfplumber, MVP)                          accepted (revisit #20)
ADR-011  OpenAPI codegen                                       accepted
ADR-012  Knowledge graph layer (Neo4j, projector, Anthropic)   accepted
ADR-013  LLM provider — Anthropic only, no LangChain           accepted
ADR-014  Entity extraction prompt + cost                       accepted
ADR-015  Embedding provider — Voyage AI                        accepted
ADR-016  Chat surface mode taxonomy + route shape              accepted
```

### D.2 ADR slots reserved or missing

```
ADR-005  ?                              never written, no plan
ADR-006  Async extraction queue         reserved by #40 body, never written  ← FILE NOW
ADR-007  ?                              never written, no plan

ADR-017  Hybrid taxonomy data model     needed for EPIC 1
ADR-018  Taxonomy versioning lifecycle  needed for EPIC 1
ADR-019  Auth model + identity          needed for EPIC 2 (D1)
ADR-020  Workspace scoping              needed for EPIC 2 (D2)
ADR-021  Audit retention + tamper-evidence  needed for EPIC 2 (D3)
ADR-022  Persistence trajectory (SQLite→Postgres)  needed for EPIC 10 (D11)
```

Action: either reuse ADR-005/-006/-007 numbers or skip them. ADR-006
is the natural place to write the async queue ADR since the issue
body already cites it.

### D.3 Open architectural questions per existing ADR (no new ADR needed)

- **ADR-013** — pin Claude model variant (Opus / Sonnet / Haiku); today defaulted in code.
- **ADR-014** — section batching default (`max_sections_per_call`) is wired but stays at 1; revisit cache-hit benefit at scale.
- **ADR-015** — pin Voyage variant; HNSW `ef`/`M` tuning; embedding cache key + observability (overlaps EPIC 3 item 3.2).
- **ADR-012** — Cypher prompt for GraphRAG mode; today the `mode=graph` path uses templated Cypher — confirm and document the contract.
- **ADR-016** — empty-retrieval short-circuit (EPIC 4 item 4.2); per-mode rate limiting (depends on EPIC 2).
- **knowledge_graph_payload.md** — `(:Section)` vs `(:Chunk)` deprecation (D14); topic ID derivation; `same_topic_as` cardinality.

### D.4 Architectural themes with no doc backing

These themes are discussed in handovers / comments but have no
architecture or ADR doc. Each becomes either an ADR or a
`docs/architecture/<theme>.md`:

```
auth + identity                 (EPIC 2 → ADR-019)
multi-tenant scoping            (EPIC 2 → ADR-020)
audit retention + tamper        (EPIC 2 → ADR-021)
async queue technology          (EPIC 3 → ADR-006)
backup / restore / DR           (EPIC 3 → docs/architecture/backup_restore.md)
malware scanning                (EPIC 3 → docs/architecture/file_safety.md)
sensitive-data detection        (EPIC 2 → docs/architecture/sensitivity_policy.md)
reviewer collaboration FSM      (EPIC 2 → docs/architecture/reviewer_collaboration.md)
hybrid retrieval / rerank       (EPIC 4 → docs/architecture/rag_quality.md)
eval harness                    (EPIC 4 → docs/architecture/rag_eval.md)
entity canonicalization         (EPIC 5 → docs/architecture/entity_resolution.md)
3DX object linkage              (EPIC 6 → docs/architecture/source_links.md)
brand-token / theme spec        (EPIC 6 → orbital_widget_ux.md addendum)
production database trajectory  (EPIC 10 → ADR-022)
deployment matrix               (EPIC 10 → docs/architecture/deployment_matrix.md)
customer-facing surface scope   (EPIC 10 → product brief, then ADR if needed)
taxonomy data model             (EPIC 1 → ADR-017)
taxonomy versioning             (EPIC 1 → ADR-018)
```

### D.5 Reconciliation between long-term phases and knowledge-layer phases

`long_term_vision.md` numbers Phases 1–5 (Governed Knowledge Graph
→ GraphRAG → Cartography → C-K Design → Innovation Map). The
knowledge layer also calls its work "Phase 1 / 2 / 3" but means
projection / entity extraction / vector RAG.

These are different ladders that share numbers. Risk: confusion
when discussing "Phase 3". Action:

- Keep "Phase 1 / 2 / 3" inside `knowledge_layer.md` as the
  *implementation* ladder.
- Use "Pillar 1 / 2 / 3 / 4 / 5" in `long_term_vision.md` for the
  *product* ladder.
- Update `long_term_vision.md` once to substitute Pillar wording.

---

## E. Recommended sprint plan

Each sprint is sized to land 1 epic-ish theme + 1 mechanical theme
in parallel. PR sizes capped to ~600 LOC backend + ~400 LOC
frontend for reviewability.

### Sprint S+1 — close Phase 3 + decide governance

Goal: Phase 3 is done and EPIC 2 is unblocked.

1. Land `GET /knowledge/search` + Neo4j HNSW vector index + chunk
   embedding write-path (#186).
2. Land citation validation on `/knowledge/chat` (EPIC 4 item 4.1).
3. Land empty-retrieval short-circuit (EPIC 4 item 4.2).
4. Wire embedding cache hit/miss counters (EPIC 3 item 3.2).
5. Widget chat panel (EPIC 4 item 4.6).
6. **Decisions taken (architecture review meeting):** D1 (auth), D2
   (workspace), D5 (queue tech), D9 (#59 product call).
7. Write **ADR-019**, **ADR-020**, **ADR-006**, draft **ADR-021**.

### Sprint S+2 — auth and queue foundations

Goal: identity is real, async ingestion exists.

1. Implement #83 (auth + identity middleware + actor on audit
   events).
2. Implement #91 (workspace scoping predicate everywhere).
3. Implement async queue from ADR-006 (#40 backend half).
4. Land #59 fix (duplicate uploads append to original family) per
   D9.
5. **Decisions taken:** D3 (audit retention), D4 (reviewer claim
   model), D7 (3DX container).
6. Write **ADR-021** final, **ADR-022** draft (Postgres trajectory).

### Sprint S+3 — taxonomy bootstrap

Goal: EPIC 1 visible end-to-end on the demo corpus.

1. **ADR-017** + **ADR-018** (resolve D12, D13).
2. Slices 1.1, 1.2, 1.3, 1.4 (deterministic + business allocation +
   gap analysis).
3. Slices 1.9, 1.10, 1.13 (UI mode indicator + dashboard + chunk
   inspector).
4. Rename #22 to "Canonical knowledge-asset *type* taxonomy" and
   land its schema (feeds 1.1).

### Sprint S+4 — taxonomy completion + reviewer collaboration

Goal: emerging-taxonomy → business taxonomy candidate → V1 path
exists, reviewers can collaborate.

1. Slices 1.5, 1.6, 1.7, 1.8 (corpus aggregator + auto-create + LLM
   completion + version+validation workflow).
2. Slice 1.11, 1.12, 1.14 (graph view + version compare + reviewer
   actions).
3. Implement #88 reviewer assignment / locking / comments.

### Sprint S+5 — operational backbone

Goal: production-shape concerns covered.

1. #94 backup/restore runbook + script.
2. #96 metrics + readiness probes + SLAs.
3. #84 retention/purge policy + endpoint.
4. #85 malware scanning gate (no-op + opt-in real scanner).
5. #92 sensitive-data detection (deterministic MVP).
6. #124 residual reconciliation surface (HTTP or CLI per D8).

### Sprint S+6 — handoff + breadth

Goal: customers can export trusted knowledge; more file types work.

1. #22 entity-type taxonomy completed (EPIC 5 item 5.1 entity
   canonicalization).
2. #23 deterministic chunk export package (scope-tightened).
3. #90 validated-asset export package + Orbital action.
4. #47 OCR (opt-in extra).
5. EPIC 7 items 7.1 (tables) and 7.2 (XLSX) — pick the higher-value.
6. #89 source metadata + 3DX object links (lays groundwork for
   #95 in a later sprint).

### Continuous backlog

- EPIC 8 quality/DX items run as sprint side-quests; they are
  small and parallelizable.
- EPIC 4 hardening items 4.3 (BM25 hybrid), 4.4 (rerank), 4.5
  (eval harness) ladder up once the customer-facing surface
  decision (D10) is taken.

---

## F. Issues to **close** as superseded or already-shipped

Sweep candidates — verify each before closing:

| # | Reason |
|---|---|
| #210 | Superseded by EPIC 1 (kept open as parent spec; not actionable). |
| #211 | Superseded by EPIC 1 (kept open as parent spec; not actionable). |
| #22 | RENAME and KEEP; verify scope is *asset typing*, not domain content. |
| #23 | Scope-tighten (vector part overtaken by Phase 3); KEEP. |

(No closures yet — every existing open issue is still meaningful
modulo restructuring.)

---

## G. What this document does *not* fix

- The **3DEXPERIENCE platform-side decisions** (auth context API,
  brand tokens, container size) — those need an external
  conversation with the 3DX team, not an internal ADR.
- The **customer-facing audience for the chat surface (D10)** — a
  product call, not an architecture call.
- The **product naming** (Knowledge Forge / Knowledge Explorer /
  Orbital / KW Pipeline) — there are now four names floating in
  docs and tickets. A naming decision is overdue but is product
  marketing, not architecture.
- The **front-end fan-out** — `apps/web` (review workspace),
  `apps/widget` (3DX widget), `apps/explorer` (Knowledge Explorer
  read-only graph surface, shipped 2026-05-04 in #207),
  `apps/widget-preview` (browser dev preview) — there is no
  deployment matrix today. EPIC 10 item 10.2 closes the gap.

---

## H. Errata — 2026-05-15

The 2026-05-15 audit (see
[`2026-05-15-progress-plan.md`](2026-05-15-progress-plan.md) §B)
cross-checked this document against the live issue tracker. Four
issue / doc mismatches surfaced — none change the epic structure or
sprint plan, but they make this document less trustworthy as a
status source. Recording them here rather than rewriting the
original sections (the original framing is preserved for archival
clarity; future revisions of this doc should fold these in).

| Reference in this doc | What it says | Reality on 2026-05-15 |
|---|---|---|
| §C EPIC 9 (#59 duplicate uploads) | "KEEP, blocked on D9" | **Closed** 2026-05-04 (`state_reason: completed`). D9 was resolved implicitly — append-to-original-family ([Recommendation A](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/59)) was adopted. |
| §C EPIC 2 (#84 retention / purge policy) | "KEEP, blocked on D3" | **Closed** 2026-05-10. Superseded by ADR-027 (archive / purge admin tool, accepted) + the `/admin/archive/*` route family. |
| §F (#210 self-learning taxonomy, #211 hybrid taxonomy) | "Kept open as parent specs; not actionable" | **Both closed** 2026-05-10. EPIC-1 parent issue #336 + slices #338–#352 took over as the canonical tracker entries. The 200+-line specs remain readable on the issue tracker as historical context. |
| §A.4 D9 (duplicate uploads decision) | "Open, blocks #59" | Resolved implicitly when #59 closed; the chosen recommendation was option A. |

The 2026-05-14 progress plan §A.1 separately claimed that **#321
(Explorer large-corpus truncation states)** was closed via
#397/#398/#400/#401. Verification on 2026-05-15: **#321 is still
open** (last updated 2026-05-07). The four cited PRs landed the
truncation-banner surface, but the issue itself was not
administratively closed at that time. Either close it or amend the
2026-05-14 plan — out of scope for this errata block.

This errata block is the canonical pointer for these mismatches; the
inline §C / §F / §A.4 entries above are preserved unmodified so the
2026-05-04 narrative reads as it was originally written.

---

*Generated 2026-05-04 by the backlog-restructure pass following
the audit-followups handover. Errata appended 2026-05-15 by the
post-audit drift sweep. Next review: after Sprint S+1
closes and ADRs 006/019/020/021 land.*
