# Converged Plan — Knowledge Injection Pipeline — 2026-05-17

Combines the **external "Knowledge Injection Pipeline MVP & Architecture"
plan** (the 20-section document shared 2026-05-17) with the **actual repo
state** and the **Neo4j-decoupling assessment** done the same day.

Companion to (not a replacement for):

- [`2026-05-04-backlog-restructure.md`](2026-05-04-backlog-restructure.md)
  — the comprehensive backlog/status reset.
- [`2026-05-15-progress-plan.md`](2026-05-15-progress-plan.md) —
  the most recent sprint-level rollforward.
- [`mvp_backlog_review.md`](mvp_backlog_review.md) — the existing MVP scope.

Goal of this revision:

1. Reconcile the external plan against shipped code and ADRs.
2. Record the four decisions taken 2026-05-17 about stack, OCR,
   Neo4j decoupling, and the business ontology.
3. Produce one MVP scope + post-MVP roadmap that the team can work
   from without having to re-read three documents.

---

## A. One-page status (2026-05-17)

### A.1 What's already on `main` that the external plan calls "MVP work"

```
INGEST + REVIEW                                            ✅ shipped
─ Catalog, FSM, parsers (txt/pdf/docx/pptx), dedup, idempotency
─ Semantic JSON contract + schema-version migration policy
─ Reviewer workbench (HITL): validate / reject / demote / promote
─ Confidence scoring (ADR-023), HITL router + auto-promoter
─ Persistent SQLite catalog (15 migrations), filesystem storage

SEMANTIC EXTRACTION                                        ✅ shipped
─ Topic extractor (LLM, document-level themes)             #411
─ Claim extractor (LLM, subject–predicate–object atoms)    #392
─ Process / SOP extractor (deterministic + LLM-assisted)   #390
─ Entity extractor (LLM, canonical subjects)
─ Deterministic taxonomy extractor (per-chunk concepts)    #468 (slice 1.1)
─ Taxonomy versioning lifecycle (DRAFT → CANDIDATE_V0…)    #470 (slice 1.2)
─ LLM business-taxonomy allocator per chunk                #474 (slice 1.3)

KNOWLEDGE GRAPH + VECTOR RAG                               ✅ shipped
─ KnowledgeProjector + GraphStore Protocol (2 impls)
─ Chunk relations (related_to / shares_keyword / same_topic_as)
─ Topic clustering + topic-membership edges
─ Voyage AI embeddings (voyage-3, 1024d) + Neo4j HNSW
─ Vector search + hybrid retrieval (RRF) + chat surface
─ Neighborhood / relations / atlas / explore-search routes
─ Document-relations cache (SQLite-backed, on-demand Neo4j fallback)

GOVERNANCE / OPERABILITY                                   ✅ shipped
─ Auth (ADR-019), scopes, audit events, structured logs
─ OpenAPI codegen → typed frontend client
─ Reviewer UI (apps/web) + corpus Explorer (apps/explorer)
─ Confidence + drift detection + auto-promotion
```

**Roughly 60% of the external plan's MVP scope is already on `main`.**

### A.2 Decisions taken 2026-05-17

| # | Decision | Rationale |
|---|---|---|
| **D1** | **No stack swap.** Keep Anthropic + Gemini (via `instructor`), Voyage AI embeddings, SQLite catalog, Neo4j graph, filesystem storage. | The external plan recommends LangChain / LlamaIndex / Ollama / sentence-transformers / Qdrant / PostgreSQL / MinIO. Each contradicts a shipped ADR (notably ADR-013 §"No LangChain", ADR-015 Voyage choice). Swapping would burn weeks for no behavioural gain. The current stack is working and tested. |
| **D2** | **OCR is not mandatory for MVP.** Keep on the backlog but don't gate the demo on it. | OCR (Tesseract + OCRmyPDF) is the highest-ROI ingestion gap but no upcoming demo requires scanned-document support. ADR-023's `ocr_override_active` confidence signal already exists for when OCR lands. |
| **D3** | **Neo4j decoupling is post-MVP.** Tier 1 (lazy-connect / availability) ships after the MVP demo; Tiers 2–4 stay in the backlog. | ADR-031 "Neo4j mandatory in production" stands for the MVP. Decoupling lowers operational risk but doesn't change demo capability. The Protocol pattern already lets us defer cheaply. |
| **D4** | **Business ontology is post-MVP.** BusinessDriver / KPI / Persona / Feature / Role / IPE node kinds wait for a demo or contract that specifically needs them. | The hybrid taxonomy from EPIC-1 §1.1–1.3 (now all shipped) already gives operators a generic-categories surface; full business semantics is a follow-on epic, not MVP-gating. |

### A.3 What this collapses to for the demo

A "knowledge injection pipeline" demo using the current `main` already
demonstrates the value the external plan §19 wants to convey:

> source document → noise reduction → semantic structure → graph of
> meaning → LLM-ready knowledge

The remaining MVP work is **not building the pipeline** — it's
**finishing the operator-facing surfaces** that make the value
visible.

---

## B. MVP definition (what "done" looks like)

The MVP is done when a fresh viewer can:

1. **Upload a multi-hundred-page document** (PDF / DOCX / PPTX / TXT).
2. **Watch it move through the FSM** (uploaded → extracted →
   needs_review → validated) with confidence + HITL routing visible.
3. **See the extracted knowledge surfaces**:
   - Document-level themes (DocumentTopic).
   - Deterministic + business-taxonomy categories per chunk.
   - Claims and Processes when applicable.
   - Chunk-level entity mentions.
4. **Navigate the knowledge graph** (Explorer) — neighborhood,
   bridges, atlas, taxonomy.
5. **Ask the LLM questions** grounded in the corpus (chat surface
   with citations back to source chunks).
6. **See a confidence dashboard** per document (extraction quality,
   per-section trust score, OCR flag, drift indicators).
7. **See a "high-value chunks" surface** (chunks dense in
   entities/claims/processes).
8. **See clearly labelled roadmap-only features** so the demo
   doesn't over-promise.

Of those, items 1–5 are shipped. **Items 6, 7, and 8 are the MVP
gaps.**

---

## C. MVP gap list (≈ 2 sprints)

### C.1 Confidence dashboard per document (small)

**Why it matters:** §10 of the external plan is right that
"confidence is the spine of trust". The data is already computed
(`ConfidenceScorer`, ADR-023). What's missing is an aggregated
read surface.

**Scope:**

- New read route: `GET /documents/{id}/confidence` returning a
  composite score + per-signal breakdown (OCR override, drift,
  semantic clarity, source traceability).
- Reviewer-UI panel rendering the response inline on the
  document page.
- Atlas tile aggregating corpus-wide confidence distribution.

**Stays inside existing services** — no new stores, no new
extractors.

### C.2 High-value chunks surface (small–medium)

**Why it matters:** §10.2 of the external plan. Operators want to
see "the 20 chunks that matter" without scrolling through 800.

**Scope:**

- Importance signal per chunk: weighted sum of
  `len(claims) + len(processes_step_count) + degree_in_graph +
  entity_density` (numbers exist; aggregation does not).
- New read route: `GET /documents/{id}/high-value-chunks?limit=20`.
- Explorer panel using it as the "start here" entry surface.

**Stays inside existing services** — feature engineering only.

### C.3 Roadmap-only "coming soon" UI (small)

**Why it matters:** §12.2 + §18.5 of the external plan. The demo
must distinguish working features from vision. Without this, the
audience walks away believing more is built than is.

**Scope:**

- Vision gallery in the operator UI: disabled buttons / cards for
  features in this plan's §D (cross-doc compare, contradictions,
  executive summaries, business ontology, etc.).
- Each card carries a short caption: estimated effort, backend
  dependency, planned epic.
- One static config file in `apps/web` driving the gallery
  contents.

**No backend work.**

### C.4 Demo storytelling materials (small, can run parallel)

**Why it matters:** §19 of the external plan plus the "management
demo" framing.

**Scope:**

- A scripted walkthrough doc (this repo, `docs/demos/`)
  enumerating: pick the demo document, upload, watch the FSM,
  validate, explore, chat, point at gaps.
- Sample document(s) in a fixture directory if licensing permits.

**No code.**

---

## D. Post-MVP backlog (sequenced, not committed)

Everything below is **after** the MVP demo lands. Order is
recommended; each section is independent enough to re-slot.

### D.1 Sprint Post-MVP-1 — "Survive Neo4j outage"

From the Neo4j decoupling assessment (2026-05-17), Tier 1 only:

- Lazy-connect Neo4j (move `GraphDatabase.driver(...)` out of
  `Neo4jGraphStore.__init__` into first-use).
- Structured 503 envelope per route when Neo4j is unreachable,
  rather than 500.
- Document `KW_KNOWLEDGE_LAYER_ENABLED=false` as the supported
  "graph degraded" production fallback (not just a dev flag).

Cost: ~3–5 days. Risk: near zero. Benefit: removes the worst
single-point-of-failure boot dependency.

### D.2 Sprint Post-MVP-2 — OCR + ingestion completeness

External plan §3.1 + §3.3:

- **OCR pipeline.** Tesseract + OCRmyPDF as a new enricher in
  the existing parser chain. Detect "no selectable text" PDFs;
  run OCR; produce searchable PDF + text. Threads existing
  `ocr_override_active` confidence signal.
- **Image-only document parser.** Pictures and screenshots end
  up handled by the OCR path.
- **Apache Tika evaluation** as a generic catch-all for file
  types beyond the current four. Optional; only adopt if a
  customer brings a real `.rtf` / `.epub` / `.html` corpus.
- **Unstructured-library evaluation** for layout fidelity on
  complex PDFs. Compare against `pdfplumber` output on the demo
  corpus; adopt only if quality gain is material.

Cost: ~1–2 weeks for OCR; Tika/Unstructured are evaluation
spikes.

### D.3 Sprint Post-MVP-3 — Cross-document analysis

External plan §9.1 Level 4 + §12.2 vision items:

- **Gap analysis** (EPIC-1 slice 1.4 already on the backlog):
  "what topics does the corpus not cover that the taxonomy
  expects?".
- **Contradiction detection across documents**: same claim
  subject, conflicting predicates/objects → flag for review.
- **Cross-document summary surface**: compare two documents'
  themes / claims / coverage side-by-side.
- **Executive summary per document**: one-shot LLM pass over the
  validated semantic document + extracted claims.

Cost: ~3–4 weeks total; each item is its own slice.

### D.4 Sprint Post-MVP-4 — Neo4j decoupling Tier 2

Move computed topics + entity canonical IDs into SQLite (mirror
on projection). Three more read routes
(`/knowledge/taxonomy`, parts of `/knowledge/atlas`,
`/knowledge/claims` entity hydration) stop touching Neo4j.

Cost: ~2 weeks.

### D.5 Sprint Post-MVP-5 — Business ontology layer

External plan §6.2 / §6.3 / §7.2 / §7.3:

- New TaxonomyCategory archetypes: `business_driver`, `kpi`,
  `persona`, `feature`, `role`, `ipe`, `value_lever`.
- Business-relationship vocabulary
  (`IMPLEMENTED_BY` / `MEASURED_BY` / `INFLUENCES` / `SUPPORTED_BY`)
  as new `ChunkRelationKind` literals + LLM detection prompts.
- Operator-imposed YAML extends the existing taxonomy loader;
  no new store needed.
- BusinessTaxonomyAllocator (slice 1.3, this PR) automatically
  picks them up — no extractor change.

Cost: ~2–3 weeks. Lower if the demo target is one specific
domain (HR / safety / engineering).

### D.6 Sprint Post-MVP-6 — Neo4j decoupling Tier 3

SQLite-backed `GraphStore` implementation. Neighborhood BFS via
recursive CTEs; corpus pagination via indexed scans. Neo4j
becomes "performance optimization", not "required backend".
ADR-031 amendment.

Cost: ~3–5 weeks.

### D.7 Sprint Post-MVP-N (long horizon) — Scale-out

Only when SQLite / filesystem / single-pod genuinely hits a
wall:

- Postgres migration (replaces SQLite catalog).
- S3-compatible object storage (replaces filesystem).
- Worker / queue split (extraction off the API process).
- Optional vector-DB decoupling (Tier 4): pick `sqlite-vss`,
  Qdrant, or a managed service based on the perf gap.

ADR work required at each step.

---

## E. External plan → repo reality (reference table)

For future readers who arrive at the external plan first.

| External plan section | Repo status | Notes |
|---|---|---|
| §3.1 Tika / Unstructured | Partial | Native parsers ship pdf / docx / pptx / txt. Tika as catch-all is post-MVP §D.2. |
| §3.1 OCRmyPDF / Tesseract | Not built | Backlog post-MVP §D.2. Confidence signal exists. |
| §3.2 LangChain orchestration | **Rejected** | ADR-013 forbids LangChain. Orchestration is in `KnowledgeProjector` + extractor chain. `instructor` library provides structured-output features without the dep tree. |
| §3.2 LlamaIndex retrieval | **Rejected** | `KnowledgeSearchService` + `HybridSearchService` (RRF) already cover the retrieval surface. |
| §3.3 Microsoft GraphRAG | **Use as reference only** | Methodology (community detection, hierarchical summarization) is a useful input to §D.3. Not adopting as runtime dep. |
| §3.4 Neo4j Community for MVP | ✅ | Behind `GraphStore` Protocol per ADR-031. Decoupling is post-MVP §D.1 / §D.4 / §D.6. |
| §3.4 JanusGraph long-term | Deferred | Possible Tier-4 target if Neo4j Community licensing becomes a blocker. |
| §3.5 Qdrant vector DB | **Rejected (for now)** | ADR-015: vectors live on Neo4j HNSW. Re-evaluate at Tier 4 only if perf/cost dictates. |
| §3.6 sentence-transformers | **Rejected** | ADR-015: Voyage AI (`voyage-3`). Higher embedding quality justifies the API dep. |
| §3.7 Ollama runtime | **Deferred** | Anthropic + Gemini via `instructor`. Ollama could land as a third provider behind the same factory without conflict, when local-only inference is needed. |
| §6.1 Core nodes | ✅ shipped | Document, DocumentVersion, Topic (graph) + DocumentTopic (SQLite), Chunk, Entity, SourceReference (citations), ExtractionRun (audit events). |
| §6.2 Business nodes | Backlog | §D.5. |
| §6.3 Technical nodes | Backlog | §D.5 (same epic). |
| §7.1 Generic semantic relations | ✅ shipped | `related_to`, `shares_keyword`, `same_topic_as`, `belongs_to`, `has_entity`, `part_of`. |
| §7.2 Business relationships | Backlog | §D.5. |
| §7.3 Technical relationships | Backlog | §D.5 (same epic). |
| §8 Multi-level chunking | ✅ shipped | `ChunkRecord` carries doc/version/section ids, heading, keywords, char counts, neighbors via edges. |
| §9 Semantic extraction levels 1–3 | ✅ shipped | Level 1 (structural) via parsers; Level 2 (content) via Topic/Claim/Entity/Process extractors; Level 3 (relational) via chunk_relations + ChunkTaxonomyAllocator (this PR). |
| §9 Level 4 strategic semantics | Backlog | §D.3. |
| §10 Confidence + importance scoring | Mostly shipped | Confidence scorer exists (ADR-023). Importance ranking surface is MVP §C.2. |
| §10.3 HITL trigger | ✅ shipped | `HITLRouter` + `HITLAutoPromoter` + threshold env vars. |
| §11 Storage strategy | ✅ shipped | SQLite + filesystem. Postgres / MinIO are scale-out §D.7. |
| §12.1 Core functional UI | ✅ shipped | `apps/web` + `apps/explorer`. |
| §12.2 Roadmap-only UI | Not built | MVP §C.3. |
| §13 API endpoints | ≈90% shipped | Two new endpoints for MVP §C.1 and §C.2. |
| §14 Docker Compose stack | Partial | Repo has compose for backend + Neo4j; doesn't include Qdrant / MinIO / Ollama because we don't use them. |
| §15 Migration path to scale | Deferred | Covered by §D.7. |
| §17 License summary | Aligned | Same conclusion: Neo4j Community is the only flagged dep; decoupling tiers retire the concern. |
| §18 Risks | Aligned | Same risk shape. Mitigations differ where the repo has already shipped them (Protocol pattern, confidence + HITL, generic ontology start). |
| §19 Demo story | Aligned | The demo storyboard is MVP §C.4. |

---

## F. Open questions / decisions still owed

1. **Demo target document.** A real ~500-page corporate document
   would make the demo dramatically more compelling than the
   small fixtures we test against. License + redaction is the
   blocker. Decision needed: is there a customer-sourced doc
   the team can use, or do we synthesize one from public sources?
2. **Confidence dashboard scope.** §C.1 can be a single document
   route or expanded to a corpus-wide quality dashboard. The
   latter is a bigger surface but lands the §19 demo story more
   strongly.
3. **High-value chunk ranking signal.** §C.2 weighting is
   illustrative; the right formula is a small experiment, not a
   guess. Plan a one-day spike against an existing validated
   corpus.

---

## G. Tracking

Single tracking issue should be opened on GitHub bundling §C.1 / C.2 /
C.3 / C.4 as the MVP-completion epic, with §D.1–D.7 listed as
follow-ups that don't gate the demo. Filed separately so this doc
stays the planning surface and the issue tracker stays the work
surface.

This doc supersedes the external "Knowledge Injection Pipeline MVP
& Architecture" plan as the authoritative MVP scope. The external
plan stays useful as a **vision document** for items in §D.

---

_End of converged plan, 2026-05-17._
