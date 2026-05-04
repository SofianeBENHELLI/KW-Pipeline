# Planning — Smart HITL, INTEROP, Catalog-in-Explorer, Swym Community Scoping

**Status:** *requirements-and-questions only*. No code, no ADRs, no
schema. The four features below are flagged so the open questions
they raise can be answered before any implementation starts.

**Scope:** four user requests filed on 2026-05-04:

1. **Smart HITL routing.** Human review only when relevant: triggered
   on doubt about chunking + semantic mapping, then SPC-style sampling
   (1/100, 1/1000) at steady state. Manufacturing analogy: full
   inspection during ramp-up, statistical control afterwards.
2. **External / INTEROP review workflow.** The architecture must leave
   a hook for an external workflow system (3DEXPERIENCE workflow,
   ServiceNow, JIRA, …) to act as the reviewer.
3. **Catalog of ingested documents in Knowledge Explorer.** New
   section in `apps/explorer` listing every ingested document with
   document-to-document similarity scores.
4. **Swym community scoping on ingestion.** At upload time, the user
   selects the target Swym community. All downstream artifacts inherit
   the scope.

**Companion docs:**
[`docs/architecture/system_architecture.md`](../architecture/system_architecture.md)
for the current shape, and
[`docs/roadmap/2026-05-04-backlog-restructure.md`](2026-05-04-backlog-restructure.md)
for how these epics fit the existing backlog.

---

## 1. Feature A — Smart HITL routing with SPC sampling

### 1.1 Problem

Today every document version is forced through `NEEDS_REVIEW` after
semantic extraction. 100% human inspection. Two problems:

- It does not scale past pilot volumes.
- The signal of "this one really needs a human" is drowned in 99%
  of trivially correct documents.

### 1.2 Goal

Route documents to human review **only** when they need it. Use
manufacturing-style statistical process control (SPC):

- **Ramp-up phase** — every document is reviewed (today's behavior).
- **Steady-state phase** — only documents flagged as *doubtful*, plus
  a small statistical sample (e.g. 1/100 or 1/1000), are routed to a
  human. Everything else is auto-validated.
- **Drift detection** — if the sampled-doc rejection rate climbs
  above a threshold, fall back to ramp-up automatically.

### 1.3 Doubt criteria — *to evaluate*

The user explicitly asked us to evaluate the criteria. Proposed
composite signal `DocumentConfidenceScore`, weighted; final weights
TBD after a tuning pass on real data:

| Signal | Source | What it tells us |
|---|---|---|
| Chunk-relation density | `knowledge/chunk_relations.py` | Low density → fragmented document, possibly bad chunking |
| Orphan chunk count | `knowledge/topic_clustering.py` | Chunks with no relations → suspicious |
| Topic clustering ambiguity | clustering | Many small clusters / no clear cluster → fuzzy semantics |
| Citation coverage (LLM Phase 2) | `knowledge/entity_extractor.py` | % of triples returned with valid citations vs dropped |
| Schema strict-pass rate | `semantic_schema_loader.py` | LLM output that nearly fails Pydantic but barely passes |
| Embedding novelty | Voyage embeddings | Cosine to nearest existing chunk; very far → new domain |
| Acronym density | new heuristic | High → likely needs human ear |
| Section length anomalies | parser output | Very short (parser failure) or very long (OCR run-on) sections |
| Asset-count anomaly | semantic output | Significantly fewer/more assets than corpus norm |
| OCR-derived flag | parser output | Always force review (already in #47) |

The composite score collapses these into one `confidence ∈ [0, 1]`,
and the HITL router's threshold decides routing.

### 1.4 Open questions (answer before implementation)

- **Q1.1** Do auto-validated docs project into the KG with the same
  trust as human-validated, or do they carry a `validation_actor:
  system` flag visible to consumers?
- **Q1.2** Ramp-up exit criterion? Examples: "300 consecutive
  validated docs with 0 rejections", "rejection rate < 5% over a
  100-doc rolling window", "manual flip by an admin".
- **Q1.3** Sampling rate scope: per-corpus, per-community,
  per-parser, per-content-type?
- **Q1.4** Are the proposed signals in §1.3 the right list? Anything
  to add or weight to zero?
- **Q1.5** When a sampled doc is rejected by a human, do we
  re-trigger ramp-up for that community/parser?

### 1.5 Architecture impact

- **Lifecycle FSM extension.** Two design alternatives:
  - **A — New state.** Add `AUTO_VALIDATED` distinct from
    `VALIDATED`. KG projection trigger fires on either.
  - **B — Sub-status.** Keep one `VALIDATED` state, add
    `validation_method: human | auto | external` and
    `validation_actor: <id|system>` metadata.
  - **Recommendation:** B — minimises FSM disruption, maximises
    flexibility, keeps consumer queries simple. Pending Q1.1.
- **New service `confidence_scorer.py`** — runs at end of semantic
  extraction; outputs `DocumentConfidenceScore` (composite).
- **New service `hitl_router.py`** — given the confidence score, the
  current SPC state for the scope, and the configured adapter,
  decides:
  - send to human reviewer (Orbital), or
  - send to external workflow (Feature B), or
  - auto-validate.
- **New table `sampling_state`** — per scope (community / parser /
  global): phase, last sampled doc id, rolling rejection rate,
  current sample rate, last drift event.
- **New ADR** — HITL routing policy + SPC sampling math.

### 1.6 Reuse from existing backlog

- The deterministic taxonomy work in EPIC 1 (#210/#211) already
  produces chunk-relation and topic-clustering signals. The
  confidence scorer reuses them.
- The "OCR-derived → force review" rule from #47 plugs in directly.
- The reviewer collaboration FSM in #88 still applies on the
  human-routed subset.

---

## 2. Feature B — External / INTEROP review workflow

### 2.1 Problem

Today validation is one synchronous `POST /validate` call from
Orbital. Customers will want to plug an external workflow system
(3DEXPERIENCE workflow, ServiceNow, JIRA, …) as the reviewer
authority.

### 2.2 Goal

Leave a hook in the architecture so the HITL router can dispatch
review requests to an external system, with a contract for the
external system to call back with the decision.

### 2.3 Architecture impact

- **New abstraction `ReviewApprovalAdapter` Protocol** with two
  implementations:
  - `OrbitalReviewAdapter` — today's path; the human reviewer is
    inside `apps/web`.
  - `ExternalWorkflowAdapter` — emits a webhook (or queue message)
    to the external system carrying:
    - document id, version id, semantic JSON URL, Markdown URL,
    - confidence score and the signals that drove the routing,
    - callback URL with a one-time, HMAC-signed token,
    - timeout / SLA expectations.
- **New endpoint `POST /webhooks/review/{token}`** — the external
  system calls back with: decision (`validated` / `rejected`), actor
  identity, decision timestamp, optional comment.
- **New (transient) lifecycle marker** — `EXTERNAL_REVIEW_PENDING`,
  carried as metadata on `NEEDS_REVIEW` (so the FSM stays unchanged
  per Feature A recommendation B). Visible in Orbital and Explorer
  as "Awaiting external review".
- **Per-community adapter config** (depends on Feature D) — each
  Swym community picks its own review adapter.
- **Timeout policy + auto-fallback** — if the external system never
  responds within the configured window, the document falls back to
  Orbital review.

### 2.4 Open questions

- **Q2.1** Sync or async contract? *(strong recommendation: async)*
- **Q2.2** Per-community adapter config or one global? *(strong
  recommendation: per-community)*
- **Q2.3** Default behavior when no adapter is configured: Orbital
  review or auto-validate?
- **Q2.4** Timeout policy: auto-reject, auto-fallback to Orbital, or
  hold indefinitely?
- **Q2.5** Callback authentication: HMAC + idempotency key + signed
  timestamp recommended; confirm.

### 2.5 Reuse from existing backlog

- #88 (reviewer assignment / locking / comments) covers the
  human-on-Orbital path; this feature is its sibling for the
  external-workflow path.
- #83 (auth) is needed for the actor identity in the callback.

---

## 3. Feature C — Catalog of ingested documents in Knowledge Explorer

### 3.1 Problem

`apps/explorer` today shows the corpus as a graph (cluster →
document → chunk + concept map). It does **not** offer a flat
catalog view, and it does not surface document-to-document
similarity. Catalog browsing today only exists in `apps/web`
(`PipelineWidget` + `DocumentsList`), which is the reviewer
workbench, not the navigation surface.

### 3.2 Goal

Add a third view to the Explorer (next to "Corpus Overview" and
"Concept Map"): **Catalog**.

It shows every ingested document as a row, with similarity scores
linking documents that share semantic content.

### 3.3 Architecture impact

- **Document similarity service** — precomputed metric. Options:
  - **A — Centroid of chunk embeddings.** Mean-pool the chunk
    embeddings of a document; cosine similarity between doc
    centroids. Cheap once Phase 3 embeddings are live, lossy.
  - **B — Topic-vector overlap.** Each document → a sparse vector
    of topic ids it touches; Jaccard or cosine. Deterministic, no
    embeddings needed.
  - **C — TF-IDF document-level.** Fully deterministic, classical.
  - **D — Combined.** Weighted mix of A + B (recommended once
    Phase 3 embeddings ship).
- **New table `document_similarities`** — `(doc_a, doc_b, score,
  algorithm, computed_at)`. Top-K cached per document.
- **New endpoints**:
  - `GET /knowledge/catalog?community_id=…&cursor=…&limit=…` —
    flat catalog list, scoped by community.
  - `GET /knowledge/documents/{id}/similar?top=K` — top-K similar
    documents with score and algorithm.
- **Frontend (`apps/explorer`)** — new view tab "Catalog":
  - Sortable table: filename, type, status, ingested_at,
    community, parser, top-3 similars (with hover preview).
  - Click a row → focuses the document in the graph (cross-view
    navigation).
  - URL deep-link `#catalog/<doc_id>`.

### 3.4 Open questions

- **Q3.1** Similarity algorithm: A, B, C, or D?
- **Q3.2** Cross-community similarity allowed, or strictly
  intra-community?
- **Q3.3** Recompute frequency: on every new validated document,
  batch nightly, or on-demand?
- **Q3.4** Show only `VALIDATED` (+ `AUTO_VALIDATED`) docs in
  Explorer's catalog, or also intermediate statuses for
  transparency?

### 3.5 Reuse from existing backlog

- Phase 3 chunk embeddings feed similarity option A directly.
- The deterministic topic clustering already shipped (#142) feeds
  option B directly.
- The catalog already exists in SQLite; this is a new read view +
  a new precomputed column.

---

## 4. Feature D — Swym community scoping on ingestion

### 4.1 Problem

Today documents are global. There is no concept of a workspace,
project, or community in the data model. This blocks every
multi-tenant, governance, and 3DEXPERIENCE-context scenario.

### 4.2 Goal

When a user uploads a document, they choose the target **Swym
community** (3DEXPERIENCE 3DSwym collaborative space). All
downstream artifacts — chunks, topics, entities, similarities,
graph projections — inherit this scope.

This effectively **defines the workspace unit for the previously
generic #91 issue**. Workspace = Swym community, until further
notice.

### 4.3 Architecture impact

- **Data model.** Add `community_id: str` to `Document` (and
  inherited objects via the document fk). Index for fast filter.
- **Upload flow.**
  - API: `POST /documents/upload` accepts `community_id` in the
    form-data; required.
  - UI: each upload form has a community picker; the dropdown is
    populated from the user's Swym memberships (Feature D
    depends on Feature A's auth identity).
- **Filtering.** Every list / search / graph / chat / export
  endpoint takes `community_id` as a query parameter (or derives
  it from the user's session).
- **Per-community config** — see Features A and B; HITL routing
  rules and external workflow adapter are per-community.
- **Membership lookup.** New service `swym_membership_client.py` —
  reads the user's communities from 3DSwym (REST API or SSO
  claims). Cached locally with TTL.
- **Cascade behavior on community deletion in 3DX** — needs
  decision (Q4.4).

### 4.4 Open questions

- **Q4.1** 1-community-per-doc, or multi-community (cross-link)?
- **Q4.2** Per-user "personal" community concept, or all docs must
  go into a shared community?
- **Q4.3** Membership source: live 3DSwym REST, SSO claims, cached?
- **Q4.4** Behavior when a 3DX community is deleted: archive,
  soft-delete, migrate to "limbo"?
- **Q4.5** Confirm per-community config for HITL + external
  adapter?
- **Q4.6** Workspace unit = Swym community always, or are there
  other flavors (project, organization, tenant) to model?

### 4.5 Relationship with existing backlog

- **Supersedes / refines #91** — this is the workspace scoping
  ticket with the unit defined. #91 stays open as parent issue;
  this epic is the implementation.
- **Depends on #83** (auth + identity) — the user's Swym
  memberships drive the upload picker and the read-side filter.
- **Pairs with #89** (source-system metadata + 3DX object links).
- **Touches every existing list/search/graph endpoint** — the
  filter predicate must be applied server-side.

---

## 5. Cross-cutting impact

### 5.1 Hard dependency order

```
[D1 auth model] ──► [#83 auth] ──┬─► [Feature D Swym scoping]
                                 │      │
                                 │      ├─► [Feature A HITL routing]
                                 │      ├─► [Feature B INTEROP adapter]
                                 │      └─► [Feature C catalog + similarity]
                                 │
                                 └─► [#91 workspace scoping] (subsumed by Feature D)
```

Without auth (Feature A's identity = user) and Swym membership
lookup (Feature D), Features B and C cannot enforce community
boundaries; the per-community config in Features A and B has no
unit; the catalog filter has nothing to filter on.

**Practical consequence:** Feature D should land first, immediately
after #83 lands. Features A, B, C can run in parallel after D.

### 5.2 New ADRs to write

| ADR | Subject | Triggered by |
|---|---|---|
| ADR-019 | Auth model + identity propagation | EPIC 2 (existing) |
| ADR-020 | Workspace scoping (= Swym community) | Feature D |
| ADR-021 | Audit retention + tamper-evidence | EPIC 2 (existing) |
| **ADR-023** | **HITL routing policy + SPC sampling math** | **Feature A** |
| **ADR-024** | **External review approval contract (callback shape, idempotency, HMAC, timeout)** | **Feature B** |
| **ADR-025** | **Document similarity algorithm + persistence** | **Feature C** |
| **ADR-026** | **Swym membership integration (live REST vs SSO claims vs cache)** | **Feature D** |

ADRs 019/020/021 are already on the backlog from the prior
restructure doc (§D.2). The four new ADRs (023–026) come out of
this planning round.

### 5.3 Lifecycle FSM — recommended unified shape

```
EXTRACTED
  └─ NEEDS_REVIEW       (always, FSM unchanged)
        │
        │  HITL router decides routing dispatch (metadata only):
        │   ├─ validation_method = human    → Orbital review
        │   ├─ validation_method = external → ExternalWorkflowAdapter
        │   └─ validation_method = auto     → immediate auto-validate
        │
        ▼
  VALIDATED            (carries: validation_method, validation_actor,
                                  confidence_score, signals)
        │
        ▼
  KG projection (unchanged trigger)
```

Every existing consumer keeps working. Audit log records the
routing decision. UI can colour-code or badge by
`validation_method` if useful.

### 5.4 Frontend impact summary

| Frontend | Catalog view | Community picker | HITL signals visible | External-review marker |
|---|---|---|---|---|
| `apps/web` (Orbital) | exists; add community filter | upload form | reviewer queue badges | "external review pending" badge |
| `apps/widget` (KnowledgeForge) | exists | upload form | summary counts | "external review pending" count |
| `apps/explorer` (Knowledge Explorer) | **new** | filter only | optional badges in graph | optional badge |

### 5.5 Test surface increase

- Property tests on the SPC state machine: ramp-up → steady-state
  → drift detected → ramp-up.
- Integration test for the external-workflow callback (HMAC sig
  validation + idempotency-key replay).
- Snapshot tests for the new catalog view in Explorer.
- Cross-community isolation tests (Feature D): user A in community
  X cannot see community Y.

---

## 6. Proposed issues

Filed simultaneously with this doc as parent epic-issues (each
references this planning doc):

| Epic # | Title | Blocked-on |
|---|---|---|
| EPIC-A | Smart HITL routing & SPC sampling | Q1.1–Q1.5 + Feature D |
| EPIC-B | External / INTEROP review workflow adapter | Q2.1–Q2.5 + Feature D |
| EPIC-C | Knowledge Explorer catalog + document similarity | Q3.1–Q3.4 + Phase 3 search |
| EPIC-D | Swym community scoping on ingestion | Q4.1–Q4.6 + #83 (auth) |

Each epic's body restates the scope, lists the open questions, and
points back here.

---

## 7. Decisions needed before any code lands

Strict prerequisite list, in dependency order:

1. **Q4.6** — confirm the workspace unit is Swym community.
2. **Q4.1, Q4.2, Q4.3, Q4.4** — settle the Swym data model.
3. **D1** (existing decision in the prior restructure doc) — auth
   model. Without identity, community scoping is theoretical.
4. **Q1.1, Q1.2, Q1.3** — settle the HITL routing math.
5. **Q1.4** — confirm the doubt-criteria signal list and weights.
6. **Q2.1, Q2.2, Q2.3, Q2.4, Q2.5** — settle the external workflow
   contract.
7. **Q3.1, Q3.2, Q3.3, Q3.4** — settle the similarity algorithm
   and freshness.

Once 1–7 are answered, the four ADRs (023–026) get drafted, then
the implementation slices get filed.

---

*Generated 2026-05-04. No code, no schema, no FSM change yet.
Implementation starts after the open questions in §7 are answered.*
