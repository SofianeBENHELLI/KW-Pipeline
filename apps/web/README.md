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
    ├── api/
    │   ├── client.ts            # typed openapi-fetch wrapper
    │   ├── types.ts             # public alias re-exports
    │   └── generated/schema.ts  # generated; do not hand-edit
    ├── domain/
    │   └── document.ts
    ├── features/
    │   ├── pipeline/
    │   │   └── PipelineWidget.tsx
    │   ├── review/
    │   │   └── ReviewWorkspace.tsx
    │   └── graph/
    │       ├── KnowledgeGraphView.tsx   # @neo4j-nvl/react wrapper
    │       └── index.ts
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
- `features/review/` owns the expanded document review workspace
  (the audit surface — the reviewer's home).
- `features/graph/` owns the optional knowledge-graph view
  (`<KnowledgeGraphView />` wrapping `@neo4j-nvl/react`). Mounted in
  the review workspace; renders an empty-state when the document has
  no projection yet (knowledge layer disabled or version not
  validated). See
  [`docs/architecture/knowledge_layer.md`](../../docs/architecture/knowledge_layer.md).
- `fixtures/` provides API-shaped sample data for tests.
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
- `@neo4j-nvl/base` is heavyweight (canvas-based graph layout) — ~2 MB
  raw / ~600 KB gz. The graph slice is wrapped in `React.lazy` (see
  [`src/features/graph/index.tsx`](src/features/graph/index.tsx)) and
  pinned to a dedicated `graph` chunk via `manualChunks` in
  [`vite.config.ts`](vite.config.ts), so reviewers who never open the
  graph tab don't pay the cost.

## Bundle budget

Bundle size is enforced in CI (issue #125). Budgets live in
[`bundle-budgets.json`](bundle-budgets.json) and are checked by
[`scripts/check-bundle-size.mjs`](scripts/check-bundle-size.mjs) after
`vite build`. Each emitted JS chunk in `dist/assets/` is matched against
a pattern; the script fails if any chunk exceeds its gzip budget or if a
`required: true` pattern has no matching asset (likely meaning the
chunk was renamed and the budget needs updating with intent).

Current budgets:

| Pattern | Label | Max gz |
|---|---|---|
| `^index-.*\.js$` | Initial app chunk | 80 KB |
| `^graph-.*\.js$` | Graph vendor (NVL + d3), lazy | 650 KB |
| `^KnowledgeGraphView-.*\.js$` | Graph component, lazy | 80 KB |

Run locally with `npm run build && npm run bundle:check`. The
`vite build` step also writes a treemap to `dist/stats.html` via
[`rollup-plugin-visualizer`](https://github.com/btd/rollup-plugin-visualizer);
in CI the same file is uploaded as the `bundle-visualizer` artifact.

When you legitimately need more headroom, raise the relevant budget in
the same PR that adds the weight, with a one-line note in the PR
description on why.
