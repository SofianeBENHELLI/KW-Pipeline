# Orbital — KW Pipeline web

Reviewer-facing frontend for the KW Pipeline document intelligence MVP.

Stack: Vite + React 18 + TypeScript, Vitest + @testing-library/react,
react-router-dom. Rationale lives in
[`docs/adr/ADR-004-orbital-frontend-stack.md`](../../docs/adr/ADR-004-orbital-frontend-stack.md).

## Quickstart

Requires Node 22 LTS.

```bash
cd apps/web
npm install
npm run dev        # vite dev server on http://localhost:5173
npm run build      # tsc -b && vite build, emits dist/
npm run preview    # serve the production build locally
npm test           # vitest run (unit + component)
npm run typecheck  # tsc -b --noEmit
```

## Layout

```
apps/web/
├── index.html
├── package.json
├── tsconfig.json
├── tsconfig.node.json
├── vite.config.ts
├── vitest.config.ts
└── src/
    ├── App.tsx
    ├── App.test.tsx
    ├── main.tsx
    └── test-setup.ts
```

## Notes

- React 18 is pinned to maximise compatibility with the testing-library and
  react-router-dom versions we use today. The ADR explains the trade-off.
- Vitest reuses the Vite config, so there is one transform pipeline for dev,
  build, and tests.
- The dev server talks to the FastAPI backend in `apps/api/`. CORS for the
  cross-origin dev call lands in issue #36; until then either run the backend
  in a CORS-permissive dev mode or add a Vite proxy.
- A future Tauri shell can wrap this same build for a native desktop window
  without changing the React app.
