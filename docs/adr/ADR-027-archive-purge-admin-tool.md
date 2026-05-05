# ADR-027: Archive/Purge Admin Tool — HTTP Surface for the Only Sanctioned Deletion Path

## Status

**Proposed**, 2026-05-05. Codifies the design of the dedicated admin
tool that ADR-020 §4 (rewritten in
[#262](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/262))
deferred. Closes the EPIC-D
([#218](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/218))
D.9 design slot for the no-delete policy's pressure-release valve.

## Context

The no-delete policy ([#262](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/262))
prohibits scattered hard deletes across services. Documents and
`document_scopes` rows can only be flagged (`removed_at`,
`archived_at`); read paths filter the flagged rows out, but the
rows themselves stay on disk until an explicit, audited admin
action finalises (or reverses) the decision. ADR-020 §4 commits to
that shape and points forward to a dedicated tool — this ADR is
that tool.

Without a sanctioned tool, two failure modes pile up:

1. **Storage growth without a release valve.** Soft-removed scope
   links and archived documents accumulate forever. The pilot
   footprint absorbs this; production does not.
2. **Hard deletes leak back in.** Every service that wants to
   "just clean up" its own state risks reintroducing the
   scattered-delete pattern the no-delete policy was written to
   prevent. A single sanctioned code path closes that door.

The Archive/Purge Admin tool is the **only** path to physical
deletion or rehydration of source data. Centralising the deletion
contract in one set of HTTP routes, gated by the admin role and
audited end-to-end, keeps the rest of the codebase honest.

## Decision

### 1. Three admin actions

The tool exposes three admin-only HTTP routes, all under
`/admin/archive/`. Every route requires the `admin` role
([ADR-019 §4](ADR-019-authentication-and-authorization.md))
enforced by the `@require_role("admin")` dependency from
[#264](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/264).

#### 1.1 `unarchive` — clear `documents.archived_at`

Reverses the D.6 flag-archive cascade. The document reappears on
read paths.

```http
POST /admin/archive/unarchive?confirm=true
Authorization: Bearer <admin>
Content-Type: application/json

{"document_id": "doc-abc-123"}

200 OK
{
  "document_id": "doc-abc-123",
  "archived_at_before": "2026-05-04T12:34:56Z",
  "unarchived_at": "2026-05-05T09:12:00Z"
}
```

- **Audit event**: `admin.document.unarchived` with fields
  `{document_id, archived_at_before, unarchived_at, actor}`.
- **Side effects**: `documents.archived_at` is cleared; no other
  state changes. Bytes, extractions, semantic JSON, Markdown asset,
  and KG nodes are untouched (preserved by the flag-only cascade).
- **Idempotency**: calling on an already-unarchived document
  returns 409 with `{"detail": "document_not_archived"}`.

#### 1.2 `relink_scope` — reactivate a soft-removed scope link

Reverses the [#262](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/262)
soft-remove on a `document_scopes` row by clearing `removed_at`
and overwriting `added_at` / `added_by` with the admin actor.

```http
POST /admin/archive/relink_scope?confirm=true
Authorization: Bearer <admin>
Content-Type: application/json

{
  "document_id": "doc-abc-123",
  "scope_kind": "swym_community",
  "scope_ref": "swym-community-42"
}

200 OK
{
  "document_id": "doc-abc-123",
  "scope_kind": "swym_community",
  "scope_ref": "swym-community-42",
  "removed_at_before": "2026-05-04T12:34:56Z",
  "relinked_at": "2026-05-05T09:12:00Z"
}
```

- **Audit event**: `admin.scope_link.relinked` with fields
  `{document_id, scope_kind, scope_ref, removed_at_before,
  relinked_at, actor}`.
- **Side effects**: the targeted `document_scopes` row has
  `removed_at` cleared. The matching `documents.archived_at` is
  **also** cleared if reactivating this link gives the document at
  least one active scope link again — so the read path sees the
  document immediately.
- **Idempotency**: re-linking an already-active row returns 409
  with `{"detail": "scope_link_already_active"}`.

#### 1.3 `purge_artifacts` — irreversible bytes deletion

Hard-deletes the document's source artifacts. The catalog row
stays in the DB as an audit trace (see §3); only the bytes /
extractions / semantic JSON / Markdown asset / KG nodes are
physically removed.

```http
POST /admin/archive/purge_artifacts?confirm=true
Authorization: Bearer <admin>
Content-Type: application/json

{"document_id": "doc-abc-123"}

200 OK
{
  "document_id": "doc-abc-123",
  "purged_at": "2026-05-05T09:12:00Z",
  "bytes_freed": 4823104,
  "versions_purged": ["ver-1", "ver-2"],
  "kg_nodes_dropped": 137,
  "scope_links_preserved": 2
}
```

- **Audit event**: `admin.document.purged` with fields
  `{document_id, purged_at, bytes_freed, versions_purged,
  kg_nodes_dropped, actor}`.
- **Side effects**: for every version, `StorageService.delete(uri)`
  drops the bytes; raw extractions, semantic JSON, and the Markdown
  asset are deleted via the same storage Protocol; KG subgraphs are
  dropped via ADR-012's `delete_subgraph_for_version`. Every
  version's status flips to `PURGED` (§3). The catalog row,
  `document_scopes` rows, and audit log are preserved.
- **Pre-condition**: the document MUST already be archived
  (`documents.archived_at IS NOT NULL`). Purging a non-archived
  document returns 409 with `{"detail": "document_not_archived"}`.
  This forces archive-then-purge as the ordered ritual and gives
  operators a chance to reverse via `unarchive` before bytes go.

### 2. Dry-run on every mutating route

Every mutating route accepts `?dry_run=true`. A dry-run returns
the **impact summary** — exactly what the action would change —
without performing any state mutation. **No audit row is written
for a dry-run**; the audit log records only actual actions.

```http
POST /admin/archive/purge_artifacts?dry_run=true
Authorization: Bearer <admin>
Content-Type: application/json

{"document_id": "doc-abc-123"}

200 OK
{
  "dry_run": true,
  "would_purge": {
    "document_id": "doc-abc-123",
    "bytes_freed": 4823104,
    "versions_purged": ["ver-1", "ver-2"],
    "kg_nodes_dropped": 137,
    "scope_links_preserved": 2,
    "audit_event_that_would_be_written": "admin.document.purged"
  }
}
```

The impact summary lists, depending on the action:

- **`unarchive`** — the `archived_at` value that would be cleared.
- **`relink_scope`** — the `removed_at` value that would be
  cleared, whether the parent document's `archived_at` would also
  clear, and the projected new size of the document's active scope
  set.
- **`purge_artifacts`** — bytes that would be freed, list of
  version ids that would flip to `PURGED`, count of KG nodes that
  would drop, and count of `document_scopes` rows that would be
  preserved.

`?dry_run=true` and `?confirm=true` are mutually exclusive: a
dry-run does not need confirmation because it does not mutate.
Passing both returns 400 with
`{"detail": "dry_run_and_confirm_are_exclusive"}`.

### 3. Catalog row preserved; `PURGED` status on versions

`purge_artifacts` does **not** delete the `documents` row or the
`document_versions` rows. Instead:

- Every `document_versions` row for the purged document flips its
  `status` to a new terminal value `PURGED`.
- The `storage_uri` on every purged version is overwritten with a
  tombstone marker:
  `tombstone:purged:<document_id>:<version_id>:<purged_at_iso>`.
  The tombstone is parseable so audit tooling can recover context
  without joining against the audit log; it is also obviously not
  a real URI, so any storage backend that accidentally receives it
  fails the standard "not found" path rather than fetching
  unrelated bytes.
- `documents` keeps its row, its `archived_at` timestamp, and its
  `document_scopes` rows.

A document is conveyed as "purged" through every one of its
versions carrying `status = PURGED`. There is no separate
`Document`-level purged status; the convention mirrors how the
catalog already expresses lifecycle through the version FSM
(ADR-025 §1).

Read endpoints respond to a fetch of a purged document or version
with HTTP **410 Gone** — not 404. The distinction matters: 404
means "never existed," and consumers downgrade to a generic
"document-not-found" message; 410 means "existed and was
intentionally purged," and the same consumers can render a
tombstone card with the `purged_at` timestamp pulled from the
catalog row. The 410 body shape is:

```json
{
  "detail": "document_purged",
  "document_id": "doc-abc-123",
  "purged_at": "2026-05-05T09:12:00Z"
}
```

The migration that lands `purge_artifacts` adds:

- `PURGED` to the `DocumentVersionStatus` enum.
- The `PURGED` key to every exhaustive
  `Record<DocumentVersionStatus, …>` literal on the frontend, per
  the codebase's exhaustive-record CI trap.
- `PURGED` to the version-status histogram surfaced by the
  catalog's metrics endpoint.

`PURGED` is **terminal**. The FSM has no outgoing edges from
`PURGED`. The single incoming edge is `* → PURGED`, fired only by
`purge_artifacts`; no other code path may write `PURGED`.

### 4. Bulk — `purge_batch`

A bulk wrapper accepts up to **100 `document_id`s** per request
and applies `purge_artifacts` on each, best-effort.

```http
POST /admin/archive/purge_batch?confirm=true
Authorization: Bearer <admin>
Content-Type: application/json

{"document_ids": ["doc-1", "doc-2", "doc-3"]}

200 OK
{
  "results": [
    {"document_id": "doc-1", "status": "purged",
     "bytes_freed": 1024, "versions_purged": ["ver-1"]},
    {"document_id": "doc-2", "status": "purged",
     "bytes_freed": 2048, "versions_purged": ["ver-1"]},
    {"document_id": "doc-3", "status": "error",
     "detail": "document_not_archived"}
  ],
  "summary": {
    "purged_count": 2,
    "error_count": 1,
    "total_bytes_freed": 3072
  }
}
```

- **Best-effort**: a failure on one doc does not abort the batch.
  The per-doc error is returned in `results[i].detail`.
- **One audit row per affected doc** — never a single batch-level
  audit row. Each successful purge writes its own
  `admin.document.purged` event so the audit log remains queryable
  per document.
- **Dry-run is symmetric**: `?dry_run=true` returns the impact
  summary for every doc in the list, with no state changes.
- **Cap**: lists longer than 100 return 400 with
  `{"detail": "batch_too_large", "max_per_request": 100}`.
  Chaining multiple calls is the documented escape hatch.
- The bulk route exists only for `purge_artifacts`. Bulk
  `unarchive` and bulk `relink_scope` are deferred until a
  concrete operator workflow asks for them.

### 5. Auth, role gating, and `?confirm=true`

Every mutating route is gated by the `admin` role
([ADR-019 §4](ADR-019-authentication-and-authorization.md)) via
the `@require_role("admin")` dependency from
[#264](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/264).
The audit row records:

- `actor` — the authenticated `current_user.id`.
- `actor_role` — pinned at `admin`; recorded redundantly so a
  future role change cannot retroactively obscure who did what.
- `request_id` — the request-scoped id propagated by FastAPI
  middleware; lets the audit log join against the access log.

In addition to role gating, every non-dry-run mutating route
requires the explicit query parameter `?confirm=true`. A request
that omits `confirm=true` (and is not a dry-run) returns 400 with
`{"detail": "missing_confirm"}`. This is defence in depth — a
curl typo or a misconfigured admin UI does not silently mutate
state.

### 6. Reversibility envelope

Every action except `purge_artifacts` is fully reversible. The
audit trail records the pre-action state in enough detail to
reconstruct it (`archived_at_before`, `removed_at_before`).

`purge_artifacts` pre-state is recoverable from the audit trail
**only until the bytes themselves are deleted**. Once
`StorageService.delete(uri)` runs successfully, the bytes are
gone; the audit row records what was purged but cannot rehydrate
what is no longer on disk. This is the deliberate point of no
return. Keeping the audit row's shape rich enough (storage URI,
version id, document id) preserves the option of a future
bytes-recovery slice if a deployment adds an off-system snapshot
adapter; this ADR does not specify that recovery slice.

### 7. Storage layer contract — `StorageService.delete()`

`purge_artifacts` requires a deletion primitive on the storage
layer. The `StorageService` Protocol gains a
`delete(uri: str) -> None` method:

```python
class StorageService(Protocol):
    def put(self, uri: str, blob: bytes) -> None: ...
    def get(self, uri: str) -> bytes: ...
    def delete(self, uri: str) -> None:
        """Delete the object at uri. Best-effort + idempotent:
        deleting a missing object is not an error."""
```

- **Best-effort + idempotent.** Deleting a URI that does not
  exist returns silently. The motivating case is a partial prior
  purge that left the catalog out of sync with the storage
  backend — the retry must converge, not fail.
- **In-memory and filesystem implementations** land alongside
  the Protocol change (dict `pop`, `os.unlink(missing_ok=True)`).
- **The future S3 implementation** mirrors the contract via
  `delete_object`, which is already idempotent on S3's side.
- **No hierarchical deletion in v1.** `delete(uri)` deletes a
  single object. Purging a document fans out to N storage URIs
  called individually; the orchestration lives in the admin tool,
  not the storage layer.

## Consequences

- **Positive — clear single boundary for deletion.** Every
  physical delete of source data flows through this tool. The
  rest of the codebase keeps the no-delete invariant; auditors
  have one set of routes to reason about.
- **Positive — fully audited.** Every mutating action writes an
  audit event with actor, request_id, and pre-action state.
- **Positive — reversible up to the bytes.** `unarchive` and
  `relink_scope` are fully reversible; `purge_artifacts` is
  reversible up to the moment the bytes leave disk. Multiple
  safety stops (archive flag, then dry-run, then `?confirm=true`)
  precede the irreversible step.
- **Positive — `PURGED` + 410 Gone is a queryable contract.**
  Read consumers distinguish "never existed" from "purged"
  without guessing. The version-status histogram makes purge
  volume observable.
- **Negative — catalog rows accumulate forever.** Even after
  purge, `documents` and `document_versions` rows stay. For the
  pilot footprint this is negligible; at scale, a separate vacuum
  tool that deletes ancient `PURGED` rows will eventually be
  needed (out of scope here per the no-delete policy).
- **Negative — admin tool is sharp.** `purge_artifacts` is by
  design irreversible past a point. Mitigations: the
  archive-then-purge pre-condition (§1.3), `?dry_run=true`,
  `?confirm=true`, and the admin-role gate.
- **Neutral — thin orchestrator.** The admin tool itself adds
  little new logic: it composes existing flag/storage methods
  behind admin-role-gated HTTP routes. The complexity it
  codifies is contractual (audit shape, tombstone shape, error
  envelopes), not algorithmic.

## Alternatives considered

### Separate CLI tool

Ship a `kw-admin` CLI that talks directly to the catalog and
storage layers, bypassing the API. Rejected per the user
direction on 2026-05-05: keeping the admin surface uniform with
the rest of the API (HTTP, audited, role-gated) is more valuable
than the slightly tighter coupling a CLI would offer. A CLI also
forks the audit and auth story — one HTTP surface, one auth
model, one audit shape.

### Auto-purge after N days of being archived

Add a background job that purges any document archived for more
than N days. Rejected because it removes the human-in-the-loop
for an irreversible action — the whole point of the flag-only
cascade is that a human reviews the archive decision before
bytes go. Auto-purge would also create a quiet liability surface
(silent data loss tied to a config knob), which is exactly the
failure mode the no-delete policy was written to avoid.

### Preserve bytes forever

Skip `purge_artifacts` entirely and treat the archive flag as
the final state. Rejected because it defeats the purpose of
having a deletion path at all: storage liability accumulates
without bound, and there is no answer to legitimate deletion
requests (e.g. a 3DSwym community owner who insists their
content actually leave disk).

### Nuclear purge (catalog row deletion)

Extend `purge_artifacts` to also delete the `documents` and
`document_versions` rows. **Explicitly out of scope** per the
user direction on 2026-05-05. The catalog row is the audit
trace — deleting it removes the only on-disk evidence the
document ever existed, and the 410 Gone read response would
degrade to 404, losing the "was purged vs. never existed"
distinction.

## Implementation slicing

Implementation is **not** in scope of this ADR. The slices below
file as separate issues so each lands as an independently
reviewable PR:

- **Slice 1** — `unarchive` route + audit event +
  `?confirm=true` + `?dry_run=true` + tests.
- **Slice 2** — `relink_scope` route + audit event + tests.
- **Slice 3** — `StorageService.delete()` Protocol + in-memory
  and filesystem implementations + tests.
- **Slice 4** — `purge_artifacts` route + dry-run + audit event
  + `PURGED` enum migration + version-status histogram update +
  exhaustive-Record updates on the frontend + tests.
- **Slice 5** — `purge_batch` bulk wrapper + tests (covers the
  partial-failure case and the 100-cap).
- **Slice 6** — Tombstone storage URI shape + 410 Gone read
  response on the document and version fetch endpoints + tests.

Slices 1–3 can land in any order; slice 4 depends on slice 3;
slice 5 depends on slice 4; slice 6 can land in parallel with
slice 4.

## References

- [#262](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/262) —
  No-delete policy. Upstream constraint that motivates this ADR.
- [#218](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/218) —
  EPIC-D — Multi-scope ingestion. Parent epic; this ADR is the
  D.9 design slot.
- [ADR-020](ADR-020-workspace-scoping.md) §4 — Flag-only cascade
  contract. Source of the archive flag this tool finalises.
- [ADR-019](ADR-019-authentication-and-authorization.md) §4 —
  Auth and role model. The `admin` role gates every endpoint
  defined here.
- [ADR-012](ADR-012-knowledge-graph-layer.md) — Knowledge graph
  layer. Source of `delete_subgraph_for_version`, called during
  `purge_artifacts`.
- [ADR-025](ADR-025-document-similarity-and-supersede.md) §1 —
  Terminal `DocumentVersionStatus` precedent. `PURGED` follows
  the same terminal-status convention as `SUPERSEDED`.
- [#264](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/264)
  — `@require_role(...)` enforcement. Concrete dependency these
  routes attach to.
- [`docs/roadmap/2026-05-04-hitl-and-extensions.md`](../roadmap/2026-05-04-hitl-and-extensions.md)
  §4.6 — `flag_archive()` pseudocode. Source of the archive
  shape this ADR finalises.
