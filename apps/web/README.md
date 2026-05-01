# Orbital — KW Pipeline web

Reviewer-facing frontend for the KW Pipeline document intelligence MVP.

Stack: Vite + React 18 + TypeScript, Vitest + @testing-library/react,
react-router-dom. Rationale lives in
[`docs/adr/ADR-004-orbital-frontend-stack.md`](../../docs/adr/ADR-004-orbital-frontend-stack.md).

The UI is intentionally an operational workbench, not a fancy standalone app.
It should be able to run first as a normal web app and later as a compact
3DEXPERIENCE-compatible widget. UX direction lives in
[`docs/architecture/orbital_widget_ux.md`](../../docs/architecture/orbital_widget_ux.md).

## Quickstart

Requires Node 22 LTS.

```bash
cd apps/web
npm install
npm run dev               # vite dev server on http://localhost:5173
npm run build             # tsc -b && vite build, emits dist/
npm run preview           # serve the production build locally
npm test                  # vitest run (unit + component)
npm run typecheck         # tsc -b --noEmit

# OpenAPI codegen — see docs/workflows/openapi_codegen.md
npm run openapi:export-backend   # regenerate apps/api/openapi.json (needs Python)
npm run openapi:generate         # regenerate src/api/generated/schema.ts
npm run openapi:check            # CI-style staleness check
```

## Generated API types

`src/api/generated/schema.ts` is generated from `apps/api/openapi.json`
by `openapi-typescript`. Do not hand-edit it. The public alias layer at
`src/api/types.ts` re-exports stable names (`ApiDocument`, etc.); feature
code imports from there. The fetch client in `src/api/client.ts` is a
thin layer over [`openapi-fetch`](https://openapi-ts.dev/openapi-fetch),
which compile-time-checks paths, methods, path params, and response
shapes against the generated `paths` interface.

When the backend contract changes, regenerate both files and commit them
together. CI fails if either is stale. See
[`docs/workflows/openapi_codegen.md`](../../docs/workflows/openapi_codegen.md)
for the full workflow.

## Layout

```
apps/web/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── vitest.config.ts
└── src/
    ├── App.tsx
    ├── App.test.tsx
    ├── domain/
    │   └── document.ts
    ├── features/
    │   ├── pipeline/
    │   │   └── PipelineWidget.tsx
    │   └── review/
    │       └── ReviewWorkspace.tsx
    ├── fixtures/
    │   └── sampleDocuments.ts
    ├── main.tsx
    ├── styles.css
    ├── test-setup.ts
    └── ui/
        └── StatusBadge.tsx
```

## Frontend Slices

- `domain/` mirrors backend API shapes and lifecycle statuses.
- `features/pipeline/` owns the compact dashboard widget experience.
- `features/review/` owns the expanded document review workspace.
- `fixtures/` provides API-shaped sample data until live HTTP hooks land.
- `ui/` contains shared presentation primitives such as status badges.

## Notes

- React 18 is pinned to maximise compatibility with the testing-library and
  react-router-dom versions we use today. The ADR explains the trade-off.
- Vitest reuses the Vite config, so there is one transform pipeline for dev,
  build, and tests.
- The dev server talks to the FastAPI backend in `apps/api/`. Add
  `http://localhost:5173` or `http://127.0.0.1:5173` to
  `CORS_ALLOWED_ORIGINS` when running the API locally.
- A future Tauri shell can wrap this same build for a native desktop window
  without changing the React app.
- Keep branding behind a small theme layer so official 3DEXPERIENCE /
  Dassault Systemes tokens can replace local defaults later.
