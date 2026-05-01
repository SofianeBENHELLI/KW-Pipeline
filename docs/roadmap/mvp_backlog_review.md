# MVP Backlog Review

Last reviewed: 2026-05-01

## Current Health

- Backend tests: `294 passed`.
- Frontend tests: `2 passed`.
- Frontend production build: passed.
- Ruff: passed.
- Python compileall: passed.
- Known dependency advisory: `npm audit --audit-level=moderate` reports the
  Vite/esbuild development-server advisory; tracked in issue #79.

## Code Audit Summary

The backend has moved beyond the first in-memory MVP:

- SQLite persistence now covers catalog, raw extraction, semantic JSON, and
  Markdown payloads.
- Semantic payloads have schema-version loading/migration policy.
- Lifecycle transitions are enforced through the FSM.
- Upload guardrails cover size and content-type allowlisting.
- CORS is configurable for Orbital local development.

The frontend is currently a structured scaffold:

- Compact pipeline widget.
- Expanded review workspace.
- API-shaped sample fixture data.
- Shared status badges and domain types.
- No live API wiring yet.

## Backlog Hygiene Findings

**2026-05-01 hygiene pass (issue #81) complete.** All 12 candidate issues
were resolved:

- #1, #2, #4, #5, #9, #13, #17, #19, #28, #57, #61 — closed as **completed**
  (acceptance criteria fully satisfied on `main`).
- #18 — closed as **superseded** by #63 (real schema migration system).

The backlog is now clean. No further triage needed from the original
hygiene list.

## Missing Items Added

The 2026-04-30 audit added these missing backlog items:

- #77 Orbital API wiring and fixture removal.
- #78 3DEXPERIENCE widget embedding and brand token adapter.
- #79 Vite/esbuild audit remediation.
- #80 Generated typed API client from Harvester OpenAPI.
- #81 Backlog hygiene: close completed issues and rewrite partial items.

## Recommended Work Order

### 1. (Done) Active audit fixes

PR #75 merged as commit `e15bf69` (closes #58 — reject whitespace-only
uploads + wire ParserRegistry).

### 2. (Done) Clean stale backlog state

Hygiene pass completed on 2026-05-01 (issue #81). Closed 11 completed issues
and superseded 1. See "Backlog Hygiene Findings" above.

**Recent closes since the 2026-04-30 audit doc:**

| Commit | Issue | What it closes |
|---|---|---|
| `e15bf69` | #58 | Reject whitespace-only uploads + wire ParserRegistry |
| `dc0b7ca` | #49 | SemanticEnricher Protocol and ADR-009 |
| `d1c3e92` | #46 | DOCX parser via python-docx |
| `67fa258` | #41 | Stream uploads and SHA-256 |
| `d252c67` | #38 | Cursor pagination on GET /documents |

### 3. Make Orbital live

Work #77 first, optionally paired with #80:

1. Add frontend API client.
2. Replace fixture-driven document list with `GET /documents`.
3. Wire upload, extract, semantic generation, validate, and reject.
4. Add UI loading/error/empty states.
5. Keep compact widget constraints from #78 visible while building.

Why: this turns the current frontend scaffold into a usable MVP slice.

### 4. Keep CI and dependencies honest

1. Complete #79 or record an explicit temporary acceptance.
2. Keep `npm ci`, frontend tests, and build in CI.
3. Add type generation/staleness checks once #80 lands.

Why: frontend work is now real enough that dependency and contract drift will
start to matter.

### 5. Prepare the next backend phase

After Orbital can use the current API, prioritize:

1. #63 real schema migration system.
2. #60 idempotency keys for write endpoints.
3. #42 structured audit trail.
4. #43 Pydantic Settings.

(#38 pagination and #41 streaming upload are already done on main.)

Why: these are the robustness and scale items most likely to hurt once a UI is
driving the API repeatedly.

### 6. Then move to real documents and semantic enrichment

Once the MVP is usable end-to-end:

1. Parser queue and background jobs: #40.
2. Real parsers: #45, #46, #47, #20.
3. Semantic provider/entity work: #21, #48, #49.
4. Knowledge taxonomy/export: #22, #23.

Why: parser and semantic enrichment work should build on stable lifecycle,
persistence, and review workflows.

## Open Decisions

- Should duplicate bytes uploaded without `document_id` create a new family or
  attach to the original family? Issue #59 says the current behavior is wrong,
  but product semantics should be confirmed before changing it.
- Should review status and semantic validation status be committed in one
  catalog transaction? The route now avoids the obvious partial-mutation path,
  but a stronger service-level transaction may be warranted later.
- Should unsupported content types fail during upload only, extraction only, or
  both? Current behavior supports configurable upload allowlists and parser
  registry failure at extraction time.
- What is the first 3DEXPERIENCE container size and authentication/context
  model? This drives #78.
