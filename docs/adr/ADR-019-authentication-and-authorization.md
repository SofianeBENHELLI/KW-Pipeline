# ADR-019: Authentication, Authorization, and 3DEXPERIENCE User Context

## Status

**Proposed**, 2026-05-04. First slice of
[#83](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/83). This
ADR sets the contract; a follow-up ADR (number TBD) will specify the
3DEXPERIENCE context handoff once Dassault platform docs are in hand.

ADR-018 is unclaimed and skipped on purpose — the auth slot was
reserved as ADR-019 by the ADR-017 references and the backlog
roadmap.

## Context

Today every write endpoint accepts anonymous calls. The audit-event
store landed in #206 (closes the residual of #26) and the structured
event vocabulary documented in
[`docs/architecture/observability.md`](../architecture/observability.md)
is already wired — but there's nothing for it to attribute. A reviewer
opens Orbital, hits **Validate**, and the resulting
`review.validated` audit row does not record *who* validated. Same
problem for `review.rejected`, batch upload, semantic generation, and
every other write path. "Who validated this document?" is a question
the system cannot answer.

The Orbital UI has no concept of an unauthorized state either: there
is no sign-in surface, no session-expired banner, no 401-aware error
envelope rendering on the frontend. A future deployment behind any
real identity provider would simply break.

Issue [#83](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/83)
mandates three things:

1. **Identity for write actions** — every state-changing endpoint
   needs a principal, even if that principal is "anonymous" today.
2. **Audit-trail attribution** — every persisted audit event for a
   write action carries an `actor` field tied to that principal.
3. **A path to 3DEXPERIENCE context handoff** — when the embedded
   widget is hosted inside a 3DDashboard, the platform-supplied user
   context flows into KW-Pipeline so the reviewer doesn't double-sign
   in.

This ADR scopes the **first slice**: the `AuthService` boundary, three
operating modes (`disabled` / `dev` / `bearer`), and the actor wiring
on the review-decision path. Role enforcement, the
3DEXPERIENCE-specific handoff, and the frontend session-expired UX are
explicitly deferred — see §5–§6 below for the slicing plan.

## Decision

### 1. `AuthService` Protocol — three operating modes

A single Protocol (`app.services.auth.AuthService`) sits in front of
every authenticated request. Concrete impls plug in via the same
"swap-in-via-factory" pattern ADR-013 (LLM) and ADR-015 (embeddings)
already use. Mode selection is governed by the `KW_AUTH_MODE` env var,
read once at app startup through `app.settings.Settings`.

| Mode | Identity | When it's used | Audit actor |
|---|---|---|---|
| `dev` | Fixed identity from `KW_AUTH_DEV_USER` (defaults to `dev`), role `admin` | **Current default.** Local dev / CI / demos. Keeps the out-of-the-box flow open while attributing every review decision to a recognisable actor in the audit log. Switch to `bearer` for any shared deployment. | `KW_AUTH_DEV_USER` (or `dev`) |
| `disabled` | Anonymous user, role `admin` | **Legacy escape hatch.** Behaviour matches pre-ADR-019: every write endpoint accepts every caller and the audit row carries the `anonymous` sentinel. Kept as an explicit opt-in (`KW_AUTH_MODE=disabled`) for callers that still expect the open-API shape; loud startup warning. **Will be removed** once nothing in CI / docs / dashboards still asks for it. | `anonymous` |
| `bearer` | HS256 JWT in `Authorization: Bearer <jwt>`, validated against `KW_AUTH_SECRET` | **MVP only.** Internal service-to-service handshake (ITEROP callbacks, scheduled jobs). Refuses to construct without `KW_AUTH_SECRET` so a misconfigured deployment fails at startup, not at the first 401. | `sub` claim |

The `bearer` claim shape is `sub` (user id) + `role` (one of
`viewer` / `contributor` / `reviewer` / `admin`) + `exp` (Unix
seconds, required) + `iat` (Unix seconds, required). Tokens missing
a claim, with a bad signature, with an unknown role, or expired are
rejected with a generic `401 unauthorized` envelope — error messages
are intentionally constant ("missing or invalid token") so the
verifier doesn't leak which check failed.

`dev` is the current default. Every existing test, demo seed script,
and frontend call keeps working without setting `KW_AUTH_MODE`, AND
every review decision lands a recognisable `actor="dev"` in the audit
log instead of the legacy `anonymous` sentinel. The factory logs
`auth.mode_selected` at startup so an operator who left the default in
a shared environment notices the MVP-grade identity layer.

`disabled` remains available as a legacy escape hatch via an explicit
`KW_AUTH_MODE=disabled` opt-in. Picking it loudly logs
`auth.mode_selected` with a remediation hint pointing at this ADR so
an operator who set it does not silently keep an open API.

### 2. Implementation — stdlib HS256, no PyJWT

The verifier uses `hmac` + `hashlib` + `base64` from the standard
library to do HS256 verification. Roughly 50 lines including the
b64url padding helper and the constant-time signature compare. Why
not pull in PyJWT:

- **MVP scope.** The bearer mode is not the production scheme — the
  3DEXPERIENCE context handoff supersedes it. Adding a runtime
  dependency we'll then have to deprecate is more churn than the
  stdlib version.
- **Determinism.** No transitive dep means no SDK-default behaviour
  to chase across upgrades (e.g. PyJWT's leeway / required-claim
  defaults shifted across major versions).
- **Crypto budget.** `cryptography` already ships transitively via
  `voyageai`; when the production handoff lands and we need RS256 /
  JWKS, that direct-dep promotion happens at the same time.

A test helper (`encode_hs256`) lives next to the verifier so tests
don't import a third-party library just to construct fixtures. The
API is a verifier only — tokens are minted upstream.

### 3. Roles

Four canonical roles, defined as a `Literal[...]` so the Python type
checker enforces the closed set at every call site:

| Role | Description | Initial endpoint mapping (when enforcement lands) |
|---|---|---|
| `viewer` | Read-only access to the catalog and knowledge layer. | `GET /documents`, `GET /documents/{id}`, `GET /knowledge/*`. |
| `contributor` | `viewer` + ingestion writes. | `POST /documents/upload`, `POST /documents/.../extract`, `POST /documents/.../semantic`. |
| `reviewer` | `contributor` + review decisions. | `POST /documents/.../validate`, `POST /documents/.../reject`. |
| `admin` | `reviewer` + admin endpoints. | `POST /admin/purge`, `POST /admin/replay`, etc. |

In this ADR, the role mapping is **defined** but not yet
**enforced** beyond the actor-on-review-decision path. The
:class:`User` type carries the role today; a follow-up slice adds an
`@require_role(...)` dependency that returns HTTP 403 for an
insufficient role. The slicing matters: turning every write route
into an auth-required path simultaneously would force the frontend
work into the same PR, which we want to keep separate.

### 4. Actor identity in audit

Every audit event for a write action MUST carry an `actor` field —
the authenticated principal id (`User.id`). This PR wires it on the
two review-decision paths only:

- `review.validated` — emitted by
  `DocumentService._record_review` via `ReviewService.handle_validation`.
- `review.rejected` — emitted by the same path via
  `ReviewService.handle_rejection`.

Other write paths (`document.uploaded`, `document.status_changed`,
`semantic.generated`, knowledge-projection events) get the same
treatment in follow-up slices — the slicing keeps each PR small
enough to review against a single test surface.

The route layer reads the principal via the `get_current_user`
FastAPI dependency, which calls `services.auth.authenticate(request)`
and translates the `AuthError` → HTTP 401 with the stable error
envelope (`ErrorCode.UNAUTHORIZED`).

### 5. Unauthorized & expired-session UX

**Out of scope for this PR.** The frontend is untouched.

The backend contract is in place now: auth failures return HTTP 401
with the `KW_UNAUTHORIZED` error envelope; role failures (when
enforcement lands) return HTTP 403 with `KW_FORBIDDEN`. Both
envelopes carry `error.message`, `error.remediation`, and
`error.retryable` — the same shape every other client-side error
already uses, so the frontend session-expired surface is a render
change, not a contract change.

A follow-up ADR slice covers:

- Orbital — reading the 401 envelope and showing a "your session
  expired, sign in again" banner with a deep-link to the configured
  IdP (or a reload prompt for `dev` mode).
- Knowledge Explorer — the same banner shape.
- Widget — the embedded surface gets the 3DX-supplied identity (see
  §6) so a 401 there means the host platform's session expired, not
  the API's. The remediation is "reload the dashboard tile".

### 6. 3DEXPERIENCE context handoff

**Deferred to a future ADR.** Dassault Systèmes' 3DDashboard exposes
a platform-level user context to embedded widgets via the Run-Your-App
(RYA) container — the embedded widget can read `csrf` cookies, the
3DPassport collaborative-space id, and the active user. Translating
that context into a KW-Pipeline `User` is the production path; that
translation is what the deferred ADR will specify, alongside:

- The token shape (likely RS256 with the JWKS endpoint exposed by
  3DPassport).
- The handshake (where in the widget bootstrap we request the
  context, how we cache it, how we refresh it).
- Tenant scoping (the collaborative-space id becomes the tenant key
  for the workspace story tracked under EPIC-D / #218).

This ADR's `bearer` mode is the bridge: it lets us wire actor
attribution and the role model without blocking on the 3DX docs. The
Protocol is the same shape, so the new mode plugs in alongside
`disabled` / `dev` / `bearer` without churning any call site.

This dependency is also tracked under #78 (widget container/theme
work) — the same physical platform docs unblock both ADRs.

## Consequences

**Positives**

- Audit attribution: `review.validated` / `review.rejected` rows
  carry an `actor`, so "who validated doc X" is a SQL query.
- MVP path to embedded: the `bearer` mode lets an internal
  service-to-service caller (Iterop) authenticate today without
  waiting on the 3DX handoff.
- Forward-compat: every write route migrates to `Depends(get_current_user)`
  one at a time without re-shaping the underlying service signatures
  beyond the new optional `actor: str | None` parameter.

**Negatives**

- **HS256 is not production-grade for browser-issued tokens.** The
  shared secret model does not survive a browser deployment. The
  ADR documents this explicitly; the production path is the 3DX
  handoff (deferred).
- **Three-mode complexity.** Operators have to understand three
  modes. An unset env var defaults to `dev` (fixed dev admin user)
  which is safe for local but not for shared deployments — the
  factory logs `auth.mode_selected` at startup so operators notice.
  `disabled` (open API) remains as an explicit escape hatch and the
  long-term shape collapses back to one or two modes once `bearer`
  (or its 3DX successor) is wired everywhere.
- **One more env-var-scoped secret.** `KW_AUTH_SECRET` joins
  `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` in the deployment
  surface.

**Neutrals**

- Forces every write route to take a `User` dep over time. The
  slicing plan (§4) keeps each PR small enough to review.
- Adds a `User` import to the route layer. No new transitive dep
  on the Python side; the verifier is stdlib-only.

## Alternatives considered

### Cookie-based session (Flask-Login / FastAPI-Users style)

**Rejected.** A stateless API + an embedded widget surface favors
bearer tokens: the widget cannot rely on the host page's cookies
(third-party cookie restrictions, the iframe sandbox), and the
service-to-service callers (Iterop) don't have a cookie jar at all.
Bearer is the natural shape for both.

### OAuth2 PKCE on top of the 3DX identity

**Deferred.** This is the production target — but the ADR for it
needs the 3DX docs in hand. Specifying PKCE today would either
guess at the 3DPassport authorization endpoint shape (likely wrong)
or block the audit-attribution work indefinitely. The bearer mode
lets us wire actor attribution now and revisit the auth scheme when
the 3DX docs are available.

### No-auth + IP allowlist

**Rejected.** Doesn't capture the actor. An IP allowlist is a
network-level access control, not an identity layer; the audit table
would still record `actor=null` for every write. This is the
no-progress option.

## References

- [#83](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/83) —
  Authentication, authorization, and 3DEXPERIENCE user context (this ADR's tracking issue).
- [#78](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/78) —
  Widget container / theme work; same Dassault docs dependency as the deferred 3DX-handoff ADR.
- [#215](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/215) —
  EPIC-A (HITL routing). The HITL router will read `User` to drive its decision-routing.
- [#216](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/216) —
  EPIC-B (external review systems / ITEROP). Relies on the bearer mode for callback authentication.
- [#218](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/218) —
  EPIC-D (multi-scope). Relies on `User.id` for the `personal:<user_id>` scope auto-creation; the workspace ADR (ADR-020) will pick up the tenant key from the same `User`.
- [ADR-013](ADR-013-llm-provider-and-no-langchain.md) — Protocol-behind-a-factory shape this ADR mirrors.
- [ADR-015](ADR-015-embedding-provider.md) — Same Protocol pattern, kept the dep surface intentionally small.
- [`docs/architecture/observability.md`](../architecture/observability.md) — Audit event vocabulary; this ADR adds `actor` to the `review.*` rows.
