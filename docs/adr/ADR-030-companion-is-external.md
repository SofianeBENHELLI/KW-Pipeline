# ADR-030: AURA Companion is External — KW Pipeline ships contracts only

## Status

Accepted, 2026-05-10.

This ADR records the scope boundary between **KW Pipeline** (this
repo) and **AURA** (the future companion / answer / recommend /
decide / act surface). It supersedes the in-tree "Companion frontend
(chat UI in `apps/web` or new `apps/companion/`)" item that ADR-029
listed as Phase A of [EPIC #373](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/373).

## Context

KW Pipeline already ships three frontends:

- `apps/web` — Orbital reviewer workbench
- `apps/explorer` — Knowledge Explorer
- `apps/widget` — KW Forge ingestion widget

Each addresses a distinct audience and is intentionally decoupled
(see the user's stated architecture preference: "three frontends,
three audiences — keep decoupled"). Adding a fourth in-tree
frontend (`apps/companion/`) would:

- Force every backend change to touch a fourth client surface.
- Pull a chat / RAG / streaming UI into a repo whose bounded context
  is *knowledge production*, not *knowledge consumption*.
- Tie KW Pipeline's release cadence to the companion's UX iteration
  speed, which is much faster.

The user has decided AURA will be a **separate product**, connected
to KW Pipeline over the HTTP contracts only.

## Decision

KW Pipeline is the **knowledge platform**. AURA is an **external
consumer**. Inside this repo:

- ✅ **In scope**: backend contracts, schemas, policy primitives,
  audit event names, settings — everything AURA needs to compose a
  grounded answer against the validated knowledge layer. The three
  pre-implementation lock-ins shipped under EPIC #373
  ([#370 citation contract](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/370),
  [#372 trust gate](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/372),
  [#371 feedback bridge](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/371))
  stay in scope.
- ✅ **In scope**: the future `POST /companion/answer` route
  (when filed) and `POST /companion/feedback` route — these are
  HTTP contract surfaces AURA will call.
- ❌ **Out of scope**: any `apps/companion/`, `apps/chat/`, or
  in-`apps/web` chat UI. AURA owns its own frontend.
- ❌ **Out of scope**: companion-specific deployment artefacts
  (chat session storage at scale, UI streaming infrastructure, etc).
  KW Pipeline exposes the data; AURA stores the conversation.

## Consequences

### What changes

- The Phase A list under EPIC #373 drops "Companion frontend".
- The companion route (when implemented) ships as a backend-only PR
  with HTTP-shape tests; no frontend smoke coverage in this repo.
- The Pydantic schemas in `app.schemas.companion` and
  `app.schemas.companion_feedback` become the official AURA-facing
  contract — additive evolution per ADR-029's back-compat policy.

### What stays the same

- The Knowledge Explorer (`apps/explorer`) keeps its read-only
  navigation surface. It is **not** a companion — no Q&A, no
  generative responses. Search + browse only.
- Orbital (`apps/web`) keeps its reviewer workbench role, including
  the Orbital-side surface for HITL re-reviews triggered by the
  AURA feedback bridge (#371). The trigger fires inside KW
  Pipeline; the human review happens in Orbital.
- The widget (`apps/widget`) is unaffected.

### What this enables

- AURA can be implemented in any stack (TypeScript SPA, native
  app, embedded chat in a 3DEXPERIENCE widget, etc.) without
  KW Pipeline release coordination.
- The contract becomes the integration boundary, not source code.
  Versioning the contract (`schema_version` on `GroundedAnswer`)
  is enough to manage AURA evolution.
- Internal contributors who join the KW Pipeline repo see a clean
  bounded context: "produce + validate + serve knowledge", with the
  consumer surface explicitly outside.

## Out of scope (for this ADR)

- The HTTP transport choice for AURA → KW Pipeline (REST vs
  WebSocket vs SSE) — decided when the route ADR is written.
- Authentication between AURA and KW Pipeline — covered by
  ADR-019; AURA presents a bearer token like any other client.
- Whether AURA is open-source or proprietary — orthogonal.
