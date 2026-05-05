"""Auth boundary types: ``User``, ``Role``, ``AuthError``, ``AuthService``.

ADR-019 introduces a single authentication boundary that every write
endpoint will eventually depend on. The boundary is a Protocol — same
shape as :class:`app.services.knowledge.LLMClient` and
:class:`app.services.knowledge.EmbeddingClient` — so concrete modes
(``disabled`` / ``dev`` / ``bearer``) plug in without churn at the
call sites.

This module is deliberately tiny. The ``User`` payload carries only
the fields the audit layer and (eventual) role-gating needs; callers
that want richer claims compose the underlying token themselves and
project into this shape.

See ``docs/adr/ADR-019-authentication-and-authorization.md`` for the
contract this Protocol implements and the deferred 3DEXPERIENCE
context-handoff ADR that supersedes ``bearer`` in production.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# The four canonical roles. Stored as a plain :class:`Literal` so call
# sites can pattern-match on them and so the audit payload is a simple
# string. Role-gating beyond the actor-on-review path is explicitly
# deferred (see ADR-019 §3) — the strings exist now so future PRs add
# enforcement without rewriting the User contract.
Role = Literal["viewer", "contributor", "reviewer", "admin"]


@dataclass(frozen=True)
class User:
    """The authenticated principal for one request.

    ``id`` is the stable identifier we record in audit events ("who
    validated doc X"). ``role`` drives the future role-gating layer.
    ``claims`` holds the original token claim dict (or an empty dict
    for ``disabled`` / ``dev`` modes) so a later PR can read richer
    fields (display name, tenant, 3DX collaborative-space) without
    breaking this Protocol.
    """

    id: str
    role: Role
    claims: dict[str, Any] = field(default_factory=dict)


class AuthError(Exception):
    """Raised by ``AuthService.authenticate`` when the request lacks
    valid credentials.

    The route layer translates this to HTTP 401 with the stable error
    envelope (``ApiError`` / ``ErrorCode.UNAUTHORIZED``) — same shape
    the rest of the API already uses for client-side errors. Message
    strings are intentionally generic (e.g. "missing or invalid token")
    to avoid leaking which check failed.
    """


@runtime_checkable
class AuthService(Protocol):
    """Authenticate the principal behind one HTTP request.

    Implementations MUST be safe to share across requests — FastAPI
    dispatches handlers across the thread pool and the dependency is
    constructed once per app. State (clock, secret bytes) is set at
    construction time; per-call work happens inside :meth:`authenticate`.

    Implementations MUST NOT raise anything other than
    :class:`AuthError` for credential-related failures. Configuration
    failures (missing secret, malformed env var) raise at construction
    time so the operator notices at startup, not on the first 401.
    """

    name: str
    """Human-readable mode tag, used in startup logs and the ``X-Auth-Mode``
    debug header (future). One of ``"disabled"`` / ``"dev"`` /
    ``"bearer"`` today; future modes (``"3dx"``) MUST pick a fresh tag."""

    def authenticate(self, request: Any) -> User:
        """Resolve the principal for ``request``.

        ``request`` is a duck-typed FastAPI :class:`starlette.requests.Request`
        in production; tests pass a small fake with the headers they
        care about. Concrete impls read whatever they need (``Authorization``
        header for ``bearer``, env-derived identity for ``dev``) and
        return a :class:`User`.

        Raises:
            AuthError: when the request lacks valid credentials.
        """


__all__ = ["AuthError", "AuthService", "Role", "User"]
