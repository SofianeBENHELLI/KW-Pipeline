# ADR-026: Swym Membership Lookup — Live REST with Per-Request Memoisation and Circuit Breaker

## Status

**Proposed**, 2026-05-04. Codifies the membership-lookup decision
taken in the 2026-05-04 Q&A round
([`docs/roadmap/2026-05-04-hitl-and-extensions.md`](../roadmap/2026-05-04-hitl-and-extensions.md)
§4, Q4.3) and required by EPIC-D (#218). Companion to
[ADR-020](ADR-020-workspace-scoping.md).

## Context

[ADR-020](ADR-020-workspace-scoping.md) §5 commits to a server-side
scope filter on every list / search / graph / chat / catalog
endpoint. For the `swym_community` flavor of scope, that filter has
to answer one question on every request that touches a community
scope:

> Is user `U` a member of 3DSwym community `C`?

3DSwym is the source of truth for community membership. KW Pipeline
does not own the membership graph, and there is no event stream we
can subscribe to that would give us a reliable mirror without
non-trivial reconciliation infrastructure.

The lookup needs to be **correct** — a user removed from a community
must lose access on the next request, not on the next cache flush.
Permission staleness in a content product carries real
data-visibility implications: a user who was removed from a
sensitive-content community must not see one more search result from
it. At the same time, the lookup needs to be **survivable** under
load: a user-request that touches several community scopes must not
fan out into one 3DSwym call per scope per request, because that
collapses both the latency budget and the rate-limit headroom 3DSwym
gives us.

This ADR specifies how the lookup is performed at runtime and how the
system behaves when 3DSwym is degraded.

## Decision

### 1. Live REST on every request, per-request memoisation only

Membership is resolved by **live REST call to the 3DSwym membership
API** on each FastAPI request that needs it. There is **no
cross-request cache**. Within a single request, an in-flight memo
ensures that if the same lookup is requested multiple times during
the resolution of one request, the underlying 3DSwym call is made
once and reused.

The memo lives on a **request-scoped FastAPI dependency** keyed by
the request id. When the request ends, the memo is discarded with
the request. A burst of requests for the same user all hit 3DSwym
independently — that is the intended behavior.

A typical request that touches `N` scopes makes at most:

- 1 call to `list_user_communities(user_id)` to resolve the user's
  community set, OR
- up to `N` calls to `is_member(user_id, community_id)` if the
  predicate path is hit instead.

Identical lookups inside the same request are deduplicated. Different
lookups inside the same request are not.

### 2. No cross-request cache

A 60-second TTL was considered and rejected. Even 60 seconds of stale
permissions is unacceptable for a content product where scope changes
carry data-visibility implications: a user removed from a community
should not be able to issue a query that returns content from that
community within the next minute.

The cross-request cache is not a permanent prohibition — if 3DSwym
becomes a measurable bottleneck under real production load, this
decision can be revisited with concrete latency and call-volume data.
For v1, the conservative posture is the correct one.

### 3. Circuit breaker around the 3DSwym client

The 3DSwym client is wrapped in a standard half-open circuit breaker
with admin-tunable `failure_threshold` and `recovery_timeout`
parameters. The breaker has three states:

- **Closed** — calls flow through; failures increment a counter.
- **Open** — calls short-circuit immediately without hitting 3DSwym;
  the API responds with a degraded posture (see below).
- **Half-open** — after `recovery_timeout`, one probe call is allowed
  through; success closes the breaker, failure re-opens it.

When the breaker is **open**, the API behaves as follows:

- **Read endpoints** (search, graph, chat, catalog, list): fail
  closed for `swym_community` scopes. Documents in `swym_community`
  scopes are filtered out of the result set as if the user had no
  community membership. Documents in `personal` and `project` scopes
  are unaffected. The response carries an explicit
  `swym_membership_unavailable` warning so the client can surface a
  banner.
- **Write endpoints** that target a `swym_community` scope (e.g. an
  upload addressed to community `C`): respond with
  `503 service_unavailable` and an error envelope whose
  `code = "swym_membership_unavailable"` and a remediation hint
  pointing at the operator-facing status page. The user can retry, or
  switch the upload to their `personal` or a `project` scope.

The breaker fails closed in both directions: outage of 3DSwym never
results in a user seeing content they would not otherwise have access
to, only in content being temporarily hidden.

### 4. Implementation surface

```python
class MembershipClient(Protocol):
    def is_member(self, user_id: str, community_id: str) -> bool: ...
    def list_user_communities(self, user_id: str) -> list[ScopeRef]: ...
```

Two concrete implementations:

- `LiveSwymMembershipClient` — wraps a `httpx.Client` against the
  3DSwym membership API. Uses HTTP keep-alive to amortise TLS handshake
  cost across the calls a single user-request makes. Wraps every
  outbound call in the circuit breaker.
- `FakeMembershipClient` — in-memory map of `user_id ->
  list[community_id]`, used by the unit suite. Matches the project
  pattern set by `FakeLLMClient` (ADR-013) and `InMemoryGraphStore`
  (ADR-012): one Protocol, one production impl, one fake.

Per-request memoisation lives on a request-scoped FastAPI dependency
that wraps whichever `MembershipClient` is configured. The dependency
materialises a small cache dict keyed by `(method, *args)`; the
cache's lifetime is the request's lifetime. The wrapped client never
sees the cache.

New env vars (read through `app.settings.Settings`):

- `SWYM_API_URL` — required to construct `LiveSwymMembershipClient`.
- `SWYM_API_TIMEOUT_SECONDS` — per-call timeout, default `2.0`.
- `SWYM_BREAKER_FAILURE_THRESHOLD` — consecutive failures before
  opening, default `5`.
- `SWYM_BREAKER_RECOVERY_TIMEOUT_SECONDS` — open-to-half-open delay,
  default `30`.

Authentication to 3DSwym (3DPassport / Run Your App token) is
configured the same way the rest of the 3DEXPERIENCE-facing surface
is configured today (#202); this ADR does not re-litigate that.

## Consequences

- **Positive — zero permission staleness.** A user removed from a
  community loses visibility on the next request. There is no cache
  flush to wait for and no reconciliation drift to debug.
- **Positive — outage isolation.** When 3DSwym is degraded, the
  breaker contains the failure to the `swym_community` scopes only.
  `personal` and `project` scopes keep working, so users can still
  read their own content and ingest into projects during a 3DSwym
  outage.
- **Positive — simple test seam.** The `MembershipClient` Protocol
  matches the project's existing pattern. The unit suite swaps in a
  `FakeMembershipClient` and never touches the network. Integration
  tests against real 3DSwym live behind a `pytest -m
  swym_integration` marker, opt-in only, mirroring the pattern from
  ADR-012 and ADR-015.
- **Negative — hot-path latency.** Every read-or-write that touches a
  community scope pays at least one 3DSwym round-trip. The
  per-request memo collapses fan-out within a request, but the first
  call still happens on the request's critical path. HTTP keep-alive
  to a stable endpoint is the main mitigation; `voyage-3` retrieval
  (ADR-015) and Anthropic chat calls (ADR-013) are already on the
  request hot path, so the marginal latency cost of one more
  out-of-process call is small relative to the dominant Phase 3
  costs.
- **Cost — N membership calls per active user-request.** For the
  embedded 3DEXPERIENCE pilot deployment we expect on the order of
  tens of concurrent users, each issuing single-digit
  community-touching requests per minute, so the call volume to
  3DSwym is on the order of low hundreds of calls per minute. Within
  the rate-limit budget any reasonable 3DSwym tier provides. If usage
  outgrows that envelope the alternatives in §Alternatives become
  worth revisiting.

## Alternatives considered

### Cross-request cache with TTL (e.g. 60 seconds)

Cache the membership result in-process for 60 seconds keyed by
`(user_id, community_id)`. Drops the call rate to 3DSwym by roughly
the cache hit ratio.

Rejected for v1. Permission caches are an anti-pattern in
content-isolation contexts: a user removed from a community
continues to read content from it for up to TTL seconds. The
operator has no way to force a flush short of restarting the API
process. The product's data-visibility guarantees are weakened to
"eventually consistent within TTL", which is not the contract the
3DSwym community owners expect when they remove a member.

Revisit only if 3DSwym becomes a measurable bottleneck. At that
point a TTL of seconds (not minutes) plus a forced-flush admin
endpoint becomes a viable mitigation, but only with explicit product
sign-off on the staleness window.

### Webhook-driven membership mirror

3DSwym pushes membership-change events to a KW Pipeline webhook
receiver; we maintain a local mirror table and read against it.
Eliminates the per-request 3DSwym call entirely; turns membership
into a local index lookup.

Rejected for v1. The infrastructure cost is too high for the pilot:
a webhook receiver, a mirror table, drift reconciliation (because
webhooks miss), an initial-sync mechanism for users who exist before
the mirror is bootstrapped, and a replay path for missed deliveries.
Each of those is a real piece of code and a real new failure mode.
This is a credible **future** optimization on top of this ADR — the
`MembershipClient` Protocol gives us the seam to swap in a
`MirroredSwymMembershipClient` without touching call sites — but it
is not v1 work.

### Stamping membership at upload time

Resolve the user's communities at upload and store the resolved set
on the document version itself; serve reads against the stamped
copy.

Rejected. The stamping is correct at upload time and immediately
wrong afterwards: a user who **leaves** a community after upload
keeps seeing its content because the stamp does not change, and a
user who **joins** a community after the upload never sees its
content because the stamp predates them. Both directions are
incorrect. Membership must be resolved on the read, not on the
write.

## References

- [ADR-020](ADR-020-workspace-scoping.md) — Workspace scoping. The
  read filter in ADR-020 §5 is what consumes this ADR's
  `MembershipClient`.
- [ADR-012](ADR-012-knowledge-graph-layer.md) — Establishes the
  Protocol-plus-fake pattern this ADR follows.
- [ADR-013](ADR-013-llm-provider-and-no-langchain.md) — Same pattern
  for the LLM client; `LiveSwymMembershipClient` matches its shape.
- [#218](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/218) —
  EPIC-D — Multi-scope ingestion. Parent epic.
- [#202](https://github.com/SofianeBENHELLI/KW-Pipeline/pull/202) —
  3DEXPERIENCE-reachable backend deployment; sets the auth plumbing
  this ADR's 3DSwym calls ride on.
- [`docs/roadmap/2026-05-04-hitl-and-extensions.md`](../roadmap/2026-05-04-hitl-and-extensions.md)
  §4 — Source of truth for the decisions codified in this ADR.
