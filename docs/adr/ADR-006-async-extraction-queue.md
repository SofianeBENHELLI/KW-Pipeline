# ADR-006: Async Extraction Queue and Failure/Retry Policy

## Status

**Proposed**, 2026-05-07. Resolves decision **D5** ("queue technology") in
the 2026-05-04 backlog restructure
(`docs/roadmap/2026-05-04-backlog-restructure.md` §A.4) and unblocks
issue [#40](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/40)
("Harvester — Async background extraction queue"). The ADR slot was
reserved by the issue body itself and never written; filed now as part
of Sprint S+2 ("auth and queue foundations").

## Context

`POST /documents/{id}/versions/{vid}/extract` and the `retry-extraction`
twin run the parser **inline on the request thread**
(`apps/api/app/routes/lifecycle.py:205` and `:255` →
`ExtractionJobService.extract` /
`ExtractionJobService.retry_extract` →
`ParserRegistry.for_content_type(...).parse(...)`).

For the parsers shipped today this is acceptable on small inputs:

- `PlainTextParser` is sub-second.
- `DocxParser` and `PptxParser` are <2s on typical office documents.
- `PdfParser` (pdfplumber, ADR-010) is the outlier: a 100-page PDF
  takes 10–60s, and an OCR-mode PDF (when issue #47 lands) will take
  multiples of that.

Three concrete failure modes follow from the inline shape:

1. **Reverse-proxy timeouts.** Cloudflared and most cloud load
   balancers cap request lifetimes at 30–100s. A 60s pdfplumber run
   surfaces to the operator as "the extraction failed" when the
   parser actually succeeded — the response just never reached the
   client.
2. **Connection-pool exhaustion under burst.** A demo where five
   reviewers simultaneously trigger extraction on five large PDFs
   pins five worker threads for ~minute-each. New requests (even
   trivial reads on `/documents`) queue behind them.
3. **No retry separation.** Today, a parser crash and a transient
   infrastructure error are indistinguishable on the route side —
   both surface as `ExtractionFailed` and require the operator to
   manually call `retry-extraction`. Once extraction moves to a
   queue, the runtime can offer the operator a clean polling shape
   (`status: EXTRACTING` → terminal status) and reserve the retry
   button for genuine human action.

The current `ExtractionJobService` already encapsulates the lifecycle
FSM transitions (`STORED → EXTRACTING → EXTRACTED|FAILED`), so the
queue layer can wrap it without re-implementing FSM rules.

The audit-followups handover (and the restructure doc §A.4) listed
five plausible queue technologies:

| Tech | Adds out-of-process dep? | Persisted? | Multi-worker? | MVP fit |
|---|---|---|---|---|
| `asyncio.create_task` / `concurrent.futures.ThreadPoolExecutor` (in-process) | No | No | Threads only | ✅ |
| SQLite-as-queue (FOR UPDATE SKIP LOCKED via app-level locks) | No (already a dep) | Yes | Process-local | ⚠️ overkill for one-process MVP |
| Redis (RQ / Arq) | Yes | Yes | Yes | ⚠️ infra footprint |
| NATS JetStream | Yes | Yes | Yes | ❌ infra footprint |
| Postgres-as-queue (advisory locks / `LISTEN`/`NOTIFY`) | Yes (via ADR-022) | Yes | Yes | ❌ blocked on Postgres trajectory |
| Celery | Yes | Yes (broker-dependent) | Yes | ❌ heavy framework, no LangChain-style precedent for "no Celery" but the install graph is large |

The MVP target is a single-tenant single-process demo (`docker run`,
`uvicorn`, sometimes a Cloudflare quick tunnel). Horizontal scale is
not in scope until ADR-022 settles the Postgres persistence
trajectory (decision **D11**).

## Decision

### 1. Tech: in-process bounded `asyncio.Queue` + thread-pool worker

A single `ExtractionWorker` runs as an asyncio task on the FastAPI
event loop. Submission is non-blocking; execution happens off the
event loop via `loop.run_in_executor(thread_pool, ...)` because
`pdfplumber` and `python-docx` are synchronous CPU/IO-blocking calls.

```text
                    ┌────────────────────────────────────────┐
   POST /…/extract  │ route handler                          │
   ──────────────▶  │  - lifecycle FSM: STORED → QUEUED      │
                    │  - submit(version_id) → asyncio.Queue  │
                    │  - return 202 + job snapshot           │
                    └─────────────┬──────────────────────────┘
                                  │
                                  ▼
                    ┌────────────────────────────────────────┐
                    │ ExtractionWorker (asyncio task)         │
                    │  - await queue.get()                    │
                    │  - run_in_executor(pool, run_extract)   │
                    │    → ExtractionJobService.extract(...)  │
                    │  - lifecycle status flips on completion │
                    └────────────────────────────────────────┘
```

**Concurrency knobs** (read from `Settings`):

- `KW_EXTRACTION_QUEUE_SIZE` (default `16`) — bounded `asyncio.Queue`.
  When full, the route returns **503 Service Unavailable** with
  `Retry-After: 5` and a structured envelope; this gives the operator
  immediate backpressure feedback rather than silently buffering.
- `KW_EXTRACTION_WORKERS` (default `1`) — number of worker tasks.
  Default 1 keeps lifecycle FSM transitions linearizable without
  needing per-version locking. Operators can dial up after they
  verify their parser pool is concurrency-safe.
- `KW_EXTRACTION_INLINE` (default `false`) — when `true`, bypass the
  queue and call `ExtractionJobService.extract` synchronously on the
  request thread. The pre-S+2 default. Tests use this. Demos can
  flip it on if the deployment matrix calls for it.

### 2. API change: 202 Accepted with the job snapshot

When `KW_EXTRACTION_INLINE=false`, `POST /documents/{id}/versions/{vid}/extract`
returns **`202 Accepted`** with the queued snapshot rather than the
final `RawExtraction`:

```jsonc
{
  "job_id": "ext-…",                       // opaque, scoped to (document_id, version_id)
  "document_id": "doc-…",
  "version_id": "ver-…",
  "status": "QUEUED_FOR_EXTRACTION",       // matches DocumentVersionStatus enum
  "submitted_at": "2026-05-07T19:31:20Z",
  "queue_position": 3                      // best-effort, "unknown" when KW_EXTRACTION_INLINE=true
}
```

`status` is the canonical `DocumentVersionStatus` enum value
(`QUEUED_FOR_EXTRACTION`, not the abbreviated `QUEUED`) so the schema
shipped to the typed client matches the value the FSM emits — copy
this verbatim into `apps/api/app/schemas/extraction.py` rather than
inventing a separate "queue status" vocabulary.

When `KW_EXTRACTION_INLINE=true`, the route preserves its current
shape: returns `200 OK` with the `RawExtraction` body. This keeps the
route contract backward-compatible for inline-mode consumers (most
notably the integration test suite — see §5).

The polling endpoint is **`GET /documents/{id}`** — the existing
catalog route. The response carries the document plus every
`DocumentVersion`, and the client selects the target version by
`version_id` locally (the same shape `apps/web/src/api/client.ts` uses
today; a per-version polling route is *not* in the API and the typed
client's `getVersion` helper explicitly throws "not implemented"). The
lifecycle status on the matched version is the source of truth
(`STORED → QUEUED_FOR_EXTRACTION → EXTRACTING → EXTRACTED|FAILED`). No
new "jobs" resource is introduced — the version IS the job.

`GET /documents/{id}/versions/{vid}/extraction` continues to 404 until
the version reaches `EXTRACTED`.

> **PR-2 implementation note.** If the polling cadence becomes a hot
> spot (the catalog route is heavier than a per-version probe would
> be), PR-2 may add a thin `GET /documents/{id}/versions/{vid}` route
> that returns just the matching `DocumentVersion`. That's a forward
> add — the catalog-route shape stays canonical for the MVP.

### 3. New lifecycle state: `QUEUED_FOR_EXTRACTION`

The lifecycle FSM in `apps/api/app/models/document.py` already lists
`QUEUED_FOR_EXTRACTION` as a state name in the project description;
this ADR makes it real:

```
STORED → QUEUED_FOR_EXTRACTION → EXTRACTING → EXTRACTED → ENRICHED → NEEDS_REVIEW → VALIDATED|REJECTED
                                       └─→ FAILED ──┐
                          FAILED ──(retry)──────────┘
```

The `STORED → QUEUED_FOR_EXTRACTION` transition happens at submission.
The worker performs the `QUEUED_FOR_EXTRACTION → EXTRACTING` transition
when it dequeues. The remaining transitions are unchanged.

### 4. Retry policy

**Operator-initiated only.** The worker performs zero automatic
retries.

A parser crash, a transient I/O blip, and a misconfigured Voyage key
all surface as `FAILED` with a persisted `failure_reason` (existing
contract from #8 / `mark_failed`). The existing `retry-extraction`
route (`POST /documents/{id}/versions/{vid}/retry-extraction`) is the
sole recovery surface and continues to require `FAILED` as the
precondition (`409` otherwise). On retry, the version is re-enqueued
to the SAME worker pool — retry semantics are identical to a fresh
submission.

Reasoning: automatic retry on parser failure is a footgun. Pdfplumber
crashes on a structurally-broken PDF deterministically; retrying it
ten times produces the same crash, ten audit events, and a confused
operator. Transient infra blips (Voyage rate limit, Neo4j connection
hiccup during projection) DO benefit from retry, but those failure
modes are scoped to the projection / chat surface, not the extraction
parser. The narrow scope of this ADR (parser execution) is where
"retry-on-button" beats "retry-on-error".

A future ADR can introduce automatic retries with exponential backoff
specifically for the projection / embedding write-back path
(`KnowledgeProjector.project_chunks`), where transient API failures
ARE the dominant failure mode and idempotency is established (cache
key is `(model, sha256(text))`).

### 5. Persistence: none in v1

The `asyncio.Queue` is process-local and lives in RAM. **A process
restart drops every queued job.**

Mitigation:

- **Detection.** A small startup hook scans the catalog for versions
  in `EXTRACTING` or `QUEUED_FOR_EXTRACTION` at boot. Any such version
  is flipped to `FAILED` with `failure_reason="extraction interrupted
  by process restart"`. This makes the "stuck-state" visible to the
  operator and re-uses the existing retry surface for recovery.
- **Audit trail preserved.** The catalog and audit-event store are on
  disk (SQLite). Only the in-flight queue is volatile; every
  successful extraction's payload is persisted via
  `CatalogStore.save_raw_extraction` before the success status flip.

Persistence will arrive when ADR-022 (Postgres trajectory) lands — at
that point a `SELECT … FOR UPDATE SKIP LOCKED` queue table on Postgres
becomes the obvious fit, sharing the catalog's connection pool. Deferring
that decision keeps this ADR focused.

### 6. Multi-worker / horizontal scale: deferred

`KW_EXTRACTION_WORKERS > 1` is supported by the same single-process
asyncio model. **Multi-process scale-out is explicitly out of scope.**

When a deployment outgrows one box, the migration path is:

1. Land ADR-022 (Postgres trajectory).
2. Replace the in-process queue with a Postgres-backed queue table
   (the worker code stays — only the `Queue` interface implementation
   changes).
3. Run N worker processes pointed at the same Postgres.

The shape of `ExtractionWorker` deliberately treats the queue as a
`Protocol` so the swap is local.

## Why this shape, not the alternatives

### Why not Celery / Arq / RQ

Each adds a broker dependency (Redis or RabbitMQ), a worker process
lifecycle, and a serialization boundary that we don't need today.
The MVP install graph is already large; the rule of thumb in this
repo (ADR-013 §1, "no LangChain") is to keep the install graph
auditable. Celery + kombu + amqp is ~4 MB of code we don't need to
own when the workload fits in one process.

### Why not SQLite-as-queue today

SQLite-as-queue is a fine pattern but requires app-level locking
(SQLite's `BEGIN EXCLUSIVE` doesn't coexist well with WAL mode and
the catalog's mixed read/write workload). The bounded `asyncio.Queue`
is one Python primitive; a SQLite queue is a small library we'd write
and maintain. When persistence is genuinely required, it almost always
coincides with the Postgres move — write the persistent queue once,
on Postgres, with `SKIP LOCKED`.

### Why not Postgres now

Blocked on ADR-022 (decision D11). Filing the queue ADR before the
persistence-trajectory ADR would lock in a tech that the persistence
decision then has to second-guess. Reversed sequencing.

### Why not NATS / Kafka / SQS

Each is the right answer at a scale we don't have. NATS in particular
is attractive for the "RYA-style fan-out" use case if we ever want
multiple downstream services to react to extraction completion — but
the MVP doesn't have those services. The audit-event store is the
event bus today.

## Consequences

### Positive

- **Reverse-proxy timeouts go away.** The route returns in <50ms;
  long parsers run independently of the HTTP timeout.
- **Backpressure is explicit.** A full queue returns `503` rather
  than silently piling up — the operator sees the back-pressure
  signal directly.
- **Parser improvements are decoupled from request-path latency.**
  When ADR-010 is revisited and we add a slower-but-better PDF
  parser (Docling / Marker), it doesn't widen the HTTP-timeout cliff.
- **Retry semantics stay simple.** "Operator clicks retry" is the
  whole story. No retry-storm worry, no exponential-backoff config to
  tune in the demo posture.
- **Test posture preserved.** `KW_EXTRACTION_INLINE=true` keeps the
  existing 1610-test backend suite synchronous; no test needs to
  await the worker.

### Negative

- **No persistence of in-flight queue.** A `docker restart` between
  enqueue and dequeue costs that one in-flight job. Mitigated by
  the boot-time stuck-state detector (§5) and surfaced via the
  existing retry button. Acceptable at MVP scale; revisit when
  ADR-022 lands.
- **Single-process scale ceiling.** Past ~`KW_EXTRACTION_WORKERS` ×
  `pdfplumber-per-thread-throughput`, the next worker has to wait.
  In practice this is far outside the demo workload (1–10 documents
  per minute peak).
- **API contract change for `extract` route.** `200 OK` with body
  ↔ `202 Accepted` with snapshot is observable to clients. The OpenAPI
  snapshot needs regeneration (per ADR-011); the typed client gets a
  new union. Mitigation: `KW_EXTRACTION_INLINE=true` preserves the
  pre-S+2 shape, and the rollout sequence is "worker first behind a
  flag, flip flag in a follow-up PR" (see §Implementation notes).
- **Status-flip ordering.** Two parallel workers running on the same
  version is impossible (the version is in `EXTRACTING` after the
  first worker grabs it; the FSM rejects a second `EXTRACTING`
  transition). But a process restart between FSM-flip and parser
  start could leave a version stuck in `EXTRACTING` without a worker
  attached — this is the §5 stuck-state case.

### Neutral

- **Per-version cancellation.** Not in this ADR. The straightforward
  shape ("operator wants to cancel an in-flight extraction") is a
  cooperative cancellation token threaded through `ExtractionJobService.extract`,
  and is a small follow-up. The MVP queue is short enough (≤ default
  16) that "wait for it to drain" is acceptable.
- **Per-tenant fairness.** Not in this ADR. Single-tenant MVP; revisit
  when EPIC 2 (#91 workspace scoping) opens the multi-tenant story.

## Implementation notes

The work splits into three small PRs to keep blast radius tight and to
preserve the test posture at every step:

### PR-1 — worker harness behind `KW_EXTRACTION_INLINE=true` default

- New `apps/api/app/services/extraction_worker.py` — `ExtractionWorker`
  asyncio task + bounded `asyncio.Queue`. Pulls jobs and delegates to
  the existing `ExtractionJobService` (no FSM logic duplicated).
- New `Queue` Protocol so the in-memory queue can be swapped for a
  persistent one in a future ADR-022 follow-up without touching the
  worker.
- New `KW_EXTRACTION_INLINE` / `KW_EXTRACTION_QUEUE_SIZE` /
  `KW_EXTRACTION_WORKERS` settings; default `INLINE=true` so this PR
  ships zero behavior change.
- Lifespan hook in `app.main` starts the worker(s) on boot when the
  flag is off, joins on shutdown.
- Stuck-state recovery on boot (§5).
- Tests: queue submission/drain, queue full → 503, stuck-state
  recovery from boot.

### PR-2 — route shape + lifecycle state

- Add `DocumentVersionStatus.QUEUED_FOR_EXTRACTION` to the FSM
  (`apps/api/app/models/document.py`); add `STORED →
  QUEUED_FOR_EXTRACTION → EXTRACTING` to `ALLOWED_TRANSITIONS`.
- New `ExtractionJobSnapshot` schema in
  `apps/api/app/schemas/extraction.py` for the 202 body.
- Route handler in `apps/api/app/routes/lifecycle.py:180-221` returns
  `202` with the snapshot when `KW_EXTRACTION_INLINE=false`, preserves
  current `200`/`RawExtraction` when `true`. Same for
  `retry-extraction`.
- Regenerate `apps/api/openapi.json` and
  `apps/web/src/api/generated/schema.ts` (per ADR-011).
- Tests: 202 contract, polling end-to-end, 503 backpressure, retry
  re-enqueue.

### PR-3 — flip default to `KW_EXTRACTION_INLINE=false`

- One-line settings change.
- Update integration smoke runner + `kw-demo` console script.
- Tests stay on `INLINE=true` via `conftest.py` env override (no test
  semantics change).
- Front-end: `<UploadCard>` and `<ReviewWorkspace>` already poll the
  version's status (existing pattern); confirm the polling cadence
  doesn't hammer the API and add a 1.5s minimum interval if needed.

### CI / sequencing

- PR-1 is purely additive; lands without OpenAPI churn.
- PR-2 carries the OpenAPI bump; remember to run
  `python apps/api/scripts/export_openapi.py` and
  `npm run openapi:generate` in the same commit
  (`project_kw_pipeline_ci.md` rule).
- PR-3 ships once a manual demo confirms the polling UX. Reversible
  by env-var flip, not a code rollback.

## Open questions / explicit non-goals

- **Cancellation.** Not in this ADR. Add as a follow-up if a real
  operator hits a stuck multi-minute extraction with no recourse.
- **Per-tenant queue priority.** Not in this ADR; ties to #91.
- **SSE / long-poll endpoint** for live extraction progress
  (acceptance criterion 3 in #40, marked optional): deferred. The
  existing version-polling shape is sufficient for the MVP UI; SSE
  ladders up after EPIC 2 lands and the chat surface needs the same
  primitive.
- **Automatic retry on transient infra failures.** Out of scope for
  the parser path; in scope for the projection path in a future ADR.

## References

- [Issue #40](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/40)
  — Harvester — Async background extraction queue.
- [Issue #87](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/87)
  — Retry from FAILED (existing recovery surface this ADR preserves).
- `apps/api/app/services/extraction_job_service.py` — current
  inline `ExtractionJobService`; the queue wraps this without
  re-implementing FSM rules.
- `apps/api/app/routes/lifecycle.py:180-271` — current route handler
  for extract / retry.
- `apps/api/app/models/document.py` — lifecycle FSM
  (`DocumentVersionStatus`, `ALLOWED_TRANSITIONS`).
- [ADR-011](ADR-011-openapi-codegen.md) — OpenAPI regeneration
  contract (PR-2 must regenerate `apps/web/src/api/generated/schema.ts`).
- [ADR-022 — Persistence trajectory](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/40)
  *(planned)* — when persistent queue and Postgres land, this ADR's
  `Queue` Protocol gets a second implementation.
- `docs/roadmap/2026-05-04-backlog-restructure.md` §A.4 (D5), §C
  EPIC 3 — sprint context.
