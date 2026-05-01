# ADR-011: Generate Orbital's API client from the FastAPI OpenAPI schema

Status: accepted, 2026-05-01.

## Context

Issue #80 calls for a way to keep Orbital's frontend in lockstep with the
Harvester API contract. Before this change the frontend had a hand-written
`apps/web/src/api/types.ts` mirroring the Pydantic response schemas, and a
hand-rolled `apps/web/src/api/client.ts` that built URLs and called native
`fetch` with `T` parameters chosen by hand. Two failure modes were already
likely:

1. Backend contract changes silently drifting from frontend types.
2. Endpoint additions or rewrites being free to typo path strings, methods,
   path params, or response shapes — with no compile-time signal.

Both get worse as the API grows.

## Decision

Adopt a two-piece codegen pipeline driven from a committed OpenAPI snapshot:

1. **Backend exports a deterministic OpenAPI snapshot.** The script
   `apps/api/scripts/export_openapi.py` builds the FastAPI app and dumps
   `app.openapi()` with `sort_keys=True, indent=2` so the bytes are stable
   across reorderings of routes or model fields. The snapshot is committed
   at `apps/api/openapi.json`. A pytest (`tests/test_openapi_snapshot.py`)
   regenerates in-memory and asserts byte-equality with the committed file.
2. **Frontend generates types from the snapshot.** `openapi-typescript` (v7)
   produces `apps/web/src/api/generated/schema.ts`, also committed. The
   public alias layer at `src/api/types.ts` is a thin set of re-exports
   (`type ApiDocument = components["schemas"]["Document"]`) so feature code
   keeps importing stable names.
3. **`openapi-fetch` (v0.13) replaces the hand-written request helper.**
   Path strings, methods, path parameters, and request/response shapes are
   all enforced at compile time against the generated `paths` interface. The
   public `client.ts` exports keep their original signatures, so consumers
   (`domain/`, `features/`, `ui/`) don't change.
4. **Two CI gates catch drift.** A new `openapi-contract` job in
   `.github/workflows/ci.yml` does two `diff -u` checks: backend snapshot
   vs. live export, and committed `schema.ts` vs. fresh `openapi-typescript`
   regeneration. Either failure is a clear, single-step fix.

## Why this shape, and not the alternatives

- **Generate types only, keep `request<T>`**: rejected as less robust.
  Future endpoint wiring stays typo-prone (path strings and methods are
  string literals with no contract behind them). Once we accepted the
  generated `paths` interface for type safety, leaning on `openapi-fetch`
  to *also* enforce path/method correctness was the obvious win — for one
  more 12 KB dependency and a small refactor of an internal helper.
- **`orval` or `hey-api`**: ships axios, react-query, runtime validators,
  and an opinionated client surface. The KW Pipeline stack is intentionally
  minimal-deps native-fetch; those generators are a culture mismatch.
- **Generate at build time vs. commit `schema.ts`**: committing wins. PR
  reviewers see a real diff for every contract change, the frontend Docker
  image doesn't need Python in scope, and CI gates become trivially correct
  (compare bytes, not behaviors).
- **Snapshot under `docs/contracts/`**: rejected. The snapshot is a build
  output of the FastAPI app; it lives next to the app at `apps/api/openapi.json`.
- **Implicit schema names from FastAPI's defaults**: rejected for ergonomics.
  We set explicit `operation_id` on every route in `apps/api/app/routes.py`
  so the generated `paths` and `operations` keys are readable
  (`list_documents` instead of `list_documents_documents__get`).
- **Pydantic defaults stay optional in generated TypeScript**: rejected as
  an honest-but-painful contract. Pydantic's `Field(default_factory=list)`
  always serializes (possibly empty), but JSON Schema marks the field as
  not required, which became `T[] | undefined` on the wire. Setting
  `model_config = ConfigDict(json_schema_serialization_defaults_required=True)`
  on a shared `APISchemaModel` base flips this for **serialization-mode**
  schemas (response models) only — request bodies keep the looser shape.

## Workflow

When a backend route or response model changes:

```sh
# In apps/api/
python scripts/export_openapi.py        # writes openapi.json

# In apps/web/
npm run openapi:generate                # writes src/api/generated/schema.ts

# Commit both files in the same PR.
```

CI fails fast with a clear message if either file is stale. Detail:
`docs/workflows/openapi_codegen.md`.

## Consequences

- **Net code:** ~250 hand-written lines (export script, snapshot test,
  base schema model, response_model annotations on routes, npm scripts,
  CI job, ADR + workflow doc) plus a generated `schema.ts` (~830 lines).
- **Two new frontend devDeps:** `openapi-typescript`, `openapi-fetch`. Both
  are small and runtime-light (`openapi-fetch` is a thin wrapper over
  `globalThis.fetch`).
- **Backend change of note:** `apps/api/app/schemas/__init__.py` now exposes
  `APISchemaModel`, the base for response-shaped Pydantic models. Existing
  models in `schemas/document.py`, `schemas/extraction.py`,
  `schemas/semantic_document.py` inherit from it.
- **Test impact:** vitest test fixtures now read URL/method/body off the
  `Request` object that `openapi-fetch` passes to `fetch`, instead of the
  `(url, init)` tuple the previous client used.
- **Future work, not in scope:** `mypy`/`pyright` enforcement of return
  types on routes (#44), structured error contract (#97), and request-side
  type generation for forms (multipart upload still bypasses
  `openapi-fetch`).
