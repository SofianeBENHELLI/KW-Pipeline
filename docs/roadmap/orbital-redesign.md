# Orbital Redesign — Hi-Fi Mockup → Production

## 1. Context

Orbital ([apps/web](../../apps/web)) is the reviewer/admin workbench. The current implementation predates a proper design system: components are styled in a single `styles.css`, no theming, no density modes, ad-hoc semantic colors, and the visual hierarchy doesn't match how reviewers actually scan documents (per-page chunks dominate; lifecycle state is buried).

A hi-fi prototype was delivered as `Orbital Knowledge.zip` (extracted to `/tmp/orbital-mockup/`). It covers **every one of the 30 features** catalogued in [`docs/wiki/Orbital-Features.md`](../wiki/Orbital-Features.md) and includes a complete design-token system in [`tokens.css`](../../docs/wiki/Orbital-Features.md) (light + dark + 3 density modes), atomic primitives, and three review-workspace aesthetic variants. The `.design-canvas.state.json` hides variants B (Terminal) and C (Editorial); **variant A (Studio — Linear/Vercel-style dense)** is the chosen direction.

This document is the plan to **fully replace** Orbital's current UI with this design, with zero backend changes and minimal disruption to the demo phase.

## 2. Scope

**In scope:**
- Complete rewrite of every visual surface in `apps/web/src/` — components, layout, styles, theming.
- New `tokens.css` design system; remove the legacy `styles.css`.
- Keep React 19 + Vite + TypeScript + `openapi-fetch` + `@neo4j-nvl/react`.
- Keep `apps/_shared/` (api-core, auth, demo-toggle, settings-hub) untouched.
- Keep every existing API endpoint contract — **no backend changes**.

**Out of scope:**
- Backend / FSM / pipeline changes ([memory: stability > simplification](file:///Users/sxz/.claude/projects/-Users-sxz-code-kw-pipeline/memory/feedback_stability_priority.md)).
- The other two frontends — [Explorer](../../apps/explorer), [Widget](../../apps/widget) — stay on their current design ([memory: three audiences](file:///Users/sxz/.claude/projects/-Users-sxz-code-kw-pipeline/memory/project_three_frontends.md)).
- New features beyond the 30-feature catalog. Redesign only.
- Migration of `apps/_shared` to the new tokens — defer until after Orbital lands.

## 3. Goal & non-goals

**Goal:** every reviewer flow that works today still works after cutover, with a faster, denser, more operator-grade UI. Same URL, same endpoints, same OpenAPI types.

**Non-goals:** no Tailwind, no styled-components, no design-system library (Radix is fine for unstyled primitives if needed but no MUI/Chakra/Mantine). The mockup is intentionally low-dependency CSS-variables-only; the rewrite mirrors that.

## 4. What stays vs what gets rebuilt

| Stays as-is | Gets rebuilt |
|---|---|
| [apps/web/package.json](../../apps/web/package.json) deps (React, Vite, openapi-fetch, neo4j-nvl, vitest) | Every `.tsx` under `src/features/` and `src/ui/` |
| [apps/web/src/api/](../../apps/web/src/api) (generated schema + fetch client) | `src/App.tsx` layout shell |
| [apps/web/src/domain/](../../apps/web/src/domain) (pure helpers: `latestVersion`, `documentScopes`, FSM transitions) | `src/styles.css` → split into `src/styles/tokens.css` + per-feature `*.module.css` (or plain CSS scoped via `.orb-*` namespace) |
| [apps/_shared/](../../apps/_shared) — auth, retry, demo-toggle, settings-hub | `src/main.tsx` (only the bootstrap CSS imports) |
| Vite + ESLint + TS config | — |
| Test harness (`test-setup.ts`, vitest + RTL + axe-core) | Test files are rewritten alongside the components they cover; the harness stays |
| [scripts/check-bundle-size.mjs](../../apps/web/scripts/check-bundle-size.mjs) | — |

## 5. Approach — long-lived feature branch + parallel preview deploy

We do the rewrite on a **dedicated branch** `orbital-redesign`, with preview builds shipped to a **separate S3 prefix** so the demo backend at `https://kw-api.benhelli.org` stays pointed at the current Orbital until cutover.

```
production today:  s3://3dx-kwforge-widgets/3dx-knowledge-orbital/v0.0.0/
preview during    s3://3dx-kwforge-widgets/3dx-knowledge-orbital-next/<sha>/
   the rewrite:
cutover day:       rewrite published to .../3dx-knowledge-orbital/v0.1.0/
                   demo.html `orbital` URL flipped via deploy-orbital.sh
                   old bundle archived at .../3dx-knowledge-orbital/v0.0.0/ (kept for rollback)
```

Why this shape:

- **Zero disruption to the demo phase.** The live Orbital URL doesn't change until we're ready.
- **Reviewable in small PRs.** Each phase below lands as its own PR onto `orbital-redesign`, not main.
- **Easy rollback.** If cutover reveals an issue, redeploy the v0.0.0 bundle — no code revert.
- **Same deploy script.** [scripts/deploy-orbital.sh](../../scripts/deploy-orbital.sh) already takes a version arg; we add an env var (`KW_ORBITAL_PREVIEW=true`) that retargets the prefix.

**Alternative considered and rejected:** a parallel `apps/web-next/` directory. Adds duplicate Vite/eslint/tsconfig, doubles CI cost, and the lift to keep `apps/_shared` consumers in sync isn't worth it for a single-frontend redesign.

## 6. Phasing — 8 phases, each independently shippable

Each phase lands as one PR onto `orbital-redesign`. The branch is preview-deployable after every phase — no half-broken UI ever sits on the branch.

### Phase 0 · Design system foundation

- Create [`apps/web/src/styles/tokens.css`](../../apps/web/src/styles/tokens.css) verbatim from the mockup (the CSS variables, `[data-theme]` selectors, density modes, scrollbar, `.orb-*` atoms).
- Import it in `main.tsx` instead of `styles.css`.
- Build the atom primitives in `src/ui/` as TSX:
  - `StatusBadge`, `ScopeChip`, `Btn`, `Input`, `Kbd`, `Chip`, `MetaRow`, `Card`, `Rule`, `SectionHeading`, `Icon` (the 22 SVG icons from `orbital-shared.jsx`).
  - Each atom is a thin TSX wrapper around the existing `.orb-*` classes.
- Wire up the `data-theme` + `data-density` toggles to a `useTheme()` hook persisting to `localStorage`.
- Tests: snapshot + axe on every atom.

**Exit criteria:** `npm run dev` boots a blank page styled with the new tokens. No regressions in `npm test` (existing component tests temporarily skipped behind a flag if needed).

### Phase 1 · App shell + catalog (A1–A4) + banners (H29, H30, deep-link error)

- Rewrite `App.tsx` shell: top banner stack (Forced-auto / Session-expired / Deep-link-error) + main content slot + theme toggle in the upper-right.
- Rewrite [features/pipeline/PipelineWidget.tsx](../../apps/web/src/features/pipeline/PipelineWidget.tsx) with the new catalog table from variant A:
  - Saved views (Recent / Review / Validated / Failed) as a left-rail.
  - Sortable filename / status / uploaded columns with `↑↓` indicators (mockup `toggleSort`).
  - Sticky-failed batch selection (the `selectedBatchIds: Set<string>` pattern stays — UI is rewritten, logic is preserved).
  - Status badges + scope chips via the new atoms.
- All three banners use the atom primitives + tokens.

**Exit criteria:** entire catalog tab works against the live demo backend at `https://kw-api.benhelli.org`. Status/scope visuals match the mockup pixel-for-pixel. Deep-link `/?document=<id>` still resolves.

### Phase 2 · Review workspace (B5–B10) — variant A

- Three-pane layout with rail (mockup variant A): document list on the left, review pane center, knowledge-graph preview right.
- Rewrite [features/review/ReviewWorkspace.tsx](../../apps/web/src/features/review/ReviewWorkspace.tsx) to render the doc detail meta-rows (`MetaRow` atom), raw extraction `<pre>`, Markdown preview `<pre>`, and reviewer note textarea.
- Rewrite [features/review/ReviewActions.tsx](../../apps/web/src/features/review/ReviewActions.tsx) so the FSM buttons (Extract / Semantic / Validate / Reject) use the new `Btn` atom + status-aware enabling. Disabled-tooltip-with-reason pattern preserved. `inFlightActionsRef` dedup unchanged.
- Rewrite [features/review/ProjectionStatusPill.tsx](../../apps/web/src/features/review/ProjectionStatusPill.tsx) using the new `Chip` atom with the existing `useProjectionStatus()` hook.

**Exit criteria:** a reviewer can open a document, run the full Extract → Semantic → Validate flow, and the projection pill polls correctly. All existing review-workspace vitest cases pass.

### Phase 3 · Batch operations (C11–C14)

- Rewrite the batch toolbar at the top of the catalog (per-row checkboxes already in Phase 1).
- New batch progress pills + structured failure report at the bottom of the catalog.
- Preserve `handleRunBatchPipeline` logic in `App.tsx`; only the rendering changes.

**Exit criteria:** select N docs → click "Run pipeline" → live progress → failures stay selected (per [memory: small-impact changes](file:///Users/sxz/.claude/projects/-Users-sxz-code-kw-pipeline/memory/feedback_small_impact.md), keep the exact UX semantics).

### Phase 4 · Knowledge graph viewer (D15–D18)

- Rewrite [features/graph/KnowledgeGraphView.tsx](../../apps/web/src/features/graph/KnowledgeGraphView.tsx) with:
  - The mockup's six-mode filter toolbar (All / Chunks / Topics / Entities / Relations / Source-backed).
  - Node-inspector side panel with `MetaRow` atoms for the inspected node.
  - Restyled `@neo4j-nvl/react` canvas — pass NVL's `theme` prop pointing at our token CSS variables so node/edge colors stay in sync with light/dark.
- Preserve `filterProjection()` pure-function logic in [features/graph/types.ts](../../apps/web/src/features/graph/types.ts).
- Auto-refresh after validate via the existing `useProjectionStatus` hook.

**Exit criteria:** open a validated document, see its knowledge graph rendered with the new theme, all six filter modes work, the node inspector shows keywords/topic/score.

**Risk to watch:** `@neo4j-nvl/react` may not accept arbitrary CSS variables for node colors — we may need to resolve the variables to hex via `getComputedStyle()` and pass those to NVL. Spike on day 1 of this phase before committing to a layout.

### Phase 5 · Vector search & grounded chat (E19–E21)

- Two slide-out panels on the right edge.
- Rewrite [features/search/SearchPanel.tsx](../../apps/web/src/features/search/SearchPanel.tsx) + [features/chat/ChatPanel.tsx](../../apps/web/src/features/chat/ChatPanel.tsx) + [features/chat/ChatModeToggle.tsx](../../apps/web/src/features/chat/ChatModeToggle.tsx).
- Preserve: 300 ms debounce, `AbortController`, `KW_VECTOR_SEARCH_DISABLED` / `KW_CHAT_DISABLED` remediation banners.
- Chat citations become clickable links that deep-link to the catalog (`/?document=<id>`).

**Exit criteria:** with `VOYAGE_API_KEY` + `ANTHROPIC_API_KEY` set on the demo backend, search and chat work end-to-end. With them unset, both panels render the remediation card.

### Phase 6 · Admin surfaces (F22–F25)

- Rewrite the four admin views — Hub, Archive, HITL, Audit — into the new layout.
- The Hub is the `/admin` landing with four cards (Archive, HITL, Audit, Config) per the mockup `OrbAdminHub view="hub"` artboard.
- Audit row expansion (click to expand → pretty JSON) uses the new `Card` atom.
- HITL drift table uses tabular-nums + the new `Status` colors.

**Exit criteria:** all four admin routes are reachable, gated by backend 403, and the existing E2E flow (validate → check audit → check projection status) is unbroken.

### Phase 7 · Destructive operations + settings (G26, G27, H28)

- Rewrite [features/purge/PurgeDialog.tsx](../../apps/web/src/features/purge/PurgeDialog.tsx) and [features/purge/PurgeAllDialog.tsx](../../apps/web/src/features/purge/PurgeAllDialog.tsx) as modals using the new `Card` + `Btn--danger` atoms. Typed-filename / typed-phrase confirmation preserved verbatim — these are safety controls, don't refactor them ([memory: stability > simplification](file:///Users/sxz/.claude/projects/-Users-sxz-code-kw-pipeline/memory/feedback_stability_priority.md)).
- Rewrite [features/settings/SettingsModal.tsx](../../apps/web/src/features/settings/SettingsModal.tsx) with the new diagnostics tiles + demo-toggle controls. Lazy-loaded as before.

**Exit criteria:** all destructive flows still require the exact typed confirmation. Settings modal opens, shows backend health, presenter demo-load works.

### Phase 8 · Polish

- Dark theme parity audit — every screen rendered side-by-side in light + dark, eyeball the contrast.
- Density-mode audit — every screen rendered in `cozy` / default / `dense`.
- A11y audit — `eslint-plugin-jsx-a11y` clean, axe-core finds zero violations, keyboard nav covers every interactive element.
- Bundle-size check — `npm run bundle:check` stays under the existing budget (the new design has fewer dependencies, so this should improve).
- Visual QA against the mockup — open `/tmp/orbital-mockup/Orbital.html` in a second tab and step through artboards.

**Exit criteria:** ready for cutover.

## 7. File mapping — mockup → repo

| Mockup file | Repo destination |
|---|---|
| `tokens.css` | [apps/web/src/styles/tokens.css](../../apps/web/src/styles/tokens.css) (new) |
| `orbital-shared.jsx` icons + atoms | [apps/web/src/ui/](../../apps/web/src/ui) — split per atom |
| `orbital-shared.jsx` `DOCS` / `VIEWS` mock data | **Dropped** — real data comes from `openapi-fetch`. Keep only for Storybook-style dev fixtures if we add them later. |
| `orbital-review-a.jsx` | [features/review/ReviewWorkspace.tsx](../../apps/web/src/features/review/ReviewWorkspace.tsx) + sibling files |
| `orbital-review-b.jsx`, `orbital-review-c.jsx` | **Dropped** — variants not chosen |
| `orbital-graph.jsx` | [features/graph/](../../apps/web/src/features/graph) |
| `orbital-search-chat.jsx` | [features/search/](../../apps/web/src/features/search) + [features/chat/](../../apps/web/src/features/chat) |
| `orbital-admin.jsx` | [features/admin/](../../apps/web/src/features/admin) |
| `orbital-dialogs-banners.jsx` | [features/purge/](../../apps/web/src/features/purge) + [ui/banners](../../apps/web/src/ui) |
| `design-canvas.jsx`, `Orbital.html`, `.design-canvas.state.json` | **Dropped** — preview-only scaffolding |

## 8. Styling strategy — plain CSS, BEM-ish, scoped via `.orb-*` namespace

The mockup uses plain CSS with a `.orb-*` class prefix and CSS custom properties. We adopt the same:

- One `tokens.css` at the root, imported once in `main.tsx`.
- Per-feature `*.module.css` for component-local styles **OR** a shared `components.css` if we prefer the mockup's monolithic approach. **Recommend: CSS Modules** — gives us deterministic class names, tree-shaking, and `npm run typecheck` catches typo'd class references.
- No Tailwind. No emotion. No styled-components. The mockup proves this isn't needed.
- `font-feature-settings: "ss01", "cv11"` + tabular nums applied at the `.orb-app` root.

## 9. Test strategy

- **Vitest + Testing Library** stays as-is.
- Each phase's PR includes rewritten component tests covering the same behaviors as the file it replaces.
- **No new visual-regression infra** (Playwright / Chromatic) — out of scope for the demo phase; we lean on manual visual QA against the mockup.
- `axe-core` already runs in `test-setup.ts`; keep it.
- E2E happy path (upload → extract → semantic → validate → graph) gets a smoke test in Phase 8 to confirm cutover-readiness.

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `@neo4j-nvl/react` doesn't accept CSS-variable themes | Spike on day 1 of Phase 4. Fallback: resolve vars to hex at runtime via `getComputedStyle()`. |
| Branch divergence from `main` over 8 phases | Weekly rebase. Each phase PR's diff stays small (one feature area). |
| Demo regression mid-rewrite | Preview deploys to `3dx-knowledge-orbital-next/` keep `main` Orbital untouched. |
| A11y regressions on the new dense layout | Keep `jsx-a11y` + axe-core enabled, audit at each phase exit. |
| Bundle size growth from new icons / atoms | Inline SVGs (current mockup approach) are cheaper than an icon library. Run `bundle:check` per phase. |
| Reviewer pushback after cutover | Old bundle stays at `3dx-knowledge-orbital/v0.0.0/`; one-line rollback by republishing it. |

## 11. Cutover plan

1. **Phase 8 complete + sign-off** — manual QA against the live demo backend.
2. **Tag a rollback point**: `git tag orbital-v0.0.0-final` on the commit that built the current production bundle.
3. **Final preview build**: deploy to `s3://3dx-kwforge-widgets/3dx-knowledge-orbital-next/cutover-rc1/` and walk through every flow.
4. **Cutover** — single deploy with `KW_ORBITAL_PREVIEW=false`:
   ```bash
   VITE_API_BASE_URL=https://kw-api.benhelli.org \
     scripts/deploy-orbital.sh v0.1.0
   ```
   This publishes to `3dx-knowledge-orbital/v0.1.0/` and updates [demo.html](../../demo.html) via [scripts/_update-demo-deployment.sh](../../scripts/_update-demo-deployment.sh).
5. **Cooldown** — keep the `v0.0.0` bundle in S3 for 30 days. If something goes wrong, redeploy v0.0.0 + revert demo.html.
6. **Merge** `orbital-redesign` → `main` as a single squash commit (the per-phase PRs onto the branch keep the history reviewable; the squash keeps `main` linear).
7. **Cleanup**: delete `3dx-knowledge-orbital-next/` prefix from S3 after 7 days.

## 12. Open questions / decisions to confirm before kickoff

These don't block writing code in Phase 0 but should be answered before Phase 1:

1. **Default theme on first load** — light or dark? (Mockup ships light as default but dark is the obvious operator pick. Suggest: detect `prefers-color-scheme`.)
2. **Default density** — `cozy` / default / `dense`? (Mockup default is comfortable for a 1440p laptop. Suggest: default, with an opt-in dense toggle in Settings.)
3. **Mock data in dev mode** — do we want a Vite dev flag that swaps in `orbital-shared.jsx`'s `DOCS` fixtures when the backend is offline? Useful for designer iteration; adds maintenance. (Suggest: skip; the local dev backend is one `./scripts/demo-backend.sh` away.)
4. **CSS Modules vs single stylesheet** — pin this before Phase 0. (Recommend: CSS Modules per feature folder.)
5. **Storybook?** — could host the atom catalog under `apps/web/.storybook/`. Adds ~500 KB to devDeps. (Suggest: skip for now; revisit if the team grows past one designer.)

## 13. Effort estimate

| Phase | Estimate | Notes |
|---|---|---|
| 0 — Tokens + atoms | 1–2 days | Mostly mechanical |
| 1 — Shell + catalog + banners | 2–3 days | Most-visited screen; worth getting right |
| 2 — Review workspace | 2–3 days | Three panes + FSM controls |
| 3 — Batch operations | 1 day | Logic preserved, UI swap only |
| 4 — Knowledge graph | 2–3 days | NVL theming risk |
| 5 — Search + chat | 1–2 days | Two panels, simple |
| 6 — Admin surfaces | 2 days | Four screens, all similar shape |
| 7 — Purge + settings | 1 day | Small modal work |
| 8 — Polish + cutover | 2–3 days | A11y + dark theme audit, then cutover |

**Total: ~3 working weeks** for one engineer. Demo-phase friendly because every phase is independently shippable; the rewrite can pause at any phase exit without leaving the codebase in a half-state.

## 14. Files referenced

- Mockup at `/tmp/orbital-mockup/` (from `Orbital Knowledge.zip`)
- [docs/wiki/Orbital-Features.md](../wiki/Orbital-Features.md) — feature catalog the mockup was built against
- [apps/web/](../../apps/web) — current Orbital
- [apps/_shared/](../../apps/_shared) — shared client/auth/retry (unchanged)
- [scripts/deploy-orbital.sh](../../scripts/deploy-orbital.sh) — deploy target
- [demo.html](../../demo.html) — gets auto-patched on deploy
