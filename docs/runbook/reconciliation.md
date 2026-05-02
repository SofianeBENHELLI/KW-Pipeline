# Knowledge-layer reconciliation

> Closes #124. Companion docs: ADR-012 §4 (fire-and-log discipline),
> [`docs/architecture/knowledge_layer.md`](../architecture/knowledge_layer.md).

## What this is for

Validation writes to two places:

- **Catalog** — SQLite, source of truth. Updated synchronously.
- **Knowledge graph** — Neo4j (or the in-memory store for demos), a
  fire-and-log side-effect. ADR-012 §4 commits to never rolling back
  validation if projection fails: a transient Neo4j outage or LLM hiccup
  must not block a reviewer.

The drift this creates is intentional. The repair path lives here.

A version is *drifted* when the catalog says `VALIDATED` but the graph
has no `(:Version)` node with that id. Reasons this happens in practice:

- Neo4j was unreachable when validation landed (`KW_KNOWLEDGE_LAYER_ENABLED=true`,
  `KW_NEO4J_URI=…`, but the connection failed).
- The Anthropic SDK 5xx'd during entity extraction (Phase 2).
- A demo run with the in-memory store was restarted, so the graph is
  empty but the SQLite catalog has previously-validated versions.
- A future operator deletes the graph by hand for any reason.

## Tools

The script is `apps/api/scripts/reconcile_knowledge_layer.py` — a
thin CLI over `app.services.knowledge.reconciliation.ReconciliationService`.
Surface choice is deliberate (#124 left it open): there is no auth on
the API today (#83 still open), so an admin HTTP route would be
unguarded. CLI is the safe default. When auth lands, a one-step wrapper
can expose the same service over HTTP.

### Detect

```bash
cd apps/api
KW_KNOWLEDGE_LAYER_ENABLED=true \
KW_NEO4J_URI=bolt://localhost:7687 \
KW_NEO4J_USER=neo4j \
KW_NEO4J_PASSWORD=*** \
KW_PERSISTENT=true \
  python scripts/reconcile_knowledge_layer.py detect
```

Prints a table of every drifted version. Exits **0** even if drift is
present — `detect` is a read-only audit.

### Reconcile one version

```bash
python scripts/reconcile_knowledge_layer.py reconcile DOC_ID VER_ID
```

Re-runs `KnowledgeProjector.project(...)` for the version (delete-then-upsert
so it's idempotent against an already-healthy version), then runs entity
extraction iff Phase 2 is configured (`ANTHROPIC_API_KEY`). Output:

```json
{
  "document_id": "...",
  "version_id": "...",
  "projection_ok": true,
  "entity_extraction_ok": true,
  "error": null
}
```

`entity_extraction_ok` is `null` when Phase 2 isn't wired (no API key);
it's not a failure.

### Reconcile every drifted version

```bash
python scripts/reconcile_knowledge_layer.py reconcile-all
```

Detects drift then walks the list, reconciling each in turn. Continues
on per-version errors so one bad blob doesn't abort the batch.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | No drift, or every reconciliation reported `projection_ok=True` and `entity_extraction_ok in {True, None}`. |
| 1 | Any reconciliation reported a failure, or a sub-command was given invalid arguments (unknown document/version, version not in `VALIDATED`, etc). |
| 2 | The knowledge layer is disabled. Set `KW_KNOWLEDGE_LAYER_ENABLED=true` (and the `KW_NEO4J_*` family for Neo4j) before invoking. |

Suitable for cron / a periodic check that pages on exit ≠ 0.

## Operating notes

- **Run from `apps/api/`.** `KW_PERSISTENT=true` resolves the catalog at
  `.kw-pipeline/catalog.sqlite3` relative to the cwd; running from the
  repo root will create or read the wrong sqlite file.
- **Idempotent.** Calling `reconcile` against a healthy version is a
  no-op (the projector does delete-then-upsert).
- **No catalog mutations.** Reconciliation never touches the catalog.
  If projection fails again, the catalog still reads `VALIDATED` —
  that's by design (catalog stays the source of truth).
- **Concurrent runs are safe.** `KnowledgeProjector` and
  `Neo4jGraphStore` use Cypher merges; two simultaneous reconciles for
  the same version land the same end state.

## Verification

The integration test
[`apps/api/tests/integration/test_reconciliation_integration.py`](../../apps/api/tests/integration/test_reconciliation_integration.py)
covers the happy path against a live Neo4j:

1. Project a healthy baseline.
2. Drop the version's subgraph to simulate the ADR-012 §4 failure mode.
3. Detect drift.
4. Reconcile.
5. Assert drift is gone and the projection is back.

Run:

```bash
docker compose -f docker/docker-compose.yml up -d neo4j
KW_NEO4J_URI=bolt://localhost:7687 \
KW_NEO4J_USER=neo4j \
KW_NEO4J_PASSWORD=test_password_change_me \
  pytest -m integration apps/api/tests/integration/test_reconciliation_integration.py
```

The CI's existing `Backend integration (Neo4j, py3.12)` job runs this
file alongside the rest of the integration suite, so the path stays
green per-PR.

## Future: HTTP admin endpoint

When auth lands (#83), wrap `ReconciliationService` in a small admin
route:

```python
@router.post("/admin/reconcile/{document_id}/{version_id}",
             dependencies=[Depends(require_admin)])
def reconcile(document_id: str, version_id: str) -> ReconciliationOutcome:
    return reconciler.reconcile_version(
        document_id=document_id, version_id=version_id,
    )
```

Service interface is intentionally surface-agnostic; nothing about it
is CLI-specific. The CLI stays useful for cron / oncall tooling
regardless.
