# 3DX KnowledgeForge — UI rebuild plan (mockup → ship)

The design handoff (`design_handoff_knowledgeforge_widget/`,
`HFComposed` in `hifi.js`) replaces the current "everything stacked"
layout with a **44 px side-rail + single active mode body**. This
document is the prioritized rebuild plan, scoped against the current
state in `apps/widget/src/`.

## Mockup vs. current widget

| Area | Current | Mockup target |
|---|---|---|
| **Layout** | 4 cards stacked, all visible | Side-rail (44px) + single active mode body |
| **Header** | Title + cog | Brand mark + product name + breadcrumb stub + live pill + refresh / cog / more |
| **Settings** | Plain inline panel | Inline panel on accent-soft (`--ds-blue-100`) bg + reachability metadata pill |
| **Health** | Dot + word + version + URL | Big stat-line (word + ver + latency in mono), URL chip with `API` label |
| **Upload** | 3 buttons + queue | Drop zone + 3 buttons + queue with per-row progress % + aggregate progress bar + folder-scope summary card |
| **Documents** | Plain list, "Load more" | Search input + segmented filter + file-type icon + mono meta + status badge with icon |
| **Knowledge** | Single counts row | Hero stats + 2×2 tile grid + truncation info note |
| **Empty state** | Plain text line | Centered dashed placeholder + glyph + microcopy + action buttons |
| **Badges** | Pill, color only | Pill **+ icon** (check / cross / warn / clock / info) — meaning isn't color-only |

## Spec ↔ mockup conflicts (resolved)

| Question | Spec says | Mockup says | Pick |
|---|---|---|---|
| Top brand accent line | 2 px `--ds-blue-700` strip | none | **Remove** |
| Corner radius | 4 px | 3 px | **4 px** (spec wins) |
| Header workspace pill | placeholder for #83/#91 | "workspace · alpha" breadcrumb | **Static stub for v1**, real wiring lands with #83 |

## Backend gaps and v1 workarounds

| UI element | Backend gap | v1 workaround |
|---|---|---|
| Per-row upload progress % | `uploadDocument` uses `fetch`, no progress events | Refactor to `XMLHttpRequest` and emit `onprogress` callback — client-only |
| Aggregate "X of Y in flight" | client-side state | Already derivable from queue |
| Health latency `84 ms` | client-side | Time the `/health` fetch with `performance.now()` |
| Status filter counts (118 / 14 / 10) | No aggregated endpoint | Client-side count on currently-loaded rows; flag as approximate |
| Filename search | No `?q=` on `/documents` | Client-side filter on loaded rows; server-side search lands as a separate backend issue |
| KG 7-day delta (`↑ 8.2%`) | No history | Hide deltas in v1 — render only totals |
| Breadcrumb "workspace · alpha" | No workspace concept yet | Static placeholder; real wiring with #91 |

## Prioritized PR sequence

Each PR is small and independently shippable. Earlier PRs do not
depend on later ones unless noted.

### Foundation
1. ✅ DS palette migration (PR #180).

### Layout shift
2. **Header redesign** — brand mark, breadcrumb stub, live pill, refresh / cog / more buttons. Remove the top accent strip from PR #180.
3. **Side-rail navigation primitive** — 44 px rail, 4 mode buttons, active indicator, badges, footer status dot. Adds `activeMode` state in `App.tsx`; replaces vertical stack with single active-mode renderer.
4. **`<SectionHeader />` primitive** — icon + title + meta + actions slot + overflow.

### Per-card rebuilds (in demo-value order)
5. **Documents card rebuild** — search input, segmented filter with client-side counts, file-type icon, mono meta, badge with icon.
6. **Upload card rebuild** — drop zone, per-row progress (refactor `uploadDocument` to XHR), aggregate progress bar, folder summary tile.
7. **Knowledge card rebuild** — hero stat pair, 2×2 tile grid, info note.
8. **Health card rebuild** — big stat-line, URL chip, latency timing.
9. **Settings panel restyle** — accent-soft background, mono input, reachability pill.

### Component primitives
10. **`<StatusBadge status />`** — single source of truth for status → (variant, icon, label).
11. **`<FileTypeIcon ext />`** — dog-eared rectangle with extension label.
12. **`<EmptyState />`** — for documents-empty, upload-empty, knowledge-disabled.
13. **Icon library** (`widget-icons.tsx`) — inline SVGs lifted from mockup.

### Polish
14. **A11y pass** — keyboard rail nav (arrow keys), `aria-current="page"` on active rail button, `aria-label` on icon-only buttons, `aria-live="polite"` on health and queue.
15. **Brand-token adapter (#78)** — runtime read of 3DEXPERIENCE theme tokens; rewrite `--ds-*` on `:root`. Adds dark-mode neutral scale.
16. **3DS logo asset bundling** — drop the official compass SVG into `apps/widget/src/assets/`, swap the placeholder square.
17. **Animations** (low priority) — mode-switch fade ≤150 ms ease-out, spinner.

## Suggested rollout cadence

| Order | What | Visible result |
|---|---|---|
| 1 | Header + actions row | Widget feels DS-platform-correct |
| 2 | Side-rail | Layout inverts; one mode at a time |
| 3 | Documents rebuild | Most-used card |
| 4 | Upload rebuild | Other most-used card |
| 5 | Knowledge + Health rebuilds | All four modes complete |
| 6 | Settings + EmptyState + Badge primitive | Polish wave |
| 7 | A11y + brand adapter | Production-readiness wave |

## Files affected

```
apps/widget/src/
  App.tsx                              — active-mode state, side-rail layout
  styles.css                           — side-rail, drop zone, search, segmented, file-icon classes
  api/client.ts                        — uploadDocumentWithProgress (XHR), latency timing
  components/                          — NEW directory
    icons.tsx                          — line-icon set (lifted from mockup)
    Header.tsx
    SideRail.tsx
    SectionHeader.tsx
    StatusBadge.tsx
    FileTypeIcon.tsx
    EmptyState.tsx
  sections/HealthCard.tsx              — big stat-line, URL chip, latency
  sections/UploadQueue.tsx             — drop zone, per-row progress, summary
  sections/DocumentsList.tsx           — search, filter, file icons, badges
  sections/KnowledgeSummary.tsx        — hero stats, tile grid, info note
  settings/SettingsPanel.tsx           — accent-soft styling, reachability pill
```
