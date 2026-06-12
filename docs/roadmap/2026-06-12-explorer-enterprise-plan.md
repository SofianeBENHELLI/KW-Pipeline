# Knowledge Explorer — enterprise readiness plan — 2026-06-12

Outcome of the 2026-06-12 HMI audit, driven by three operator
complaints: the Explorer is buggy at enterprise (large-corpus) level,
there is no smooth way to move between enterprise / community /
document scopes, and the bundled demo dataset mixes with production
data.

Companion to the converged MVP plan
([`2026-05-17-converged-knowledge-pipeline-plan.md`](2026-05-17-converged-knowledge-pipeline-plan.md))
— that plan's §C shipped in PR #495; this one owns the Explorer
follow-on.

---

## A. Audit findings

### A.1 Demo / production mixing — backend catalog, not a frontend glitch

- The demo-toggle loader uploads its 44 fixture documents into the
  **same catalog** as operator data; identification was by
  `original_filename` match only (`demo_dataset.py`).
- The conflict guard is bypassable with "Force load"; reset only
  soft-archives. The Explorer UI rendered all rows identically — no
  badge, no filter.
- The frontend's built-in 16-doc `SAMPLE_SNAPSHOT` is **not** the
  culprit: it's all-or-nothing and only used when the backend is
  unreachable, labelled "Sample · backend offline".

### A.2 Enterprise-level HMI defects (ranked)

| # | Defect | Where |
|---|--------|-------|
| 1 | No scope-switching UI at all (backend accepts `?scope_kind/scope_ref`; frontend never sends them; no scope indicator) | `App.tsx`, `scope_filter.py` |
| 2 | Full-corpus graph render by default (up to ~5,000 nodes), violating ADR-028 "never default to a full-corpus render" | `App.tsx` view="corpus", `use-explorer-data.ts` `fetchFullGraph` |
| 3 | `focusRoot` not URL-persisted — focus context lost on refresh; back/forward stack cleared | `App.tsx` |
| 4 | Search→selection race with in-flight fetches | `SearchResults` onPick path |
| 5 | `expandedDocs` / `expandedClusters` never cleaned on data refresh (unbounded growth, stale ids) | `App.tsx` + `use-explorer-data.ts` |

`App.tsx` is at ~1,800 LOC and `GraphCanvas.tsx` ~1,380 — issue #229's
split is a precondition for the bigger fixes.

### A.3 Scope model status

Backend already models `personal` / `swym_community` / `project`
(ADR-020) and the routes accept explicit scope query params; community
and project resolution 403s pending the Swym membership client
(EPIC-D #218). A dev-mode / admin-seeded community path can ship
before that client exists.

---

## B. The plan (4 sprints)

### Sprint 1 — Stop the data mixing ✅ shipped with this doc's branch

- `documents.origin` column (`operator` | `demo`), migration
  `0016_document_origin` with frozen-fixture-list backfill.
- `CatalogStore.mark_documents_origin`; demo service stamps post-load
  and on reset (covers crashed mid-flight loads).
- `GET /documents?include_demo=false`; `Document.origin` on the wire.
- Explorer: DEMO badge on catalog rows; "Demo data · hidden/shown (N)"
  chip in the CORPUS rail (persisted, `kx-hide-demo-docs`); auto rule —
  hide demo rows only when they coexist with operator documents.
- Deferred to a follow-up: hard-purge option on `POST /admin/demo/reset`
  (operator can chain `/admin/archive/purge_artifacts` today).

### Sprint 2 — Scope ladder: Enterprise → Community → Document (~1.5 wk)

- Scope breadcrumb rail (`Enterprise ▸ Community ▸ Document`),
  URL-persisted, back/forward across scope changes.
- `GET /me/scopes` enumerating the caller's communities (admin-seeded
  rows now; Swym client (#218) slots in later without UI change).
- Thread `scope_kind` / `scope_ref` into every Explorer read
  (documents, graph, neighborhood, atlas) + an always-visible scope
  badge.
- Smooth narrowing: keep the current selection when it's still
  visible in the new scope; lens-in transition instead of re-mount.

### Sprint 3 — Enterprise-grade rendering, ADR-028 compliance (~1.5–2 wk)

- Default landing becomes an Atlas view (port the `/kf/explore`
  pattern); the graph only renders as a bounded lens via
  `/knowledge/neighborhood` — no more full-corpus fetch.
- Persist `focusRoot` in the URL hash; clear `expandedDocs` on
  refresh; serialize the search→select race.
- #229 split of `App.tsx` / `GraphCanvas.tsx` lands here as enabling
  work.

### Sprint 4 — Polish (~1 wk)

- Ranking / filter controls (#320), large-corpus fixtures + perf
  smoke (#322), chunk-panel scroll coordination, scope-aware trust
  defaults (validated-only at enterprise altitude).

---

## C. Open decision

Two explorer surfaces exist: the 3DX tile (`apps/explorer`) and
Orbital's `/kf/explore`. This plan invests in `apps/explorer` (the
deployed 3DX tile), reusing the atlas/neighborhood backend that
`/kf/explore` proved out. If the team prefers to consolidate on one
surface, decide before Sprint 3 starts.

---

_End of Explorer enterprise readiness plan, 2026-06-12._
