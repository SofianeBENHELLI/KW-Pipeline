# ADR-025: Document Similarity (Topic-Jaccard) + Version Supersede Flow

## Status

**Proposed**, 2026-05-05. Codifies the EPIC-C
([#217](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/217))
catalog + similarity primitives needed by the Knowledge Explorer's
"more like this" affordance and version-history view. Lands on top of
the [#59](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/59)
fix (PR #243) that made document families actually accumulate
versions; without that fix the supersede flow described here would
have nothing to supersede against.

## Context

Per EPIC-C, the Knowledge Explorer needs two affordances the current
catalog cannot answer:

1. **"More like this."** From a document detail view, surface the K
   most similar documents in the catalog so reviewers can find related
   prior art without a free-text search.
2. **A clean version-history story.** With #59 fixed, families now
   carry multiple versions, but the FSM has no terminal status that
   distinguishes "this version was superseded by a newer validated
   one" from "this version is the latest validated one." Today
   `DocumentVersionStatus.VALIDATED` collapses both cases, so the
   catalog and search consumers can't filter to "show me only the
   latest validated version per family" without re-deriving that fact
   client-side.

The catalog and graph also need a similarity edge between documents
to drive the Explorer's catalog view (EPIC-C C.4) and the "related
documents" panel on the future `/documents/{id}/similar` route (C.3).
The clustering primitive that surfaces topic ids per chunk
(`topic_clustering.py`, #142) already produces a deterministic signal
the similarity service can consume — we don't need a new vector
dependency for the first slice.

## Decision

### 1. `SUPERSEDED` is a new terminal `DocumentVersionStatus`

A new `SUPERSEDED` value is added to
`DocumentVersionStatus`. It is **terminal**: the FSM has no outgoing
edges from `SUPERSEDED`. The single incoming edge is `VALIDATED →
SUPERSEDED`, fired by the supersede orchestration in §2.

The transition table delta is:

| From → To | Pre-ADR-025 | Post-ADR-025 |
|---|---|---|
| `VALIDATED → SUPERSEDED` | not allowed | **allowed** |
| `SUPERSEDED → *` | n/a | **never** (terminal) |
| `* → SUPERSEDED` (anything other than `VALIDATED`) | n/a | **never** |

Catalog, search, and chat consumers filter `SUPERSEDED` out — for
those surfaces, "validated" means "currently-validated, i.e. in
`VALIDATED` and not superseded." The Orbital audit and version-history
views still surface `SUPERSEDED` so the lineage of a family is fully
inspectable.

### 2. Auto-supersede on validation of a newer sibling

When a version `vN+1` of a document family transitions to `VALIDATED`,
the most recent prior `VALIDATED` version `vK` (with `K ≤ N` and
`K` maximal among the prior validated siblings) auto-transitions to
`SUPERSEDED`. The orchestration lives in
`ReviewService.handle_validation`:

```python
# 1. Drive the new version NEEDS_REVIEW → VALIDATED via the catalog.
mark(document_id=..., version_id=..., reviewer_note=..., actor=...)

# 2. Look up the family's prior VALIDATED siblings and pick the
#    highest-numbered one (if any).
prior = max(
    (v for v in family.versions
     if v.id != new_version_id
     and v.status == DocumentVersionStatus.VALIDATED),
    key=lambda v: v.version_number,
    default=None,
)

# 3. Transition the prior validated sibling to SUPERSEDED, carrying
#    the same actor that did the validation.
if prior is not None:
    documents.mark_superseded(
        document_id=document_id,
        version_id=prior.id,
        actor=actor,
        superseded_by_version_id=new_version_id,
    )
```

The supersede transition emits a `version.superseded` audit event with
fields `{document_id, version_id, version_number,
superseded_by_version_id, actor}` so the audit trail can answer "who
superseded what, when, on whose validation".

Edge cases:

- **First validation of a family** (no prior `VALIDATED` sibling) —
  no-op. The new version is the only validated version; nothing to
  supersede.
- **Prior sibling is `REJECTED` / `SUPERSEDED` / any non-`VALIDATED`
  status** — not eligible. Only `VALIDATED → SUPERSEDED` is legal, and
  only the most recent prior `VALIDATED` is selected.
- **Race-safety.** The supersede transition is best-effort. The
  validation MUST stay durable even if the supersede write fails —
  the orchestration follows the same fire-and-log discipline as
  ADR-012's knowledge-projection side-effects: catch and log, never
  roll back the validation. The FSM's row-level
  `ALLOWED_PREDECESSORS` guard in `update_version_status` prevents a
  concurrent writer from leaking a stale transition through.

### 3. Topic-Jaccard similarity

`sim(a, b) = |Ta ∩ Tb| / |Ta ∪ Tb|` where `Ta` is the set of topic
ids touched by document `a`'s chunks (already produced by
`TopicClusteringService`). Why Jaccard:

- **Deterministic** — same input topic sets always produce the same
  output. No tie-breaking needed beyond a documented sort order in
  `top_k`.
- **Free** — uses topic ids that are already computed during
  projection; no Phase 3 vector dependency, no extra LLM spend.
- **Set-based** — robust to long-document bias that bag-of-words
  cosine exhibits. A 50-page document and a 1-page document that
  touch the same two topics score the same as each other.

The similarity surface is the in-process
`DocumentSimilarityService` class:

```python
class DocumentSimilarityService:
    def __init__(self, *, topics: DocumentTopicProvider) -> None: ...

    def compute(self, doc_a_id: str, doc_b_id: str) -> float:
        """Jaccard in [0.0, 1.0]. sim(x, x) = 1.0. Returns 0.0 when
        either doc has no topics yet (cold-start tolerance)."""

    def top_k(self, doc_id: str, k: int) -> list[tuple[str, float]]:
        """K most similar documents excluding doc_id, sorted by
        similarity descending then document_id ascending. Documents
        with score 0.0 are dropped."""
```

The service is stateless. It consumes a
`DocumentTopicProvider` Protocol so unit tests inject a fake topic
map without spinning up the full clustering stack:

```python
class DocumentTopicProvider(Protocol):
    def topic_ids_for_document(self, document_id: str) -> set[str]: ...
    def known_document_ids(self) -> list[str]: ...
```

The pairwise `compute` is the primitive. `top_k` walks the provider's
`known_document_ids()` and ranks. A nightly batch recompute that
materialises the top-K table for every document is a future slice
(C.2 second half) — for v1 the linear walk is fine because the
catalog is small and the topic sets are already in memory.

### 4. Strict scope isolation

Similarity respects the active scope filter once EPIC-D
([#218](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/218))
lands. The contract is: similarity must NEVER cross scope boundaries
— the service consumes only documents the caller is allowed to see.
The scope filter is enforced by the caller's
`DocumentTopicProvider`: production wiring will scope-filter
`known_document_ids()` to the request's accessible scope set before
the similarity service ranks.

This ADR does not implement enforcement — no scope filter exists
yet (ADR-020 has the data model but no read-side filter wired).
Documenting the contract here keeps the C.3 HTTP slice honest when
it lands.

## Consequences

- **Positive — deterministic.** Same topic sets always produce the
  same scores. Tests pin exact ratios; no retraining-induced churn.
- **Positive — free.** No vector dep, no LLM spend. Phase 3 can stay
  off and the Explorer's "more like this" still lights up.
- **Positive — version-history is queryable.** `SUPERSEDED` is a SQL
  filter; "show me only the latest validated version per family" is
  one predicate.
- **Negative — Jaccard saturates fast.** Two documents that touch
  every topic in the corpus score 1.0 even if their content is
  unrelated. The mitigation is upstream: keep topics granular in
  `topic_clustering.py`. A vector-based similarity v2 (cosine over
  Voyage embeddings) is a follow-up slice for documents where
  topic-Jaccard is too coarse.
- **Negative — supersede is best-effort.** A flaky catalog write on
  the supersede transition leaves the prior version `VALIDATED` and
  the new version `VALIDATED` simultaneously. Mitigation: the
  catalog is the source of truth; an out-of-band reconciliation
  (mirroring the knowledge-graph reconciler in ADR-012) can
  re-derive the supersede edges from the audit log on demand.
- **Neutral — the FSM gains one terminal state.** Existing
  exhaustive `Record<…Status, …>` literals on the frontend pick up
  one new key (`SUPERSEDED`); no other consumer logic changes.

## Alternatives considered

### Cosine similarity over the entity set

Treat each document's entity set as a sparse bag-of-words and compute
cosine similarity. Rejected because the entity set is dominated by
noisy tail entries (rare named entities) and the cosine score swings
with document length even after L2 normalisation. The topic-id space
is already deduplicated by the clustering pass, which gives Jaccard
its set-based robustness for free.

### Embedding-based similarity (cosine over Voyage embeddings)

Cosine similarity over the Phase 3 chunk embeddings, aggregated to a
per-document vector. Deferred because Phase 3 is not yet integrated
into projection in production, and the deterministic-topic path is
already sufficient for the C.2 Knowledge Explorer slice. A v2 that
adds the embedding-based score as a tie-break (or a fallback when
topic-Jaccard returns 0.0) is on the roadmap.

### No similarity at all

Skip the C.2 slice and ship only the SUPERSEDED status + lineage
viewer. Rejected because the Explorer's "more like this" affordance
is a P0 UX feature per the user's audit on 2026-05-04 — the Explorer
without it is materially less useful for the pilot.

## References

- [#217](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/217) —
  EPIC-C — Catalog + similarity. The parent epic this ADR codifies.
- [#59](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/59) —
  Document family lineage prereq. Fixed in PR #243; without it,
  families could not actually accumulate versions and the supersede
  flow had nothing to supersede.
- [`topic_clustering.py`](../../apps/api/app/services/knowledge/topic_clustering.py)
  — Deterministic topic clustering. Provides the topic ids the
  Jaccard similarity consumes.
- [ADR-012](ADR-012-knowledge-graph-layer.md) — Knowledge graph layer.
  Source of the fire-and-log discipline reused for the supersede
  side-effect.
- [ADR-017](ADR-017-taxonomy-and-ontology.md) — Taxonomy and ontology.
  Companion clustering surface; topic ids and taxonomy categories
  share the same Explorer axis.
- [ADR-020](ADR-020-workspace-scoping.md) — Workspace scoping. Source
  of the scope-isolation principle that §4 commits the similarity
  surface to.
