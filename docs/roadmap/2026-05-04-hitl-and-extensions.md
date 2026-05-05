# Planning — Smart HITL, ITEROP, Catalog-in-Explorer, Multi-Scope Ingestion

**Status:** *decisions taken*. Open questions answered in a Q&A round
on 2026-05-04. One follow-up still pending (ITEROP auth, awaits the
ITEROP documentation).

**Scope.** Four user-requested features and the architecture they
imply:

1. **Smart HITL routing** — manufacturing-style SPC over the human
   review gate. Full inspection during ramp-up, statistical sampling
   afterwards, with drift-driven escalation.
2. **External / ITEROP review workflow** — the architecture leaves a
   pull-based hook so an external workflow tool (ITEROP / ServiceNow
   / 3DX workflow / JIRA…) can act as the reviewer.
3. **Catalog of ingested documents in Knowledge Explorer** — a new
   read view in `apps/explorer` listing every ingested document with
   document-to-document similarity and version lineage.
4. **Multi-scope ingestion** — at upload time the user selects the
   target scope. Three scope flavors: `personal`, `swym_community`,
   `project`. A document can live in N scopes simultaneously.

**Companion docs:**
[`docs/architecture/system_architecture.md`](../architecture/system_architecture.md)
for the current shape;
[`docs/roadmap/2026-05-04-backlog-restructure.md`](2026-05-04-backlog-restructure.md)
for how these epics fit the existing backlog.

---

## 0. Decisions taken (Q&A round 2026-05-04)

### Scoping (EPIC-D)

| ID | Decision |
|---|---|
| Q4.6 | Two scope flavors in parallel: **`swym_community`** and **`project`**. Plus an automatic **`personal`** scope per user (Q4.2). |
| Q4.1 | **Multi-scope total** — a document can live in N scopes at once, sharing the same chunks/embeddings. Join table `document_scopes`. |
| Q4.2 | Scope `personal:<user_id>` **auto-created on first sign-in**. Default upload destination. Visible only to its owner. |
| Q4.3 | **Live REST to 3DSwym** for membership lookup, with **per-request memoisation** to avoid hammering 3DSwym during a single API call. Circuit breaker for outages. No cross-request cache. |
| Q4.4 | **Hard-delete** the `(document, scope)` link when a Swym community is deleted. If the document loses all its scopes → **purge** (bytes + artifacts). |
| Q4.5 | HITL routing rules and external adapter are **configured globally** for the deployment. Not per-scope. |

### HITL (EPIC-A)

| ID | Decision |
|---|---|
| Q1.1 | Auto-validated documents are **indistinguishable from human-validated** in public APIs (Search, Chat, Explorer, KG). The metadata (`validation_method`, `validation_actor`, `confidence_score`) exists in the database and audit trail but is **not surfaced to consumers**. |
| Q1.2 | SPC bucket = `(content_type, topic_cluster)`. The **sample size N** and **success threshold P%** are admin-tunable (e.g. N=10, P=95% for "10 first chunks validated → bucket trusted"). A **`force-auto` global mode** bypasses all routing with a corpus-level disclaimer. |
| Q1.3 | Implicit from Q1.2 — sampling rate granularity matches the SPC bucket. |
| Q8.1 | "Semantic domain" = the **dominant topic cluster** computed by `topic_clustering.py` over the document's chunks. Snapshot at routing time; not retroactive. |
| Q1.4 | Five signals compose `DocumentConfidenceScore`. Performance-first selection: **OCR flag** (hard override) · **orphan chunk ratio** · **section length z-score vs corpus norm** · **topic incoherence ratio** · **citation coverage** (when Phase 2 ON) **or asset-count z-score** (fallback). Each is O(chunks) at most. |
| Q1.5 | **Drift counter on a sliding window**. On threshold crossed, **escalate the sampling rate** (e.g. 1/100 → 1/10) before falling back to ramp-up. Sample-rate **ladder** with admin-tunable thresholds. |

### ITEROP / external workflow (EPIC-B)

| ID | Decision |
|---|---|
| Q2.1 | **Asynchronous pull**. The external system polls `GET /reviews/pending` and posts decisions on `POST /reviews/{id}/decision`. **No outbound HTTP** from KW Pipeline. |
| Q2.2 | Implicit from Q4.5 — single global adapter (or none). |
| Q2.3 | Default behavior with no adapter configured: **ramp-up → Orbital, steady-state → auto-validate**. |
| Q12.1 | Orbital is always reachable for: (1) ramp-up phase, (2) confidence-based doubts, (3) SPC sampling when no external adapter is configured. The external adapter, when present, takes over (2) and (3). |
| Q2.4 | **Auto-reject on timeout**. A worker periodically scans `EXTERNAL_REVIEW_PENDING` and rejects expired entries with reason `external_workflow_timeout`. Timeout is admin-configurable. The uploader can re-submit. |
| Q2.5 | **Deferred**. Authentication scheme (HMAC / OAuth / mTLS / opaque token) is TBD until the ITEROP documentation lands. The adapter is named `ITEROPAdapter` for the first integration; `ReviewApprovalAdapter` is the generic Protocol. |

### Catalog + similarity (EPIC-C)

| ID | Decision |
|---|---|
| Q3.1 | **Jaccard on topic-sets**. Each document → set of topic ids; similarity = `|A ∩ B| / |A ∪ B|`. Deterministic, free (topics already computed by `topic_clustering.py`), no Phase 3 dependency. |
| Q3.2 | **Strict scope-isolation**. Similarity respects the user's current scope filter. No leak across communities even when the user has access to both. |
| Q3.3 | **Batch nightly recompute**. 24-hour freshness tolerance. Plus the version-supersede flow (Q17.1) which is event-driven, not batch. |
| Q17.1 | Only the **latest version** of each family is visible in KE. The latest version carries a badge ("v2", "v3") which opens a **lineage viewer** showing the full version history. Older versions remain in Orbital for audit. |
| Q3.4 | KE catalog shows `VALIDATED` (+ `AUTO_VALIDATED` treated identically) **and** `NEEDS_REVIEW` (with "review in progress" badge). Hidden: every status before `NEEDS_REVIEW`, plus `REJECTED`, `FAILED`, `SUPERSEDED`. |

### New requirements that emerged from the Q&A

- **`SUPERSEDED` version status.** When `vN+1` becomes `VALIDATED`,
  `vN` transitions to `SUPERSEDED` automatically. Audit + lineage
  retain history; consumer APIs return only the latest validated.
- **Lineage viewer in Knowledge Explorer.** Triggered by the
  version-badge click on the latest version of a family. New UI
  surface in `apps/explorer`.
- **`force-auto` admin override mode.** Global env var (e.g.
  `KW_HITL_FORCE_AUTO=true`). Bypasses confidence + sampling, every
  document is auto-validated. Banner disclaimer at corpus level
  (no per-document badge — consistent with Q1.1).
- **Bug #59 is now a hard prerequisite.** Document family lineage
  must be correct (re-uploads must append a version inside the same
  family, not create a new family) before EPIC-C's superseding flow
  can be wired. #59 moves from "P1 bug" to **blocker for EPIC-C**.

---

## 1. Feature A — Smart HITL routing with SPC sampling

### 1.1 Goal

Route to a human reviewer **only** when the document is doubtful or
when SPC sampling fires; auto-validate everything else.

### 1.2 Pipeline shape (after EPIC-A lands)

```
EXTRACTED
   │
   ▼
semantic_extractor.SemanticExtractor.run
   │
   ▼
knowledge.topic_clustering ────────► topic cluster of chunks
   │                                  (= "semantic domain" for SPC)
   ▼
confidence_scorer.compute(doc, signals) ─► DocumentConfidenceScore
   │
   ▼
hitl_router.decide(
    score      = confidence_score,
    bucket     = (content_type, topic_cluster),
    spc_state  = sampling_state[bucket],
    adapter    = configured_adapter | None,
    force_auto = settings.KW_HITL_FORCE_AUTO,
)
   │
   ├─► validation_method = human    → Orbital queue (NEEDS_REVIEW)
   ├─► validation_method = external → ITEROP queue (NEEDS_REVIEW + EXTERNAL_REVIEW_PENDING)
   └─► validation_method = auto     → mark_validated() immediately
```

### 1.3 The 5 signals

```python
def compute_confidence(doc) -> DocumentConfidenceScore:
    if doc.has_ocr_flag():                            # hard override
        return DocumentConfidenceScore(score=0.0, signals={"ocr": 1.0})

    signals = {
        "orphan_ratio":      orphan_chunks(doc) / total_chunks(doc),
        "length_z":          section_length_z_score(doc, corpus_norm),
        "topic_incoherence": num_topics(doc) / total_chunks(doc),
        "semantic_quality":  citation_coverage(doc) if phase2_on
                             else asset_count_z_score(doc, corpus_norm),
    }
    score = 1.0 - weighted_average(signals, weights=settings.HITL_WEIGHTS)
    return DocumentConfidenceScore(score=score, signals=signals)
```

The threshold (e.g. `confidence < 0.7 → human review`) is
admin-tunable.

### 1.4 SPC sample-rate ladder (Q1.5)

```
ramp-up    ── 100% review
   │
   │ exit when: rolling_success_rate(window=N) ≥ P%
   │            (admin-tunable per bucket)
   ▼
steady L1  ── sample 1/100, drift counter on rejections
   │
   │ escalate if: rolling_rejection_rate(window=W) > T1
   ▼
steady L2  ── sample 1/10, drift counter
   │
   │ escalate if: rolling_rejection_rate > T2
   ▼
back to ramp-up (force human inspection again)
```

### 1.5 Lifecycle FSM extension

The FSM **does not change**. The decision is captured as metadata on
the existing `VALIDATED` and `NEEDS_REVIEW` states:

```python
@dataclass
class ValidationMetadata:
    validation_method: Literal["human", "external", "auto"]
    validation_actor: str  # user id, "system", "ITEROP:<external_id>"
    confidence_score: float
    confidence_signals: dict[str, float]
    spc_bucket: tuple[str, str]  # (content_type, topic_cluster_id)
    spc_phase: Literal["ramp-up", "steady_l1", "steady_l2"]
    sampled: bool                 # True if doc was randomly selected
```

This metadata is persisted alongside the existing
`reviewer_note`/`actor` fields. Auto-validated docs carry
`validation_method = "auto"`; consumers do not see this distinction
(Q1.1). It feeds the audit trail and the SPC drift detector.

### 1.6 New tables / services

- `confidence_scorer.py` — composite score over the 5 signals.
- `hitl_router.py` — routing decision based on score + SPC state +
  adapter config + force-auto flag.
- `sampling_state` table — per `(content_type, topic_cluster)`:
  phase, window stats, last sample id, drift counter, current rate,
  last drift event timestamp.
- `corpus_norms` table — rolling corpus statistics (per content
  type and per topic cluster) used by length-z and asset-count-z
  signals.

### 1.7 Required ADR

**ADR-023** — HITL routing policy + SPC sampling math + the 5
signals' definitions and default weights.

---

## 2. Feature B — External / ITEROP review workflow

### 2.1 Goal

Let an external workflow (ITEROP / ServiceNow / 3DX workflow / JIRA)
act as the reviewer authority through a **pull-based, signed**
contract.

### 2.2 Contract shape

```
                  ┌────────────────────────────────────┐
                  │  external workflow (e.g. ITEROP)   │
                  └──────────────┬─────────────────────┘
                                 │ poll
                                 ▼
       GET /reviews/pending?since=<cursor>          (auth: TBD per Q2.5)
       → [{review_id, doc_id, version_id,
            semantic_url, markdown_url,
            confidence_score, signals,
            issued_at, expires_at}]

                                 │ decision
                                 ▼
       POST /reviews/{review_id}/decision           (auth: TBD per Q2.5)
       Idempotency-Key: <uuid>
       body: {decision: "validated"|"rejected",
              actor: <external_id>,
              comment?: <str>,
              decided_at: <iso8601>}
       → 200 OK | 409 (already decided)
```

### 2.3 Lifecycle marker

A document routed to the external workflow lands in `NEEDS_REVIEW`
**plus** an `EXTERNAL_REVIEW_PENDING` flag (a metadata field, not a
new FSM state). Consumers see it as `NEEDS_REVIEW` until the
callback arrives. The Orbital queue surfaces it with an "external
review pending" badge so internal reviewers know not to act on it.

### 2.4 Auto-reject worker

```python
def auto_reject_expired_external_reviews():
    for review in pending_external_reviews(expired=True):
        mark_rejected(
            version_id=review.version_id,
            actor="system",
            reason="external_workflow_timeout",
        )
```

The job runs every N minutes (admin-tunable). The audit trail
records the auto-reject with the original review id and the
external adapter name.

### 2.5 New service / endpoints

- `ReviewApprovalAdapter` Protocol with two impls:
  - `OrbitalReviewAdapter` (today's path)
  - `ITEROPAdapter` (first external impl)
- New routes:
  - `GET  /reviews/pending` (paginated, scoped, auth TBD per Q2.5)
  - `POST /reviews/{review_id}/decision` (idempotent)
- New table `pending_reviews` — tracks `(review_id, version_id,
  adapter, issued_at, expires_at, last_polled_at, decided_at?,
  decision?, decision_actor?)`.
- New worker `external_review_timeout_worker.py`.

### 2.6 Required ADR

**ADR-024** — External review approval contract: pull endpoints,
idempotency on decision callback, auth scheme (TBD pending ITEROP
documentation), timeout/auto-reject policy.

---

## 3. Feature C — Knowledge Explorer catalog + similarity

### 3.1 Goal

A flat catalog view in `apps/explorer` listing every ingested
document with similarity hints and version lineage.

### 3.2 Similarity algorithm

Pure topic-Jaccard:

```python
def similarity(doc_a, doc_b) -> float:
    topics_a = set(topic_id for chunk in doc_a.chunks for topic_id in chunk.topics)
    topics_b = set(topic_id for chunk in doc_b.chunks for topic_id in chunk.topics)
    if not (topics_a or topics_b):
        return 0.0
    return len(topics_a & topics_b) / len(topics_a | topics_b)
```

Deterministic, free (topics already exist), no Phase 3 dependency.
No `document_similarities` cache table needed at first — the top-K
query is fast enough at the corpus scales we expect during the
pilot. A persisted top-K cache can be added later if the catalog
view becomes hot.

### 3.3 Version lineage

The catalog shows **only the latest version of each family**. The
latest version row carries a "v2" / "v3" badge that opens a
**lineage viewer** showing the full version history with metadata
(uploaded by, validated by, validated at, validation_method,
similarity to the previous version).

When `vN+1` becomes `VALIDATED`, a side-effect transitions `vN` to
`SUPERSEDED`:

```python
def on_version_validated(family_id, new_version_id):
    for prior in older_validated_versions(family_id):
        prior.status = SUPERSEDED
        prior.superseded_by = new_version_id
        prior.superseded_at = utc_now()
```

Knowledge Search and Chat both filter to `VALIDATED` only (not
`SUPERSEDED`).

### 3.4 New endpoints

- `GET /knowledge/catalog?scope_kind=&scope_ref=&cursor=&limit=`
  — flat catalog list, scoped by the active scope filter.
- `GET /knowledge/documents/{id}/similar?top=K` — top-K similar
  documents in the same scope (Q3.2).
- `GET /knowledge/documents/{id}/lineage` — full version history
  with metadata, used by the lineage viewer.

### 3.5 Frontend (`apps/explorer`)

New view tab next to "Corpus Overview" / "Concept Map":
**Catalog**.

- Sortable table: filename · type · status · ingested_at · scopes
  badges · version badge · top-3 similars (hover preview).
- Click a row → focuses the document in the graph.
- URL deep-link: `#catalog/<doc_id>`.
- Click version badge → opens the lineage viewer modal.

### 3.6 Required ADR

**ADR-025** — Document similarity (topic-Jaccard) and version
supersede flow.

---

## 4. Feature D — Multi-scope ingestion

### 4.1 Goal

At upload, the user selects the target scope(s). All downstream
artifacts inherit the scope. Multi-tenant isolation enforced
server-side.

### 4.2 Three scope flavors (Q4.6 + Q4.2)

```python
class ScopeKind(StrEnum):
    PERSONAL = "personal"          # personal:<user_id>, auto-created
    SWYM_COMMUNITY = "swym_community"  # ref = swym community id
    PROJECT = "project"            # internal-only, non-Swym
```

The **`personal` scope is the default** when the user uploads with
no explicit scope choice.

### 4.3 Multi-scope per document (Q4.1)

```sql
CREATE TABLE document_scopes (
    document_id  TEXT NOT NULL,
    scope_kind   TEXT NOT NULL,
    scope_ref    TEXT NOT NULL,
    added_at     TIMESTAMPTZ NOT NULL,
    added_by     TEXT NOT NULL,        -- actor id
    PRIMARY KEY (document_id, scope_kind, scope_ref)
);

CREATE INDEX idx_document_scopes_kind_ref
    ON document_scopes (scope_kind, scope_ref);
```

A document can be linked to N scopes. Chunks, topics, entities,
similarity, search, chat, KG projection — all inherit visibility
through the document's scope membership.

### 4.4 Membership resolution (Q4.3)

```python
class SwymMembershipClient(Protocol):
    def list_user_communities(self, user_id) -> list[SwymCommunityRef]: ...

class LiveSwymMembershipClient:
    """Live REST to 3DSwym, with per-request memoisation."""

    def __init__(self, swym_api_url, breaker: CircuitBreaker):
        self._url = swym_api_url
        self._breaker = breaker

    @request_scoped_memo
    def list_user_communities(self, user_id):
        with self._breaker:
            return self._swym_get(f"/users/{user_id}/communities")
```

No cross-request cache. The circuit breaker short-circuits to an
empty list on 3DSwym outage so uploads still work for `personal`
and `project` scopes (degraded but available).

### 4.5 Scope filter on every read

Every list / search / graph / chat / catalog endpoint accepts a
scope filter and returns only documents whose
`document_scopes` includes a row matching the user's accessible
scopes. The predicate is enforced server-side via FastAPI
dependencies — never trust client input.

### 4.6 Flag-only cascade on Swym community deletion (Q4.4)

Per the no-delete policy (no real deletion of document source data
— flag-only, real purge handled by a future Archive/Purge Admin
tool), this cascade soft-removes scope links and archives the
document. The KG subgraph is the one explicit exception: it is a
derived index and may be physically cleaned up.

```python
def on_swym_community_deleted(swym_community_id):
    soft_remove_scope_links(scope_kind="swym_community",
                            scope_ref=swym_community_id)
    for doc_id in unique_doc_ids_for(swym_community_id):
        if has_no_remaining_active_scopes(doc_id):
            flag_archive(doc_id)            # documents.archived_at = now
            kg.delete_subgraph_for_document(doc_id)  # derived index, OK to drop
            audit_log("document.archived_orphan",
                      doc_id=doc_id, reason="all_scopes_removed")
```

`flag_archive` is a metadata transition only — the bytes,
extractions, semantic JSON, and Markdown asset stay in the catalog.
The Archive/Purge Admin tool (separate ADR, deferred) is the only
path to physical deletion or rehydration. The detection mechanism
(3DSwym webhook vs lazy detection on next access) is an
implementation detail. Audit retention is a separate concern handled
by EPIC 2 (#84).

### 4.7 Required ADRs

- **ADR-020** — Workspace scoping: three-flavor scope model with
  multi-scope documents.
- **ADR-026** — Swym membership integration: live REST with
  per-request memoisation and circuit breaker.

---

## 5. Cross-cutting impact

### 5.1 Hard dependency order

```
[D1 auth model — ADR-019]
   │
   ▼
[#83 auth implementation]
   │
   ├──► [EPIC-D scoping — ADR-020 + ADR-026]
   │       │
   │       ▼
   │    [EPIC-A HITL — ADR-023]
   │       │       (uses scope only as a filter; SPC bucket is
   │       │        (content_type, topic_cluster), not scope)
   │       ▼
   │    [EPIC-B ITEROP — ADR-024]
   │       │       (depends on EPIC-A's NEEDS_REVIEW + metadata)
   │       │
   │       └──► requires ITEROP documentation for Q2.5 (auth scheme)
   │
   └──► [#59 family-lineage bug fix]
           │
           ▼
        [EPIC-C catalog + similarity — ADR-025]
                (depends on family-lineage being correct so that
                 SUPERSEDED transitions are sound)
```

### 5.2 New ADRs to write (numbered)

| ADR | Subject |
|---|---|
| ADR-019 | Auth model + identity propagation (existing backlog) |
| ADR-020 | Workspace scoping — three-flavor scope model |
| ADR-021 | Audit retention + tamper-evidence (existing backlog) |
| **ADR-023** | **HITL routing policy + SPC math + 5-signal definition** |
| **ADR-024** | **External review pull contract — ITEROP adapter** |
| **ADR-025** | **Document similarity + version supersede flow** |
| **ADR-026** | **Swym membership live REST integration** |

### 5.3 Frontend impact

| Frontend | Catalog view | Scope picker | Lineage viewer | External-review badge |
|---|---|---|---|---|
| `apps/web` (Orbital) | exists; add scope filter | upload form | yes | yes |
| `apps/widget` (KnowledgeForge) | exists | upload form | summary count only | summary count only |
| `apps/explorer` (Knowledge Explorer) | **new** | filter only | yes | optional |

### 5.4 Test surface increase

- Property tests on the SPC state machine: ramp-up → steady_l1 →
  steady_l2 → ramp-up.
- Snapshot tests on the catalog view + lineage viewer.
- Integration tests for the external-workflow pull contract
  (`GET /reviews/pending` + `POST /reviews/{id}/decision` with
  Idempotency-Key replay).
- Cross-scope isolation tests (user in scope X cannot see scope Y).
- Version supersede flow: validating `vN+1` correctly transitions
  `vN` to `SUPERSEDED` and removes it from search/chat results.

### 5.5 Bug #59 promoted to blocker

Issue #59 (duplicate uploads create new families instead of new
versions) is now a **hard prerequisite for EPIC-C**, because
the version supersede flow (Q3.3 / Q17.1) relies on family lineage
being correct. Fix order: #59 → EPIC-D → EPIC-A → EPIC-B → EPIC-C.

---

## 6. Outstanding question

Only one question remains open after this round:

- **Q2.5 — ITEROP adapter authentication scheme.** Pending the
  ITEROP documentation. ADR-024 carries the placeholder; the
  decision will be HMAC, OAuth bearer, mTLS, or opaque token
  depending on what ITEROP expects.

Everything else has been decided.

---

## 7. Issue mapping

The four parent epic issues were filed alongside this doc:

| Epic | GitHub | Status |
|---|---|---|
| EPIC-A — Smart HITL routing & SPC sampling | #215 | decisions taken; ready to slice |
| EPIC-B — External / ITEROP review workflow | #216 | decisions taken; auth pending Q2.5 |
| EPIC-C — Knowledge Explorer catalog + similarity | #217 | decisions taken; blocked by #59 |
| EPIC-D — Multi-scope ingestion | #218 | decisions taken; blocked by #83 |

Granular implementation slices will be filed once #83 (auth) and
#59 (family lineage) land, since both are hard prerequisites.

---

*Generated 2026-05-04 after the Q&A round. Implementation starts
once #59 + #83 land and ADR-019 / ADR-020 are merged.*
