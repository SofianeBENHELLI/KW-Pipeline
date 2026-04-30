# ADR-004: Orbital Frontend Stack

## Status

Accepted

## Context

Orbital is the internal reviewer-facing tool that lets a human inspect uploaded
documents, the raw extraction, the schema-validated semantic JSON, and the
generated Markdown, then validate or reject the extraction. It is not a public
product surface. It must run on PC and macOS workstations used by the team
without per-OS installers, and it has to talk to the FastAPI backend in
`apps/api/`.

The constraints that drive this decision:

- **PC + macOS.** The reviewer pool spans both. Anything that needs an OS
  installer or a per-OS build pipeline costs us time we don't want to spend on
  packaging.
- **Internal review tool, not a public site.** No SSR, no SEO, no hydration
  budget, no per-route latency targets. The audience is logged-in reviewers.
- **Backend already in Python/FastAPI.** The frontend talks to that API and
  renders the artifacts the pipeline emits (Markdown, JSON, source bytes).
- **Small team.** We want a stack the existing engineers can run in 10 minutes
  and a CI job that finishes in under a minute on an empty cache.

## Decision

Use **Vite + React 18 + TypeScript** for `apps/web/`, with **Vitest** +
**@testing-library/react** for unit/component tests and **react-router-dom** for
client-side routing.

Rejected alternatives:

- **Next.js.** Buys SSR, file-system routing, image optimization, and a
  production-grade Node server. We don't need any of those for an internal
  review tool, and they pull in App Router constraints, server components, and
  a bigger CI/runtime surface that we'd pay for forever for no reviewer-visible
  benefit.
- **Electron.** Cross-platform native shell, but a browser-served SPA already
  works on PC and macOS without per-OS builds, code signing, or an auto-update
  channel. The cost is real and the win is hypothetical.
- **Tauri.** Lighter than Electron and tempting, but adds a Rust toolchain to
  the build and ties releases to per-OS bundles. We can wrap the Vite build in
  a Tauri shell later if reviewers ever need a native window — the SPA we ship
  today doesn't change.

Why Vitest over Jest: native ESM + Vite config reuse, no Babel/SWC config to
maintain, faster cold start, identical `expect`/`describe`/`it` API.

Why React 18 over React 19: 19 is fine but recent. 18.x has the broadest
testing-library and react-router compatibility today, and we have no React 19
feature on the roadmap that would justify being early adopters on an internal
tool.

## Consequences

- Reviewers run `npm run dev` (or hit a static-hosted build) on either OS with
  no per-platform install steps. CI runs the same `npm ci && npm run typecheck
  && vitest run` everywhere.
- Browser-only initially. If we later want a desktop window with filesystem
  access or offline behavior, we wrap the same Vite build in **Tauri** — the
  React app does not change.
- The frontend talks to `apps/api/` over HTTP from a different origin during
  local dev (Vite on `:5173`, FastAPI on `:8000`). This depends on the backend
  CORS configuration tracked in issue #36; until that lands, dev work uses a
  Vite proxy or a CORS-permissive backend dev mode.
- No SSR means no server-rendered SEO and no per-route Node server, which is
  the correct tradeoff for an authenticated internal tool.
- Choosing Vitest keeps the test runner in the same config graph as the build,
  so a future Storybook or Playwright layer plugs in without a second
  transformer.
