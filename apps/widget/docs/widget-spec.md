# 3DX KnowledgeForge — widget specification

This document is the single design-and-behaviour reference for the
KW-Pipeline 3DEXPERIENCE widget that lives under `apps/widget/`. It
covers what the application is, who it is for, every visible feature
and setting, the layout it renders today, the visual language it
should adopt (Dassault Systèmes brand-aligned), and the runtime
constraints a redesign must respect.

It is intended as input to layout and design work — feel free to
iterate on this file directly rather than recreating context
elsewhere.

---

## 1. What the application is

3DX KnowledgeForge is a **3DEXPERIENCE 3DDashboard widget** that puts
the KW-Pipeline document-intelligence backend inside a dashboard tile.
It is not a full app; it is a focused, embedded surface that lets a
user:

1. Confirm the backend is reachable.
2. Push documents (single, batch, or a whole folder) into the pipeline.
3. See the status of recently-ingested documents.
4. Glance at the size and shape of the resulting knowledge graph.

The widget is intentionally read-mostly and ingestion-first. Heavy
review work, full graph exploration, and admin tasks happen elsewhere
(the standalone web app at `apps/web`); the widget is a fast cockpit
the user keeps open on the dashboard.

## 2. Primary user

A knowledge worker or analyst working inside 3DEXPERIENCE who needs to:

- Drop documents (specs, reports, presentations, contracts) into
  KW-Pipeline without leaving 3DDashboard.
- Monitor whether those documents have been processed, validated, or
  flagged.
- Get a sense of how much knowledge has been extracted (nodes, edges,
  kinds).

The user is **not** the backend operator and **not** the deep-graph
explorer. They live in 3DEXPERIENCE all day and want the pipeline
embedded next to their other tiles.

## 3. Form factor & runtime context

- Lives inside a 3DDashboard tile. Width is typically 320–500 px,
  height variable; the user can resize.
- Renders on a white surface inside the dashboard (no native chrome).
- Loaded from S3 as an XHTML entry that bootstraps a React bundle. The
  dashboard supplies a `widget` runtime object for setting the title
  and persisting per-tile config.
- Per-tile state (currently just the API base URL) is saved by
  3DDashboard against the user/tile pair, so two tiles of the same
  widget can point at different backends.

## 4. Top-level layout (current)

A single vertical column inside the tile, top to bottom:

1. **Header strip** — widget title on the left, settings cog on the
   right, thin bottom border.
2. **Settings panel** (collapsible, hidden by default) — appears
   between header and body when the cog is toggled.
3. **Body** — four stacked cards in a fixed order, scrollable if the
   tile is short:
   - Backend health
   - Upload
   - Recent documents
   - Knowledge layer

There is no navigation, no tabs, no side panel, no router. Everything
is visible at once or scrolled into view.

## 5. Features

### 5.1 Backend health card

**Purpose.** Tell the user, at a glance, whether the pipeline is
reachable and which version is running.

**Display.**
- A status dot (grey/green/red) plus a status word.
- The backend's reported version, when available, after a separator.
- The currently-configured API base URL on a second line, in muted
  small text.

**States.**
- `Checking…` — first load, or after a base-URL change. Grey dot.
- `ok` — green dot, status word from backend, optional version.
- `error` — red dot, error message (e.g. "Unreachable", or
  `<code>: <detail>` from the API error contract).

**Behavior.**
- Auto-refreshes every 30 seconds.
- Re-runs immediately when the API base URL changes or when the global
  refresh tick is bumped (e.g. after a successful upload).
- Aborts in-flight requests on unmount or URL change.

### 5.2 Upload card

**Purpose.** Let the user push documents into the pipeline without
leaving the dashboard.

**Three entry points** (three buttons, all visible at once):
- **Add file** — single file picker. Primary action, visually
  emphasised.
- **Add multiple** — multi-select file picker.
- **Add folder** — recursive folder picker (browser
  `webkitdirectory`); the relative path inside the folder is preserved
  on each row.

**Queue.**
- Each picked file becomes a row in a small scrollable list under the
  buttons.
- Each row shows the file's display name (relative path if from folder
  picker) and a status badge.
- Rows can be in one of four states: `queued`, `uploading`, `done`,
  `failed`. Failed rows show the error inline beneath the row.
- A **Clear done** button appears once at least one row is in `done`.
  Clears successful rows only; leaves queued/uploading/failed in place.

**Processing model.**
- The queue uploads to `POST /documents/upload`.
- Concurrency limit of 2 in flight at once.
- Each successful upload triggers a global refresh, so Health /
  Documents / Knowledge cards re-fetch.
- Errors do not block the queue; failed rows stay visible with their
  error so the user can retry by re-picking.

**Not yet:** no per-row retry button, no drag-and-drop drop zone, no
file-type/size pre-validation, no batch summary at the end of a folder
upload.

### 5.3 Recent documents card

**Purpose.** Show what's recently been pushed into the pipeline and
where each item is in its lifecycle.

**List rows (one per document family).**
- **Filename** of the original upload (truncated with ellipsis if too
  long, full name on hover).
- **Meta line** under the filename:
  `v<latest version number> · <human-readable timestamp>`.
- **Status badge** on the right, color-coded by lifecycle phase:
  - `VALIDATED` — green (success)
  - `REJECTED`, `FAILED` — red (terminal failure)
  - `DUPLICATE_DETECTED`, `NEEDS_REVIEW` — amber (user attention required)
  - `EXTRACTED`, `SEMANTIC_READY` — blue (in-flight, machine working)
  - All other states — neutral grey
- The badge text is the literal status name (uppercased).

**Pagination.**
- First load fetches the most recent 25.
- A **Load more** button appears at the bottom when more results are
  available; clicking appends the next 25.
- The list does not auto-paginate on scroll.

**States.**
- Empty: "No documents yet — upload one to get started."
- Error: red error line above the list (list keeps any items already
  loaded).
- Loading: subtle; the Load-more button shows "Loading…" and disables.

**Not yet:** no filter, no sort, no search, no quick actions on rows
(open, retry, view review), no source-system origin, no per-row icons.

### 5.4 Knowledge layer card

**Purpose.** Give the user a one-glance sense of how much structured
knowledge has been extracted.

**Display.**
- A row of count tiles. Each tile = a big number on top, a small
  uppercase label underneath.
- Always shown: `Nodes`, `Edges`.
- After those two: the top 4 node kinds by count
  (e.g. `Document`, `Topic`, `Chunk`, `Entity`), descending. Smaller
  graphs may show fewer.
- A small note appears under the tiles when the graph exceeds the
  page-cap and counts are partial:
  "Showing first N nodes — graph is larger."

**States.**
- `Loading…` — muted text while pages are fetched.
- `error` — red error line; no tiles.
- `ok` — tiles + optional truncation note.

**Behavior.**
- Walks `GET /knowledge/graph` with cursor pagination, up to 10 pages
  of 200 nodes (cap = 2,000 nodes inspected). Aggregates `byKind`
  counts client-side.
- Re-runs on API URL change and on the global refresh tick.

**Not yet:** no clickthrough into the graph, no per-document
drill-down, no time-window filter, no breakdown by edge kind.

## 6. Settings — what's configurable

A single collapsible panel toggled by the cog icon in the header.
Currently exposes one setting:

- **API base URL.**
  - Free-text input, full width.
  - Save / Cancel buttons.
  - Persisted per tile via the dashboard's `widget.setValue` mechanism.
  - On save: the panel closes, the URL is applied immediately, and all
    four cards re-fetch.
  - No "test connection" affordance — the Health card is the de facto
    test.
  - No validation beyond "is it a string"; bad URLs surface as errors
    in the Health card and in subsequent fetches.

Everything else (concurrency limit, refresh interval, page sizes, KG
cap) is **constant in code**, not user-configurable.

## 7. Cross-feature interactions

- **Refresh tick.** A global counter that bumps on
  (a) successful upload, (b) API base URL change. Health, Documents,
  and Knowledge all watch this tick and re-fetch when it changes.
  Upload itself does not watch the tick.
- **API base URL is the single source of truth** for which backend the
  widget talks to. Changing it instantly redirects every card.
- **Cards are independent.** A failure in one (e.g. Knowledge graph
  endpoint disabled) does not affect the others.

## 8. API contract used by the widget

The widget calls four KW-Pipeline endpoints. Response shapes are
mirrored by hand in `src/api/types.ts` to avoid coupling this package
to the 1000+ line generated OpenAPI schema in `apps/web`.

| Endpoint | Method | Used by |
|---|---|---|
| `/health` | `GET` | Backend health card |
| `/documents` (cursor-paginated, `limit=25`) | `GET` | Recent documents card |
| `/knowledge/graph` (cursor-paginated, `limit=200`) | `GET` | Knowledge layer card |
| `/documents/upload` | `POST` | Upload card |

If a backend response shape changes in a way that affects the fields
rendered here, update `src/api/types.ts` to match.

## 9. Visual language — Dassault Systèmes brand-aligned

The widget's visual language follows Dassault Systèmes corporate
identity, replacing the earlier neutral slate baseline. Hex values
marked **`(verify)`** are best-faith candidates aligned with the
public DS brand and must be confirmed against the 2026 internal brand
book before being committed to code.

### 9.1 Brand foundation

**Primary brand colour: 3DS Blue.** A deep, slightly-teal corporate
blue. Used for primary actions, the brand bar, focus rings, links, and
the active settings cog state.

| Token | Value | Usage |
|---|---|---|
| `--ds-blue-900` | `#001E2E` `(verify)` | Deepest. Headings on light surfaces; pressed/hover state of filled buttons. |
| `--ds-blue-700` | `#005686` `(verify)` | The 3DS Blue. Primary action colour. Replaces the old `#2563eb` accent. |
| `--ds-blue-500` | `#1F8FBF` `(verify)` | Interactive accent, link, focus ring, selected row highlight. |
| `--ds-blue-100` | `#E5F2F8` `(verify)` | Tinted surface for selected/active card states; brand-bar gradient stop. |

**Neutrals.** Cool, near-neutral greys consistent with the platform's
UI shell.

| Token | Value | Usage |
|---|---|---|
| `--ds-ink-900` | `#0E1B26` | Primary text on light surfaces. |
| `--ds-ink-700` | `#3C4A55` | Secondary text. |
| `--ds-ink-500` | `#7A8893` | Tertiary text, captions, meta lines. |
| `--ds-ink-300` | `#C9D2DA` | Borders and dividers. |
| `--ds-ink-100` | `#F2F5F8` | Card background / surface. |
| `--ds-bg`      | `#FFFFFF` | Page background. |

**Semantic colours.** Same five lifecycle meanings as today, recoloured
to harmonise with 3DS Blue rather than fight it. Each semantic colour
also has a `--*-soft` chip background derived by mixing 8–12 %
saturation on white.

| Token | Value | Lifecycle states |
|---|---|---|
| `--ds-success` | `#1F7A4A` `(verify)` | `VALIDATED` |
| `--ds-warning` | `#B26A00` `(verify)` | `DUPLICATE_DETECTED`, `NEEDS_REVIEW` |
| `--ds-danger`  | `#B3261E` `(verify)` | `REJECTED`, `FAILED` |
| `--ds-info`    | `--ds-blue-500` on `--ds-blue-100` | `EXTRACTED`, `SEMANTIC_READY` |

### 9.2 Typography

The widget should default to the corporate Dassault stack. The exact
family name depends on the brand book; until confirmed, use a CSS
fallback chain that picks up the Dassault face when present and
degrades cleanly otherwise.

- **Family token:** `--ds-font: "DassaultSystemes", "Open Sans",
  -apple-system, "Segoe UI", Roboto, sans-serif;`
  `(verify the corporate face name)`.
- **Size scale:** keep the current compact rhythm
  (12 / 13 / 14 / 18 px), but title weight goes from 600 to 700 to
  match DS marketing typography. Card titles stay 12 px, uppercase,
  letter-spaced 0.04em.
- **No italics, no decorative weights.** DS UI is restrained.

### 9.3 Shape, density, motion

- **Radius** changes from 6 px to **4 px** across cards, buttons,
  badges, inputs — Dassault platform UI tends towards crisper
  rectangles than the rounded look the widget has today. Pill (999 px)
  shape is kept for status badges only.
- **Spacing scale** stays compact (4 / 6 / 8 / 10 / 12 / 16 px steps).
- **Borders** are 1 px, `--ds-ink-300`. Cards keep a single border,
  no shadow — flat surfaces match the platform.
- **Focus ring:** 2 px solid `--ds-blue-500` with 1 px offset. No glow.
- **Motion:** none today; if you add any, keep it under 150 ms,
  ease-out, opacity/transform only — DS UI is minimal-motion.

### 9.4 Iconography

- Adopt a single line-icon set with **1.5 px stroke**, square caps,
  16 / 20 / 24 px sizes. Phosphor, Lucide, or a DS internal set if one
  exists `(verify)`.
- Replace the `⚙` glyph in the header with a proper settings icon at
  16 px.
- Add minimal icons to status badges so meaning isn't colour-only
  (a check, a warning triangle, a cross, a clock).

### 9.5 Header — brand bar

The header strip is the widget's brand surface:

- Left: **3DS logo mark** at 16 px (the compass), followed by the
  product name "3DX KnowledgeForge" in `--ds-ink-900`, weight 700.
- The bar sits on a 1 px bottom border in `--ds-ink-300`, no fill —
  keeps the tile flat.
- Right: workspace/user pill (placeholder for #83/#91), then the
  settings icon button.
- Optional treatment: a 2 px top accent line in `--ds-blue-700` across
  the full tile width to anchor the brand. Keep it subtle.

The 3DS logo asset must come from the official brand pack (SVG,
monochrome and full-colour variants) — do not redraw it.
`(verify the asset path you want to bundle.)`

### 9.6 Token mapping (old → new)

For when `apps/widget/src/styles.css` is migrated:

| Old variable | Old value | New variable | New value |
|---|---|---|---|
| `--kw-fg`        | `#0f172a` | `--ds-ink-900` | `#0E1B26` |
| `--kw-fg-muted`  | `#475569` | `--ds-ink-700` / `--ds-ink-500` | `#3C4A55` / `#7A8893` |
| `--kw-bg`        | `#ffffff` | `--ds-bg`      | `#FFFFFF` |
| `--kw-surface`   | `#f8fafc` | `--ds-ink-100` | `#F2F5F8` |
| `--kw-border`    | `#e2e8f0` | `--ds-ink-300` | `#C9D2DA` |
| `--kw-accent`    | `#2563eb` | `--ds-blue-700` | `#005686` `(verify)` |
| `--kw-ok`        | `#16a34a` | `--ds-success` | `#1F7A4A` `(verify)` |
| `--kw-warn`      | `#d97706` | `--ds-warning` | `#B26A00` `(verify)` |
| `--kw-err`       | `#dc2626` | `--ds-danger`  | `#B3261E` `(verify)` |
| `--kw-radius`    | `6px`     | `--ds-radius`  | `4px` |

Badge backgrounds shift from the current Tailwind-ish soft tints to
soft tints derived from each semantic hue (8–12 % saturation on
white). Keep the badge text colour high-contrast against its tint.

### 9.7 Brand-token adapter (issue #78)

The widget runs inside 3DEXPERIENCE, which exposes its own theme
tokens at runtime. The plan:

1. Ship the static DS palette above as the widget's baseline (so it
   looks correct standalone, and during local dev).
2. On `widget.addEvent("onLoad", …)`, an adapter reads any
   3DEXPERIENCE-provided theme tokens (CSS custom properties or
   `widget.getValue("theme.*")`) and **overrides** the `--ds-*`
   variables on `:root`.
3. If the platform exposes a dark theme, the adapter flips the
   neutrals (`--ds-ink-*` and `--ds-bg`) to the dark scale below;
   brand blues stay the same hue but are mapped to the lighter
   `--ds-blue-500` for legibility on dark.

Dark-mode neutrals (placeholder, will be platform-driven once the
adapter lands):

| Token | Light | Dark |
|---|---|---|
| `--ds-bg`      | `#FFFFFF` | `#0E1B26` |
| `--ds-ink-100` | `#F2F5F8` | `#16242F` |
| `--ds-ink-300` | `#C9D2DA` | `#2A3946` |
| `--ds-ink-500` | `#7A8893` | `#7C8B97` |
| `--ds-ink-700` | `#3C4A55` | `#B5C0CA` |
| `--ds-ink-900` | `#0E1B26` | `#E8EEF3` |

## 10. Hard runtime constraints (do not redesign these away)

- The XHTML doctype, the `xmlns:widget` namespace, and the bootstrap
  script in `index.html` are required by the dashboard runtime.
- React must mount **inside** `widget.addEvent("onLoad", …)`.
- The dashboard's default UWA stylesheet is disabled
  (`disableDefaultCSS(true)`). The widget owns its CSS.
- There is no `manifest.json`; the widget descriptor is the XHTML
  entry plus a `widget.setTitle()` call.
- A redesign can change everything visual — palette, layout,
  components, copy, icons. It cannot change the runtime entry, the
  title call, the disable-default-CSS rule, or the per-tile
  `setValue` persistence.

## 11. What the widget intentionally does not do

- No document review (approve/reject/comment) — that lives in the
  standalone web app at `apps/web`.
- No graph exploration / node inspection — same.
- No user/auth surface — auth comes from 3DEXPERIENCE; not yet wired
  into the widget UI (issue #83).
- No multi-workspace selector — single backend, single scope per tile
  (issue #91).
- No notifications / toasts — feedback is inline per card.
- No keyboard shortcuts.

## 12. Backlog touching the widget

Open issues that will shape future iterations of this surface:

- **#78** — Prepare 3DEXPERIENCE widget embedding and brand token
  adapter (the visual-language work in §9 lands here).
- **#83** — Authentication, authorization, and 3DEXPERIENCE user
  context (header user/workspace pill).
- **#86** — Catalog search, filters, sorting, and saved views (Recent
  documents card).
- **#87** — Retry and reprocess failed documents (per-row action on
  Recent documents and Upload).
- **#88** — Review surface inside the widget (drawer/detail pattern).
- **#89** — Source metadata and 3DEXPERIENCE object links on documents
  (Recent documents row).
- **#82** — Bulk loading with batch ingest report (Upload summary
  surface).
- **#90** — Export validated assets and downstream handoff package.
- **#91** — Workspace/project scoping and data isolation (header
  workspace selector).
- **#92** — Sensitive-data detection, warnings, and redaction policy
  (per-row warnings).

## 13. Open questions for the brand pass

To lock §9 values into code, the following must be confirmed against
the 2026 internal Dassault Systèmes brand book:

1. Authoritative DS brand hex values (especially the blues, success /
   warning / danger semantics).
2. Corporate UI font family name and weight set.
3. Official DS logo SVG asset path (monochrome + full-colour variants)
   for bundling with the widget.
4. Any platform theme-token spec (CSS custom properties or
   `widget.getValue` keys) to wire the brand-token adapter (#78)
   correctly.
