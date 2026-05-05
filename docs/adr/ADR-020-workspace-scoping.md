# ADR-020: Workspace Scoping — Three-Flavor Scope Model with Multi-Scope-per-Document

## Status

**Proposed**, 2026-05-04. Codifies the scoping decisions taken in the
2026-05-04 Q&A round
([`docs/roadmap/2026-05-04-hitl-and-extensions.md`](../roadmap/2026-05-04-hitl-and-extensions.md)
§4) and required by EPIC-D (#218). This ADR **redefines** the
workspace concept that #91 originally framed.

## Context

KW Pipeline today treats every uploaded document as part of a single
flat global pool. Search, the Knowledge Graph, the Knowledge Explorer,
and the Phase 3 chat surface all read against that pool with no
isolation between users or communities.

That posture was acceptable for the local-only MVP, but it breaks down
for the embedded 3DEXPERIENCE deployment. Inside 3DEXPERIENCE, users
sit in **3DSwym communities** — bounded social spaces with explicit
membership. The product expectation is that content uploaded into a
community is visible to members of that community and nobody else.
The pilot also needs a place for content that does *not* belong in any
3DSwym community — internal experiments, unreleased work, scratch
ingestions — without polluting any community's view.

#91 originally framed this as a single `workspace_id` on each
document. The Q&A round on 2026-05-04 surfaced two facts that change
the answer:

1. The same document may legitimately need to be visible in **more
   than one** community (e.g. a hybrid-work policy referenced by both
   the HR community and the operations community). A single
   `workspace_id` forces a copy-and-re-ingest workflow that wastes
   embedding spend and breaks chunk-level identity.
2. Every user needs a **personal** working space from day one — a
   default upload destination that exists before the user has joined
   any community.

This ADR replaces the single-`workspace_id` shape with a three-flavor
scope model and a join table that allows a document to live in N
scopes simultaneously. It is the first of two scoping ADRs: ADR-026
specifies the runtime membership lookup that this ADR's read-side
filter depends on.

## Decision

### 1. Three scope flavors

```python
class ScopeKind(StrEnum):
    PERSONAL = "personal"           # personal:<user_id>, auto-created
    SWYM_COMMUNITY = "swym_community"  # ref = 3DSwym community id
    PROJECT = "project"             # internal, non-Swym
```

- **`personal`** — auto-created on first sign-in. The reference
  pattern is `personal:<user_id>`. The personal scope is the
  **default upload destination** when the user does not pick one.
  Visible only to its owner. Always exists; never deleted while the
  user account exists.
- **`swym_community`** — the reference is the 3DSwym community id.
  Visibility follows live 3DSwym membership (see ADR-026). The scope
  exists for as long as the underlying 3DSwym community exists; when
  the community is deleted, every document-scope link with that
  reference is **soft-removed** (`removed_at` flag), not physically
  deleted (see §4).
- **`project`** — an internal-only, non-Swym scope. The reference is
  a server-issued opaque id. `project` is for content that does not
  belong in any 3DSwym community: internal experiments, drafts the
  team wants to share without wiring them through 3DSwym, content
  staged before publication. Membership of a `project` scope is
  managed in the local catalog, not via 3DSwym.

The Pydantic shape:

```python
class ScopeRef(BaseModel):
    kind: ScopeKind
    ref: str  # opaque to the rest of the system; meaning depends on kind

class Scope(BaseModel):
    schema_version: Literal["v0.1"] = "v0.1"
    kind: ScopeKind
    ref: str
    label: str           # human-readable display label
    created_at: datetime
    created_by: str      # user id; "system" for personal:<user_id>
```

### 2. Multi-scope per document

A document can belong to N scopes simultaneously. Chunks, embeddings,
the knowledge-graph projection, the Markdown asset, and the semantic
JSON are all computed **once** per document version and shared across
its scopes; only the scope membership rows differ.

The membership is a join table, not a column on the document:

```sql
CREATE TABLE document_scopes (
    document_id TEXT NOT NULL,
    scope_kind  TEXT NOT NULL,    -- 'personal' | 'swym_community' | 'project'
    scope_ref   TEXT NOT NULL,
    added_at    TIMESTAMPTZ NOT NULL,
    added_by    TEXT NOT NULL,    -- user id
    removed_at  TIMESTAMPTZ,      -- soft-remove flag (no-delete policy, §4)
    PRIMARY KEY (document_id, scope_kind, scope_ref)
);

CREATE INDEX idx_document_scopes_kind_ref
    ON document_scopes (scope_kind, scope_ref);
```

The `removed_at` column is the soft-remove flag (see §4). A row with
`removed_at IS NULL` is an active scope link; a row with a non-null
timestamp is invisible to read paths but preserved on disk for the
future Archive/Purge Admin tool. `add_scope` reactivates a flagged
row by clearing `removed_at` and overwriting `added_at` / `added_by`
with the re-link caller's identity (a re-link is a fresh audit
event); active rows are no-op on re-add (first-write wins).

Index considerations:

- The two query patterns are (a) "list documents in scope X"
  → covered by `idx_document_scopes_kind_ref` on
  `(scope_kind, scope_ref)`; (b) "list scopes for document Y"
  → already covered by the primary key prefix
  `(document_id, ...)`.
- No additional indexes are needed for v1. If the catalog scales past
  the pilot, a covering index on `(scope_kind, scope_ref, document_id,
  added_at)` can be added without a schema migration.

The join row carries `added_at` / `added_by` so the audit trail can
answer "who put this document in this scope, when". The scope link is
itself an auditable event.

### 3. Config is global, not per-scope

HITL routing rules (EPIC-A) and the external-review adapter (EPIC-B)
are **deployment-level** configuration. They are not per-scope. This
matches Q4.5 of the roadmap doc: a single global `KW_HITL_*` config and
a single configured `ReviewApprovalAdapter` (or none) govern routing
for every scope in the deployment.

Reasons:

- The SPC bucket is `(content_type, topic_cluster)`, not scope. The
  same content type and topic cluster behave identically regardless
  of which scope the document lives in.
- Per-scope routing rules would multiply the admin surface
  combinatorially with no offsetting product value.
- A future per-scope override can ride on top of this ADR without a
  data-model change (the override would be a per-scope row that
  shadows the global default), so deferring it costs nothing.

### 4. Flag-only cascade on scope removal (no-delete policy)

When a 3DSwym community is deleted, every `document_scopes` row with
`scope_kind = 'swym_community'` and `scope_ref = <community_id>` is
**soft-removed**: a `removed_at` timestamp is stamped on the row, but
the row itself is preserved. Read paths
(`list_scopes_for_document`, `list_documents_in_scope`) filter out
rows where `removed_at IS NOT NULL`, so the link is invisible to
normal reads while remaining recoverable.

If a document loses **all** of its active scope links — i.e. every
remaining row carries a non-null `removed_at` — the document is
**flagged as archived**, not purged:

- A status `ARCHIVED` (or an `archived_at` column on `documents`,
  TBD by the Archive/Purge Admin tool ADR — see below) replaces the
  current "purge" cascade.
- The original bytes, raw extractions, semantic JSON, and Markdown
  asset **stay in the catalog**.
- The knowledge-graph subgraph (`delete_subgraph_for_version`,
  ADR-012) **may** be cleaned up because the KG is a derived view
  regenerable from the catalog — that is the one explicit exception
  to the no-delete policy.

The KG cleanup is operational housekeeping on a derived index, not
deletion of source data; the Archive/Purge Admin tool's job is to
later finalise (or reverse) the archive flag, deciding whether to
physically purge the source bytes or rehydrate the document.
- A row in the audit event store recording the archive with
  `reason = "all_scopes_removed"`, the original document id, the last
  scope that was soft-removed, and the actor that triggered the
  removal.

The archive flag is **reversible**: a future Archive/Purge Admin tool
(separate ADR, deferred) is the only path to physical deletion or
rehydration. Until then, archived documents keep their bytes,
extractions, semantic JSON, and Markdown asset on disk; only their
visibility on read paths changes.

This shape diverges from the original "hard-delete to honour the
data-deletion expectation of community owners" framing. The trade-off
is intentional: an irreversible cascade scattered across services
makes the audit + reversibility story of a dedicated Archive/Purge
Admin tool harder to build, and risks losing data before a human has
reviewed the archive decision. The Admin tool will close that gap
explicitly with audit + reversibility + an explicit purge action.

The flag-only cascade also applies to `project` scopes: soft-removing
the last `project` link archives the document. Deleting the user
account that owns a `personal` scope follows the same path.

### 5. Read filter on every endpoint

Every list / search / graph / chat / catalog endpoint accepts a scope
filter and returns only documents whose `document_scopes` row set
intersects the user's accessible scope set. The predicate is enforced
**server-side** via FastAPI dependencies; no endpoint trusts a client
to pass the right scope filter.

The user's accessible scope set is computed at request time:

- The `personal:<user_id>` scope of the authenticated user.
- The `swym_community` scopes returned by ADR-026's membership client
  for that user.
- The `project` scopes the user has been added to in the local
  catalog.

This is a uniform refactor across the existing endpoints, not new
architectural debt — every endpoint already accepts a request-scoped
dependency for the authenticated user, so adding a scope filter
predicate is one extra dependency per route.

## Consequences

- **Positive — multi-scope sharing.** A document referenced by N
  scopes is computed, embedded, and projected once. Storage and
  embedding spend scale with unique documents, not with cross-posting.
- **Positive — personal default works on day one.** Every user has a
  private workspace before they have joined any community, so the
  product is usable from the first sign-in.
- **Positive — flag-only cascade preserves auditability.** Owners of
  a 3DSwym community can delete it and the orphaned content becomes
  invisible to reads while remaining recoverable. The Archive/Purge
  Admin tool (deferred ADR) finalises the decision with an explicit,
  audited action.
- **Negative — membership lookups are a hot path.** The scope filter
  fires on every read endpoint. The `swym_community` portion of the
  user's accessible scope set requires a 3DSwym call. ADR-026
  addresses this with per-request memoisation and a circuit breaker;
  see that ADR for the cost analysis.
- **Negative — flag-only cascade keeps storage growing.** Soft-removed
  scope links and archived documents stay on disk until the
  Archive/Purge Admin tool acts on them. For the pilot footprint this
  is a non-issue; at scale the Admin tool's purge action becomes the
  pressure-release valve.
- **Neutral — uniform refactor across endpoints.** Every list /
  search / graph / chat / catalog endpoint must add a scope filter
  predicate. This is mechanical and lands as part of EPIC-D's slices.
- **Neutral — no Phase 3 model change.** Embeddings, chunks, and the
  graph projection are scope-agnostic; only the visibility filter on
  reads changes. No re-embedding is required to adopt scoping.

## Alternatives considered

### Single `workspace_id` per document

The original #91 framing. Rejected because the natural cross-community
case (a document legitimately visible in multiple communities) forces
either a copy-and-re-ingest workflow or a synthetic
"shared-with-multiple" workspace that defeats the isolation the model
is supposed to provide. The single-id shape also forecloses the
personal-default story unless `workspace_id` is made nullable, which
pushes the same N-of-many problem to the next decision.

### Tag-based scoping with no schema

Treat scopes as free-form tags on documents and rely on the audit
trail to guess intent. Rejected because tag matching gives weak
isolation guarantees: a tag typo silently leaks content across
communities, and there is no SQL-level enforcement of the
`(document, scope)` invariant. The flag-only cascade in §4 also
requires a real foreign-key-shaped relationship to be sound.

### Per-scope embedding namespaces

Compute and store embeddings separately per scope so that retrieval is
naturally scope-isolated. Rejected because the storage cost explodes
for shared content (a document in five scopes embeds five times) with
no quality benefit — the embeddings are byte-identical. Filtering
scope-isolated retrieval at query time over a single embedding index
is the correct trade.

## References

- [ADR-026](ADR-026-swym-membership-integration.md) — Swym membership
  live REST integration. Companion ADR; specifies the runtime
  membership lookup that §5's read filter depends on.
- [ADR-012](ADR-012-knowledge-graph-layer.md) — Knowledge graph layer.
  The reconciliation path used by §4's purge cascade.
- [ADR-017](ADR-017-taxonomy-and-ontology.md) — Taxonomy and ontology.
  The taxonomy will become a per-scope object once scoping lands;
  ADR-017 §7 anticipates this.
- [#218](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/218) —
  EPIC-D — Multi-scope ingestion. The parent epic this ADR codifies.
- [#91](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/91) —
  Original workspace framing. Redefined by this ADR.
- [#83](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/83) —
  Auth implementation. Hard prerequisite: the scope filter resolves
  against the authenticated user's identity, so the auth surface must
  land first.
- [#89](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/89) —
  3DEXPERIENCE object links. The `swym_community` scope's reference
  format is shared with the 3DX object-link surface.
- [`docs/roadmap/2026-05-04-hitl-and-extensions.md`](../roadmap/2026-05-04-hitl-and-extensions.md)
  §4 — Source of truth for the decisions codified in this ADR.
