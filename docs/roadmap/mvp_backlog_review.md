# MVP Backlog Review

Last reviewed: 2026-04-30

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

Some early issues are still open even though current `main` appears to satisfy
most or all of their original acceptance criteria. These should be reviewed and
closed or rewritten so the backlog stays trustworthy:

- #1 Blueprint architecture docs.
- #2 Upload/catalog/hash/duplicate detection.
- #4 Raw extraction worker slice.
- #5 Semantic JSON and Markdown generation.
- #9 Raw extraction and Markdown retrieval endpoints.
- #13 Ruff/pre-commit/repo hygiene.
- #17 Persist extraction, semantic JSON, and Markdown artifacts.
- #19 Upload safety policy.
- #28 CI action version bump.
- #57 SQLite pragmas/WAL/foreign keys.
- #61 SQLite duplicate hash lookup.

Issue #18 is partly superseded by #63. If #63 is the real migration-system
work, #18 should be closed as superseded or narrowed to the exact remaining
backend-MVP migration requirement.

Issue #75 is an open PR covering #58 plus parser-registry cleanup. Review and
merge or close it before starting adjacent parser-registry work to avoid
conflicts.

## Missing Items Added

The 2026-04-30 audit added these missing backlog items:

- #77 Orbital API wiring and fixture removal.
- #78 3DEXPERIENCE widget embedding and brand token adapter.
- #79 Vite/esbuild audit remediation.
- #80 Generated typed API client from Harvester OpenAPI.
- #81 Backlog hygiene: close completed issues and rewrite partial items.

## Recommended Work Order

### 1. Finish active audit fixes

1. Review PR #75.
2. Decide whether to merge it or replace it with a smaller branch.
3. Close or update #58 based on that decision.

Why: it touches parser dispatch and whitespace-only extraction behavior, which
are close to the current extraction lifecycle code.

### 2. Clean stale backlog state

Review the backlog hygiene list above and close issues that are already done.
For issues that are only partly done, rewrite the body around the remaining
acceptance criteria.

Why: too many open-but-complete issues hide the real next work.

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
3. #38 pagination for `GET /documents`.
4. #41 streaming upload/hash.
5. #42 structured audit trail.
6. #43 Pydantic Settings.

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
