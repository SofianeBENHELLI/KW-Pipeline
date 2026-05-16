# ADR-018: Taxonomy Versioning Lifecycle

## Status

**Proposed**, 2026-05-15. Required by EPIC-1 (Hybrid + Self-Learning
Taxonomy, parent #336) before slice 1.2 (#339 — taxonomy schema +
persistence) and slice 1.8 (#345 — taxonomy version + validation
workflow) can begin. The ADR slot has been reserved since the
2026-05-04 backlog restructure §D.2.

Companion to [ADR-017](ADR-017-taxonomy-and-ontology.md) which
ratifies the *content* shape (tree of categories) and the *classifier*
(embedding cosine). This ADR ratifies *how the taxonomy evolves over
time*.

## Context

ADR-017 shipped a single global taxonomy loaded from YAML at startup
(B2 / #213). It works because a taxonomy is small enough that "rewrite
the YAML, restart the API" is acceptable for v1.

EPIC-1 changes that posture in three ways:

1. **Self-learning taxonomy** (#210). The deterministic extractor (slice
   1.1, this PR's PR #468) produces *candidate* concepts per chunk
   from the content alone. The corpus emerging aggregator (slice 1.5,
   #342) rolls those candidates up into proposed classes / subclasses.
   This is a *draft* taxonomy that operators decide whether to
   promote into a real one.
2. **LLM completion** (#210 §6, slice 1.7 / #344). An LLM proposes
   missing categories / synonyms / hierarchy moves. These are
   *suggestions* — never auto-accepted.
3. **Multiple versions over time** (#210 §7). Operators promote a
   draft to V0, validate edits into V1, V2, … and archive deprecated
   versions for traceability. The Explorer renders one *active*
   version; the API exposes historical versions for audit.

Three problems follow from those:

- **Identity** — what is "the taxonomy" when there are five drafts
  and three validated versions? Today it's a singleton; tomorrow it's
  a set with a precedence rule.
- **Confusion between two timelines** — the *version* lifecycle is
  long-running (months); the *concept-suggestion* lifecycle is
  short-running (hours-to-days as a draft gets shaped). Conflating
  them ("status = accepted" — accepted what? the version? the
  individual concept?) produces audit-trail rot.
- **Audit semantics** — when an operator promotes V0 to V1, the audit
  event must carry *who* did it (the `actor.id` pattern from #460 /
  PR #460) AND *what* changed (the diff between V0 and V1).
  Today's `Taxonomy` model has neither.

A versioning lifecycle that handles all three landed as an ADR slot
on 2026-05-04 (§D.2) but was never written. This ADR fills it.

## Decision

### 1. **Two lifecycles, not one**

The system tracks two distinct state machines that compose:

```
Taxonomy version lifecycle (whole taxonomy as a resource)
─────────────────────────────────────────────────────────

    [DRAFT] ──promote──▶ [CANDIDATE_V0] ──validate──▶ [VALIDATED_V1]
       │                       │                            │
       │                       │                            └──supersede──▶ [VALIDATED_V2] ──▶ …
       │                       │                            │
       │                       └──reject──────────────┐     └──archive──▶ [ARCHIVED]
       │                                              ▼
       └──discard────────────────────────────────▶ [DISCARDED]


Concept-suggestion lifecycle (one proposed concept inside a Draft)
──────────────────────────────────────────────────────────────────

    [NEW] ──open──▶ [UNDER_REVIEW] ──┬─accept──▶ [ACCEPTED]
                                     ├─reject──▶ [REJECTED]
                                     ├─merge───▶ [MERGED]
                                     └─defer───▶ [DEFERRED]
```

Why two and not one:

- **Different cadences.** A version transitions a handful of times in
  its lifetime; a draft's concepts can transition dozens of times an
  hour during a review session.
- **Different actors.** Version transitions are governance acts
  (admin role required). Concept transitions are review acts
  (reviewer role suffices).
- **Different audit granularity.** Promoting V0→V1 is one event with a
  diff payload; accepting concept X is one event with a concept id.
  Trying to model both at the same level conflates the two.

### 2. **Version state semantics**

| State | Meaning | Mutability | Who can write |
|---|---|---|---|
| `DRAFT` | Editable workspace; concepts can be added / removed; LLM suggestions can be appended. Not visible to consumers. | Yes | Any reviewer |
| `CANDIDATE_V0` | First promotion of a draft. Concepts are frozen; the tree shape is the candidate. Visible to admins for governance review. | No (transitions only) | Admin |
| `VALIDATED_V1` (and `_V2`, `_V3`, …) | Operator-approved. **One** Validated_Vn is "active" at a time — the version the classifier reads. The active flag is implicit: the most recent VALIDATED_Vn that hasn't been ARCHIVED. | No (transitions only) | Admin |
| `ARCHIVED` | Superseded but retained for audit. Never returned by the active-version accessor; still readable via the explicit-version accessor. | No | Admin |
| `DISCARDED` | A draft that was abandoned. Retained for audit (a `DRAFT → DISCARDED` is what closes a review session that didn't produce anything worth keeping). Distinguished from `ARCHIVED` because it was never validated. | No | Reviewer (own draft) or admin |

Transitions are strict — no `VALIDATED_V1 → DRAFT` back-edge. If an
operator needs to change a validated taxonomy, they create a new
DRAFT branched from the validated version's content; promoting that
DRAFT through CANDIDATE_V0 then through `VALIDATED_V2` is the only
way to land the change.

### 3. **Identity — `(taxonomy_id, version_number)`**

A `Taxonomy` resource has a stable `taxonomy_id` (uuid) and a
monotonically increasing `version_number: int`. The
`(taxonomy_id, version_number)` pair is the canonical key.

`version_label` is a free-text display field (e.g. `"V1"`, `"V1.2"`,
`"2026-Q3"`) that operators can set on promotion. The schema doesn't
parse it; the integer `version_number` is the audit-trail key.

`schema_version: Literal["v0.1"]` per ADR-008 covers the wire shape
itself — orthogonal to `version_number`.

Tenant scope: parked behind #91 / auth, per ADR-017 §7. When the
multi-tenant story lands, `taxonomy_id` becomes per-workspace.

### 4. **Active version — implicit, not a flag**

There is no `is_active: bool` column. "Active" is derived:

```python
active(taxonomy_id) := the row with the highest version_number where
                      state == VALIDATED_Vn and state != ARCHIVED.
```

Why implicit:

- **One source of truth.** A boolean flag has to be flipped atomically
  on every transition; the derived form just reads `MAX(version_number)
  WHERE state LIKE 'VALIDATED_%'`.
- **Concurrent-write safety.** A flag-based scheme needs a unique
  constraint to prevent two-actives; the derived form is correct by
  construction.
- **Auditability.** If a future audit asks "what was active on
  2026-08-12?", the row history + the `created_at` / `state_changed_at`
  timestamps answer it directly.

The classifier reads `active(taxonomy_id)` at request time
(post-cache, see §6).

### 5. **Concept-suggestion lifecycle (per-draft)**

A `DRAFT` carries a set of `ConceptSuggestion` rows, each with one of:

| State | Meaning |
|---|---|
| `NEW` | Just added (by the deterministic extractor / LLM / operator). Pending review. |
| `UNDER_REVIEW` | A reviewer has opened the suggestion. Optimistic lock — the reviewer's `actor.id` is recorded so a second reviewer sees "in-flight by Alice". |
| `ACCEPTED` | Folded into the draft's tree at promotion time. |
| `REJECTED` | Dropped from the draft. Retained for audit so future LLM passes don't re-suggest the same thing. |
| `MERGED` | Folded into an existing category (e.g. "Battery Cooling" merged into "Thermal Management"). The merge target is recorded. |
| `DEFERRED` | Punted to a future version. Stays attached to the draft as a flagged item. |

Promoting `DRAFT → CANDIDATE_V0` snapshots the *accepted* + *merged*
suggestions into the candidate's tree. `REJECTED` and `DISCARDED`
suggestions are preserved as audit rows but not in the published
tree.

### 6. **Cache + invalidation**

The classifier embeds every category description at taxonomy-publish
time (ADR-017 §4). With versioning:

- The **active version's** category embeddings are cached in memory
  on the API process.
- Promoting a new `VALIDATED_Vn` invalidates the cache (the next
  request rebuilds embeddings for the new active version).
- `ARCHIVED` versions don't recompute embeddings — they're frozen.
- The cache is **per-version**, keyed on
  `(taxonomy_id, version_number)`, so concurrent reads of an
  archived version (audit / Explorer historical view) don't fight
  with the active classifier.

### 7. **Audit trail — every transition is an event**

Every state transition emits a structured audit event consumed by the
SQLite-backed `AuditEventStore`:

| Event | Fields |
|---|---|
| `taxonomy.draft.created` | `taxonomy_id`, `version_number`, `actor` |
| `taxonomy.draft.discarded` | `taxonomy_id`, `version_number`, `actor`, `reason` |
| `taxonomy.candidate.promoted` | `taxonomy_id`, `version_number`, `actor`, `source_version_number` (the draft this was promoted from), `accepted_count`, `rejected_count`, `merged_count`, `deferred_count` |
| `taxonomy.candidate.rejected` | `taxonomy_id`, `version_number`, `actor`, `reason` |
| `taxonomy.version.validated` | `taxonomy_id`, `version_number`, `actor`, `superseded_version_number` (if any), `diff_summary` |
| `taxonomy.version.archived` | `taxonomy_id`, `version_number`, `actor`, `reason` |
| `taxonomy.concept.added` | `taxonomy_id`, `version_number`, `concept_id`, `source` (`extractor` / `llm` / `operator`), `actor` |
| `taxonomy.concept.transitioned` | `taxonomy_id`, `version_number`, `concept_id`, `from`, `to`, `actor`, `reason?` |

`actor` follows the #91 backfill pattern (PR #460 / #462 / #464): the
authenticated principal id when present, omitted from the payload
when the transition is system-driven (e.g. the corpus aggregator
landing new NEW suggestions).

Events are emitted via the existing `audit_event_store` plumbing — no
new transport.

### 8. **Diff payload on promotion**

Promoting `CANDIDATE_V0 → VALIDATED_V1` (or `Vn → Vn+1`) attaches a
structured diff to the audit event:

```json
{
  "added_categories":   [{"id": "...", "label": "..."}],
  "removed_categories": [{"id": "...", "label": "..."}],
  "renamed_categories": [{"id": "...", "from": "...", "to": "..."}],
  "moved_categories":   [{"id": "...", "from_parent": "...", "to_parent": "..."}],
  "description_changed":[{"id": "...", "from_hash": "...", "to_hash": "..."}]
}
```

The diff is computed deterministically (sorted by id) so two
identical promotions produce byte-identical diff blobs. Per
ADR-027's purge policy the diff stays in the audit table even
after the source / target versions are archived.

### 9. **Wire shape — additive on ADR-017's existing `Taxonomy`**

Today's `Taxonomy` model (in `apps/api/app/schemas/taxonomy.py`) has
`schema_version`, `categories`, and `is_configured`. This ADR adds:

```python
class Taxonomy(BaseModel):
    schema_version: Literal["v0.1"]
    taxonomy_id: str                    # NEW — uuid
    version_number: int                 # NEW — monotonic per taxonomy_id
    version_label: str | None           # NEW — free-text display
    state: TaxonomyState                # NEW — Literal[DRAFT, ..., ARCHIVED, ...]
    created_at: datetime                # NEW
    state_changed_at: datetime          # NEW
    created_by: str | None              # NEW — actor.id of the creator
    is_configured: bool                 # EXISTING — kept for back-compat
    categories: list[TaxonomyCategory]  # EXISTING
```

Existing consumers (the read route shipped in B2, the Explorer's
left rail) continue to work because the new fields are additive.
The route's `Taxonomy` response model will fan out by `state` once
slice 1.2 lands: the public read returns the active VALIDATED_Vn;
the admin read accepts a `?version_number=` query for historical
views.

### 10. **Forward-compat with the existing YAML path**

ADR-017 §5 keeps YAML as the v1 source of edits. With ADR-018:

- A YAML import becomes a `DRAFT → CANDIDATE_V0 → VALIDATED_V1`
  promotion chain executed at import time by the loader. The first
  YAML import a deployment ever runs produces `(taxonomy_id=<uuid>,
  version_number=1, state=VALIDATED_V1)`.
- Re-importing the YAML with a meaningful change produces a new
  `version_number = N+1` and supersedes the previous Validated.
- Re-importing with **no change** (canonical-JSON hash matches) is
  a no-op — the loader skips the promotion chain to avoid noisy
  audit events on every restart.

Operators on the YAML path don't see the lifecycle directly; they
see version numbers in the Explorer's hover-tooltip + the audit
viewer. The promotion-chain abstraction is the same one the API
edit path (post-#83) will use, so swapping in the admin route later
is a contract-preserving change.

## Why not the alternatives

### Why not a single state column

Conflates the version cadence with the concept cadence. The audit
event "status changed to accepted" is ambiguous — accepted *which*
thing? Two state machines is fewer questions to answer on every
audit line.

### Why not git-style branches

Tempting because of the YAML pedigree, but operators are not git
users. A linear `version_number` is enough for the audit story; the
"branch and merge" semantics of git would mean two simultaneous
Drafts can't be promoted without merging them, which forces the
operator to learn a new mental model.

### Why not soft-delete (`archived_at: datetime | None`)

It works for documents (ADR-027) because documents have one obvious
lifecycle. The taxonomy has *two* (per §1); a single `archived_at`
flag doesn't differentiate "draft was discarded" from "validated
version was superseded".

### Why not pin one Validated version as "active" with a flag

See §4 — implicit-via-MAX is correct by construction; a flag has
race conditions and a unique-constraint requirement that the
implicit form doesn't.

## Consequences

### Positive

- **Two state machines, one storage shape.** The schema in slice
  1.2 / #339 has a single `TaxonomyVersion` table with a `state`
  column; the suggestion table is a child relation. No joins to
  derive "current state".
- **Audit trail is self-describing.** Every transition is a row
  with `actor` + `before` + `after` — replay is straightforward.
- **YAML callers see no change.** ADR-017's YAML loader is the
  only mover for the v1 deployment posture; it gains the
  promotion-chain wrap.
- **Forward compat with #83 / auth.** Per-workspace taxonomy is a
  scope predicate (`workspace_id` field), not a redesign.

### Negative

- **Three state Literals to keep in sync** — `TaxonomyState`,
  `ConceptSuggestionState`, and the event-name vocabulary.
  Mitigated by exhaustiveness tests (slice 1.2 has them).
- **Cache invalidation on `VALIDATED_V1 → VALIDATED_V2`.** Mitigated
  by per-version cache keys (§6); the cache populates lazily so
  the cost is one-time per active-version switch.

### Neutral

- **One Draft at a time per taxonomy_id, not enforced** —
  operators can have N Drafts in flight if they want. Promoting
  any one of them doesn't invalidate the others. ADR-018 doesn't
  legislate that; if it becomes a footgun, a later revision can
  add a "one active Draft" predicate.

## Implementation plan

Each item lands as its own PR.

| PR | Slice | Pre-requisite |
|---|---|---|
| **#339** | `TaxonomyVersion` schema + SQLite store; YAML loader emits the promotion chain; existing read route routes to active Validated_Vn. | This ADR. |
| **#340** | LLM business-taxonomy allocation per chunk (reads active Validated_Vn). | #339. |
| **#341** | Gap-analysis service (deterministic vs business). | #338 (already merged) + #339. |
| **#342** | Corpus emerging aggregator (creates `DRAFT` versions from the deterministic extractor's output). | #338 + #339. |
| **#345** | Concept-suggestion lifecycle + admin routes for transitions. | #339 + actor.id audit threading (already merged via #460–#464). |
| **#346** | Frontend taxonomy mode indicator (badge in left rail). | #339 minimum. |
| **(deferred)** | Admin route for `POST /knowledge/taxonomy/<id>/promote` and `/validate`. | #345 + auth #83. |

## Decisions still open before code

The macro decision ("two lifecycles, implicit active, per-version
diff in audit") is ratified above. Three sub-decisions stay
**Proposed**; push back here before slice 1.2 / #339 starts:

1. **Version numbering** — proposed monotonic per `taxonomy_id`.
   Push back: "semver" (`1.2.0`) for human-readable diffs.
2. **Suggestion-state count** — proposed five
   (`NEW`/`UNDER_REVIEW`/`ACCEPTED`/`REJECTED`/`MERGED`/`DEFERRED`).
   Push back: drop `MERGED` (handled via accept-then-rename),
   or drop `DEFERRED` (handled via stay-in-NEW).
3. **Diff payload granularity** — proposed five categories of
   change (`added` / `removed` / `renamed` / `moved` /
   `description_changed`). Push back: collapse to before/after
   tree blobs and let consumers compute the diff client-side.

## References

- [ADR-008](ADR-008-semantic-schema-versioning.md) — schema-version
  Literal pattern this ADR follows.
- [ADR-017](ADR-017-taxonomy-and-ontology.md) — taxonomy shape,
  classifier, edit-source policy. This ADR builds on §3 / §5.
- [ADR-019](ADR-019-authentication-and-authorization.md) — actor
  identity (`actor.id`) used by every audit event in §7.
- [ADR-025](ADR-025-document-similarity-and-supersede.md) — the
  pattern this ADR's version supersede mirrors at the document
  level (immutable past versions + monotonic version_number).
- [ADR-027](ADR-027-archive-purge-admin-tool.md) — purge / archive
  semantics that govern the audit retention of taxonomy events.
- [Issue #210](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/210)
  — Self-Learning Taxonomy spec (the lifecycle ladder in §7 here
  matches the spec's §7 lifecycle).
- [Issue #211](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/211)
  — Hybrid Taxonomy Model spec (the suggestion-state machine in §5
  here matches the spec's §7 review workflow).
- [Issue #338](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/338)
  — Slice 1.1 deterministic extractor (already shipped as PR #468);
  one of the sources that lands `NEW` suggestions in a DRAFT.
- [Issue #345](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/345)
  — Slice 1.8 version + validation workflow that consumes this ADR.
- `docs/roadmap/2026-05-04-backlog-restructure.md` §D.2 — ADR slot
  reservation.
