# Orbital Redesign — Sprint Plan

Companion to [`orbital-redesign.md`](./orbital-redesign.md). That doc defines _what_ we're building; this doc commits to _when_ and in what order.

## Sprint goal

> **Ship the Orbital redesign end-to-end behind a preview URL, with full feature parity to the current production bundle, and cut over on the final day.**

Exit means: the new bundle at `s3://3dx-kwforge-widgets/3dx-knowledge-orbital/v0.1.0/` is live on `demo.html`, the old v0.0.0 bundle is archived as a one-line rollback, and every flow in [`docs/wiki/Orbital-Features.md`](../wiki/Orbital-Features.md) works against the live demo backend.

## Dates

- **Sprint window:** Mon **2026-05-18** → Fri **2026-06-05** (3 calendar weeks, 15 working days).
- **Mid-sprint review:** Fri **2026-05-29** (after week 2) — go/no-go on hitting cutover by Jun 5.
- **Cutover day:** **2026-06-05** (Friday, EOD).
- **Cooldown window:** Mon 2026-06-08 → Fri 2026-07-05 — v0.0.0 stays in S3 for one-line rollback, no new visual changes.

## Capacity assumption

One engineer (you), part-time on this sprint because the demo phase keeps pulling attention. Plan assumes **~5 productive hours/day on the redesign**, leaving room for demo prep, stakeholder meetings, and the usual Cloudflared / Docker fires.

If actual capacity is significantly lower, the **Slip protocol** in §9 says what to drop without breaking the cutover.

## Phase priorities (must / should / nice)

| Priority | Phase | Why |
|---|---|---|
| **MUST** | 0 — Tokens + atoms | Nothing else compiles without it |
| **MUST** | 1 — Shell + catalog + banners | The screen reviewers see first |
| **MUST** | 2 — Review workspace | The daily-driver flow |
| **MUST** | 3 — Batch operations | Required for demo data prep |
| **SHOULD** | 4 — Knowledge graph | Visible regression if we cut over without it |
| **SHOULD** | 5 — Search + chat | Gated by env vars anyway; demo backend has the keys |
| **SHOULD** | 6 — Admin surfaces | Operator-only; can ship Day +1 if it slips |
| **SHOULD** | 7 — Purge + settings | Destructive; gets careful review regardless |
| **NICE** | 8 — Polish + cutover | If 0–7 all land, do this. Else, defer to a separate stabilization sprint. |

**Minimum viable cutover:** phases 0–4 landed + ad-hoc QA on phases 5–7. We can ship cutover with admin surfaces still on the old visual if 6 slips, since they're 403-gated and operator-only.

## Day-by-day plan

Each day ends with an exit criterion. Anything not green by EOD rolls into the next day's bucket.

### Week 1 — Foundation (2026-05-18 → 05-22)

**Mon 05-18 — Phase 0 kickoff: tokens + first atoms**
- Create `apps/web/src/styles/tokens.css` verbatim from the mockup.
- Wire into `main.tsx`. Verify both `[data-theme="light"]` and `[data-theme="dark"]` render.
- Build atoms: `Btn`, `Input`, `Kbd`, `Chip`, `StatusBadge`, `ScopeChip`.
- Open draft PR `orbital-redesign #1: tokens + atoms` onto branch `orbital-redesign`.
- **EOD exit:** atoms render in a `dev-atoms` test page; vitest passes.

**Tue 05-19 — Phase 0 finish: remaining atoms + theme hook**
- Atoms: `Card`, `Rule`, `MetaRow`, `SectionHeading`, the 22 SVG `Icon`s, `Pre` (mono code block).
- `useTheme()` hook + theme toggle in shell (persisted to `localStorage`).
- Density toggle stub (`cozy` / `normal` / `dense`) wired but unused in UI.
- **EOD exit:** every atom + theme/density toggle covered by a vitest snapshot + axe-core check. PR #1 ready for self-review.

**Wed 05-20 — Phase 1 kickoff: app shell + banner stack**
- Merge PR #1 (self-review). Open PR #2 `orbital-redesign: shell + banners`.
- Rewrite `App.tsx`: top banner stack, content slot, theme toggle in upper-right.
- Migrate `ForceAutoCorpusBanner`, `SessionExpiredBanner`, `DeepLinkErrorBanner` to atoms.
- **EOD exit:** with banners forced-on via dev hash params (`#force-session-expired`, etc.), all three render correctly side-by-side. Layout matches mockup pixel-for-pixel.

**Thu 05-21 — Phase 1 catalog table**
- Rewrite `PipelineWidget.tsx` with the variant-A catalog table.
- Status badges + scope chips wired to live `GET /documents`.
- Saved views (Recent / Review / Validated / Failed) as left-rail.
- Sortable columns (`toggleSort` pattern from mockup `orbital-review-a.jsx`).
- **EOD exit:** catalog loads from live backend, sort works, all four views filter correctly.

**Fri 05-22 — Phase 1 close + first preview deploy**
- Search bar, pagination cursor, deep-link `/?document=<id>`.
- Wire `KW_ORBITAL_PREVIEW=true` env var into [`scripts/deploy-orbital.sh`](../../scripts/deploy-orbital.sh) to retarget S3 prefix.
- **First preview deploy** → `s3://3dx-kwforge-widgets/3dx-knowledge-orbital-next/<sha>/`.
- Self-merge PR #2.
- **EOD exit:** preview URL works against live demo backend; share with one trusted reviewer (yourself in another tab counts).

### Week 2 — Daily-driver (2026-05-25 → 05-29)

**Mon 05-25 — Phase 2 kickoff: review workspace layout**
- Open PR #3 `orbital-redesign: review workspace`.
- Three-pane layout: doc list rail (reusing Phase 1 catalog), review pane center, graph preview slot right.
- `ReviewWorkspace.tsx` rewrites the meta-rows (`MetaRow` atom) + raw extraction + Markdown preview blocks.
- **EOD exit:** select a doc → see meta + extraction + markdown side-by-side. No FSM actions wired yet.

**Tue 05-26 — Phase 2 FSM controls + reviewer note**
- Rewrite `ReviewActions.tsx`: Extract / Semantic / Validate / Reject buttons with state-based enabling and disabled-tooltip-with-reason.
- Reviewer note `<textarea>` wired into POST bodies.
- `inFlightActionsRef` dedup preserved.
- **EOD exit:** complete Extract → Semantic → Validate flow works on a test document.

**Wed 05-27 — Phase 2 projection pill + tests**
- Rewrite `ProjectionStatusPill.tsx` with the new `Chip` atom + existing `useProjectionStatus()` hook.
- Migrate the four existing review-workspace tests (`App.test.tsx` review section, etc.) onto the new components.
- **EOD exit:** projection pill flips Projecting… → Up to date after validate. All migrated tests green.

**Thu 05-28 — Phase 3: batch operations**
- Open PR #4 `orbital-redesign: batch ops`.
- Per-row checkboxes (kept from Phase 1), batch action bar at the top, progress pills per row, structured failure report at the bottom.
- Preserve `handleRunBatchPipeline` logic verbatim; this is UI only.
- **EOD exit:** select 3 docs → click "Run pipeline" → live progress → failures stay selected.

**Fri 05-29 — Mid-sprint review + preview deploy**
- Self-merge PRs #3 + #4.
- Preview deploy with phases 0–3 complete.
- **Mid-sprint review (you, ~30 min):**
  - Walk through preview URL end-to-end against the mockup.
  - Decide: continue at full scope, or invoke the Slip protocol (§9).
- Write the decision into this file under §10.
- **EOD exit:** preview URL has full daily-driver flow working. Decision logged.

### Week 3 — Specialized surfaces + cutover (2026-06-01 → 06-05)

**Mon 06-01 — Phase 4 kickoff: NVL theming spike + graph filter toolbar**
- Open PR #5 `orbital-redesign: knowledge graph`.
- **Morning spike (1.5h max):** can `@neo4j-nvl/react` accept CSS-variable themes? If no, write `getComputedStyle()` resolver and pass hex. Commit the answer to the PR description.
- Build the six-mode filter toolbar (All / Chunks / Topics / Entities / Relations / Source-backed) using `filterProjection()` pure logic.
- **EOD exit:** filter toolbar works on a validated doc; graph re-renders correctly on toggle.

**Tue 06-02 — Phase 4 finish: node inspector + auto-refresh**
- Node inspector side panel with `MetaRow` atoms.
- Auto-refresh after validate via `useProjectionStatus` keyed on `lastMutationAt`.
- Migrate `KnowledgeGraphView` tests.
- **EOD exit:** click a node → inspector populated; validate a doc → graph refreshes without reload. Self-merge PR #5.

**Wed 06-03 — Phases 5 + 6 in parallel**
- **Morning — Phase 5 (PR #6):** rewrite `SearchPanel.tsx`, `ChatPanel.tsx`, `ChatModeToggle.tsx`. Preserve 300ms debounce + `AbortController` + remediation cards. Citations become clickable deep-links to catalog.
- **Afternoon — Phase 6 (PR #7):** rewrite the four admin views (Hub, Archive, HITL, Audit). They're structurally similar — one shared `<AdminCard>` + `<AdminTable>` makes this 4-for-1.
- **EOD exit:** search returns results; chat answers with citations; all four admin routes load and gate on 403.

**Thu 06-04 — Phase 7 + Phase 8 polish**
- **Morning — Phase 7 (PR #8):** `PurgeDialog` + `PurgeAllDialog` + `SettingsModal`. Typed-confirmation gates preserved verbatim — touch the safety logic last and minimally.
- **Afternoon — Phase 8 polish:**
  - Dark theme parity audit (10 minutes per screen × 8 screens).
  - Density mode quick-pass on the daily-driver screens (catalog + review).
  - A11y: `npm run lint`, `npm test` axe-core, keyboard nav of the catalog.
  - `npm run bundle:check` — must pass.
- **EOD exit:** all PRs merged onto `orbital-redesign`. Final preview deploy at `…/cutover-rc1/`. Walk through every flow one last time.

**Fri 06-05 — Cutover day**
- **Morning — cutover prep:**
  - `git tag orbital-v0.0.0-final` on the commit that built the current production bundle.
  - Update demo.html copy to mention "Orbital v0.1.0" + changelog link.
  - Drafts of PR review messages ready.
- **Midday — cutover:**
  ```bash
  VITE_API_BASE_URL=https://kw-api.benhelli.org \
    scripts/deploy-orbital.sh v0.1.0
  ```
  Verify [demo.html](../../demo.html) auto-patched to v0.1.0 URL.
- **Afternoon — soak + merge:**
  - Watch backend logs for any 4xx spike. Walk through every flow on the new live URL.
  - Squash-merge `orbital-redesign` → `main` (one commit, references all 8 phase PRs in the body).
  - Tag `orbital-v0.1.0`.
- **EOD — write sprint retro to `docs/handover/2026-06-05-orbital-cutover.md`** (10 minutes, even if tired):
  - What slipped, what landed early, what surprised you, what to do differently next sprint.

## Definition of Done — per phase

A phase is **Done** only when **all** of:

- [ ] Every component in scope has been rewritten (no surviving `apps/web/src/styles.css` references at phase exit).
- [ ] Component-level vitest + axe-core tests are green.
- [ ] `npm run typecheck` clean.
- [ ] `npm run lint` clean.
- [ ] `npm run build` succeeds.
- [ ] Preview deploy URL has been opened in a real browser and clicked through.
- [ ] PR self-merged onto `orbital-redesign` branch (squash, single commit per phase).

Phase 8 has additional cutover DoD:

- [ ] `npm run bundle:check` passes the existing budget.
- [ ] axe-core finds zero violations on every primary screen.
- [ ] Keyboard nav covers every interactive element on catalog + review workspace.
- [ ] Dark theme rendered correctly on every screen (manual eyeball, ~10 min/screen).
- [ ] Cutover deploy succeeds; demo.html auto-patches; live URL flows verified.

## Daily standup — async, written

Even solo, a 3-line journal at EOD keeps the sprint honest. Write to `docs/handover/2026-05-XX-redesign-day.md` (or one rolling file, your call):

```
shipped:   <one line>
blocked:   <one line, or "—">
tomorrow:  <one line>
```

Skip the day's standup → take 5 min the morning after to write yesterday's retroactively. Don't let it lapse for more than a day; the sprint is short enough that drift compounds fast.

## Sprint review — Fri 2026-06-05 EOD

A 30-minute self-review at EOD on cutover day:

1. Open the new live Orbital URL. Walk through: upload → extract → semantic → validate → graph → search → chat → admin audit.
2. Compare against the mockup screen-by-screen.
3. Confirm v0.0.0 bundle is reachable at its archived URL for rollback.
4. Write the retro doc.

If the cutover hasn't happened by EOD Jun 5, **don't cut over Friday afternoon** — that's the worst possible time. Move cutover to Monday Jun 8 morning instead.

## Slip protocol — what to drop if behind

In priority order, the first things to cut if the sprint is running hot:

1. **Density mode polish** (Phase 8) — default density only; `cozy` and `dense` ship later.
2. **Dark theme parity audit** (Phase 8) — light-only at cutover; dark fixes ship in a follow-up.
3. **Phase 6 admin surfaces** — keep the old admin UI behind a route prefix; only operators see it; ship redesigned admin in a follow-up sprint.
4. **Phase 5 chat** (not search) — search is read-only and low-risk; chat involves more LLM coordination.
5. **Phase 4 graph node inspector** — graph canvas + filter toolbar are the must-haves; node-detail panel can use the old visual temporarily.

What we never cut:
- Phase 0 atoms — non-negotiable.
- Phase 2 FSM controls — they ARE the product.
- Typed-confirmation gates in Phase 7 — safety-critical, can't ship partial.

If two or more items are on the chopping block at mid-sprint review, **move cutover to Jun 12** and use the extra week for polish + the dropped items. Better a one-week slip than a buggy cutover.

## Risks specific to this sprint

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| NVL theming doesn't accept CSS variables | Medium | Phase 4 +0.5 day | Spike Mon 06-01 morning; fallback to `getComputedStyle()` resolver |
| Demo phase fire-drills eat 2+ days | High | -10–20% capacity | Slip protocol; sprint is sized for ~75% capacity already |
| Backend API drift mid-sprint (e.g. new `/admin/*` endpoint shape) | Low | Phase 6 +1 day | Regenerate types via `npm run openapi:generate` weekly |
| Cutover reveals layout regression | Medium | -3h on Jun 5 | Cooldown plan: redeploy v0.0.0 in <5 minutes (script ready) |
| Bundle size grows past budget | Low | Cutover blocker | Phase 8 explicitly runs `bundle:check`; if budget breached, defer non-critical icons |

## 10. Mid-sprint review log

_Fill this section in on 2026-05-29 EOD._

- **Phases complete:** _<list>_
- **Phases slipping:** _<list with new ETA>_
- **Decision:** _continue at full scope / invoke slip protocol / move cutover to Jun 12_
- **Reasoning:** _<one paragraph>_

## 11. Post-cutover retro stub

_Fill this in to `docs/handover/2026-06-05-orbital-cutover.md` on cutover day._

Key questions for the retro:

1. Which phase took the longest vs estimate? Why?
2. What surprised you about the mockup → code translation?
3. Any atoms we'd add to `apps/_shared` next time to save work on Explorer/Widget redesigns?
4. Cutover incidents? Rollback rehearsal — did we ever actually need it?
5. What would you do differently for the next big rewrite?
