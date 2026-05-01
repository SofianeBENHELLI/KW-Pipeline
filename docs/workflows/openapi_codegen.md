# OpenAPI codegen workflow

The Orbital frontend imports its API types from a snapshot of the Harvester
FastAPI OpenAPI schema. Two committed files anchor the contract:

- `apps/api/openapi.json` — deterministic dump of `app.openapi()`.
- `apps/web/src/api/generated/schema.ts` — TypeScript types generated from
  that JSON via `openapi-typescript`.

CI fails any PR where either file is out of sync. Background and rationale
in [ADR-011](../adr/ADR-011-openapi-codegen.md).

## When to regenerate

Any time you change the HTTP-facing surface in `apps/api/`:

- New, removed, or renamed routes in `apps/api/app/routes.py`.
- New or changed `operation_id`, `response_model`, or `responses` on a route.
- Field added/removed/renamed on a Pydantic model in
  `apps/api/app/schemas/*.py`.
- Type changes (e.g. `str` → `Literal[...]`, list element type).

Changes to private services, persistence, or the FSM that don't touch the
HTTP contract do **not** require regeneration.

## Local workflow

From `apps/api/` (regenerate the snapshot):

```sh
python scripts/export_openapi.py
```

From `apps/web/` (regenerate the TypeScript types):

```sh
npm run openapi:generate
```

Or do both back-to-back from `apps/web/`:

```sh
npm run openapi:export-backend && npm run openapi:generate
```

Commit `apps/api/openapi.json` and `apps/web/src/api/generated/schema.ts`
together. Verify locally before pushing:

```sh
# from apps/web/
npm run openapi:check       # diff committed schema.ts vs fresh generation
npm run typecheck           # types.ts and client.ts compile against schema.ts
npm run test                # client tests + App tests pass

# from apps/api/
pytest tests/test_openapi_snapshot.py
```

## Adding a new endpoint

1. Add the route in `apps/api/app/routes.py`. Always set:
   - An explicit `operation_id` (used as the TypeScript key — keep it
     snake_case and stable; renames are breaking changes).
   - `response_model=...` for JSON responses, or a `responses={...}`
     entry declaring the content-type for non-JSON responses
     (see `get_markdown` for the `text/markdown` pattern).
2. If the response shape is new, define the Pydantic model in
   `apps/api/app/schemas/`. Inherit from
   `app.schemas.APISchemaModel` so default-having fields (e.g.
   `Field(default_factory=list)`) are marked required in the
   serialization-mode JSON Schema. Without this, `T[]` becomes
   `T[] | undefined` in the generated TypeScript.
3. Add a test in `apps/api/tests/` for the new behavior.
4. Regenerate per the local workflow above.
5. Add the corresponding helper to `apps/web/src/api/client.ts`. Use
   `http.GET("/path/{param}", { params: { path: { param } } })` etc. —
   path strings and parameter names are validated against the generated
   `paths` interface at compile time.
6. Add a test in `apps/web/src/api/client.test.ts` mocking
   `globalThis.fetch`. Note that `openapi-fetch` invokes `fetch` with a
   `Request` object — read URL/method/body via `request.url`,
   `request.method`, `request.clone().json()`.

## CI gates

The `openapi-contract` job in `.github/workflows/ci.yml` enforces two diffs:

1. **Backend snapshot drift.** Reinstalls `apps/api`, runs
   `scripts/export_openapi.py`, and `diff -u`s the result against the
   committed `apps/api/openapi.json`. Failure means a backend route or
   schema changed without regenerating the snapshot.
2. **Frontend type drift.** Runs `npm run openapi:check` (which is
   `openapi-typescript ... && diff -u`) against the committed
   `apps/web/src/api/generated/schema.ts`. Failure means the snapshot
   changed but the TypeScript wasn't regenerated.

The backend test suite also runs `tests/test_openapi_snapshot.py` as a
redundant safety net for backend-only PRs.

When a CI gate fails, the error message names the exact command to run.
Run it locally, commit the resulting file, push.

## Notes

- The base URL for `openapi-fetch` is read from `VITE_API_BASE_URL` at build
  time, with `http://localhost:8000` as the local-dev default — same as the
  previous client.
- `uploadDocument` bypasses `openapi-fetch` because the `multipart/form-data`
  body shape doesn't fit cleanly into its typed body helpers. Path and
  response are still pinned via the generated `ApiUploadResponse` type.
- The Pydantic-side knob that makes the contract honest about
  always-present-but-default-having fields is
  `model_config = ConfigDict(json_schema_serialization_defaults_required=True)`,
  set on `APISchemaModel` in `apps/api/app/schemas/__init__.py`.
