"""Scope-filter primitives for the read + write endpoints (EPIC-D D.5).

ADR-020 §2 defines the read-side filter: every list / get / search /
graph / catalog endpoint shows the caller only documents linked to a
scope they have access to. This module owns three primitives:

- :func:`default_scopes_for` — the implicit "what the caller sees by
  default" set. Today that's exactly ``personal:<user.id>``; D.3 will
  fold in 3DSwym community membership and ``project`` membership once
  those clients ship.
- :func:`resolve_caller_scopes` — turn the optional ``scope_kind`` /
  ``scope_ref`` query params into the effective scope set, raising
  :class:`ScopeAccessDenied` (HTTP 403) when the request asks for a
  scope the caller cannot reach. ``KW_AUTH_MODE=disabled`` returns the
  :data:`ALL_SCOPES_SENTINEL` so the legacy escape hatch keeps seeing
  everything.
- :func:`user_can_access` — a single document accessibility check used
  by ``/documents/{id}/...`` paths. Returns ``False`` (the route layer
  maps that to a 404 — hidden-existence semantics) when the user's
  effective scope set has no overlap with the document's recorded
  scope links.

The "disabled-mode bypass" is keyed off ``KW_AUTH_MODE``, NOT off
``user.role == "admin"``. Per the slice spec (2026-05-04): ``dev`` mode
also stamps an admin user, but its purpose is solo-dev iteration where
scope filtering is the whole point of the feature. Only the explicit
``disabled`` legacy mode skips the filter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.errors import ApiError, ErrorCode
from app.schemas.scope import SCOPE_KINDS, Scope, ScopeRef
from app.settings import Settings

from .protocol import User

if TYPE_CHECKING:
    from app.services.catalog_store import CatalogStore


class ScopeAccessDenied(Exception):
    """Caller asked for a scope they cannot reach.

    The route layer maps this to HTTP 403 via :class:`ApiError` so the
    public envelope (``KW_FORBIDDEN``) lights up. The exception carries
    a ``message`` and ``remediation`` so callers don't have to recompute
    them at the route boundary.
    """

    def __init__(self, *, message: str, remediation: str) -> None:
        super().__init__(message)
        self.message = message
        self.remediation = remediation


# Sentinel returned by :func:`resolve_caller_scopes` for the legacy
# ``KW_AUTH_MODE=disabled`` mode. Filter callers that receive this
# tuple skip the scope predicate entirely (no rows are hidden), which
# matches the "open-API back-compat" promise of disabled mode.
ALL_SCOPES_SENTINEL: tuple[ScopeRef, ...] = ()


def _is_disabled_mode(settings: Settings) -> bool:
    """Return ``True`` when ``KW_AUTH_MODE=disabled`` is in effect.

    Reads the env-derived :class:`Settings` directly so a test that
    monkeypatches ``KW_AUTH_MODE`` mid-suite is observed on the next
    request without re-wiring the auth service.
    """
    return settings.auth_mode.strip().lower() == "disabled"


def default_scopes_for(user: User) -> list[Scope]:
    """Return the implicit scope set for ``user`` when no params are passed.

    Today this is exactly ``personal:<user.id>``. D.3 will fold in
    3DSwym community membership (``swym_community:<community_id>`` for
    every community the user belongs to) and ``project`` membership.

    The returned :class:`Scope` instances synthesise ``added_at`` /
    ``added_by`` because they are not catalog rows — they're the set of
    scopes the caller can see, not the per-document link records. The
    scope predicate downstream only consults ``kind`` / ``ref``.
    """
    now = datetime.now(UTC)
    return [
        Scope(
            kind="personal",
            ref=user.id,
            added_at=now,
            added_by=user.id,
        )
    ]


def resolve_caller_scopes(
    user: User,
    scope_kind: str | None,
    scope_ref: str | None,
    *,
    settings: Settings,
) -> tuple[ScopeRef, ...]:
    """Resolve the effective ``(kind, ref)`` set the caller may see.

    Policy (per user 2026-05-05):

    - ``KW_AUTH_MODE=disabled`` short-circuits to the
      :data:`ALL_SCOPES_SENTINEL` (skip the filter entirely). Documented
      in :mod:`app.services.auth.disabled` as the legacy escape hatch.
    - No params → implicit default
      (``personal:<user.id>`` for now; D.3 widens this).
    - Explicit ``personal:<user.id>`` → allowed (caller's own personal
      scope).
    - Explicit ``personal:<other_user_id>`` → 403, no cross-user reads.
    - Explicit ``swym_community:<any>`` → 403 until D.3 wires the Swym
      membership client (see ADR-026). Same for ``project:<any>``.
    - Half-pair (one of ``scope_kind`` / ``scope_ref`` set, the other
      not) → 403 with a clear remediation string. The route layer
      surfaces 422 for half-pairs *on writes* (upload route); on reads
      we treat it as a forbidden ask so a malformed query never silently
      degrades to "no filter".
    - Unknown ``scope_kind`` → 403 (same envelope; the params live
      together so the policy is "you can't see scopes that aren't yours
      yet", not "your typo is a 422").

    Raises :class:`ScopeAccessDenied` when the caller's request must
    be refused.
    """
    if _is_disabled_mode(settings):
        return ALL_SCOPES_SENTINEL

    has_kind = scope_kind is not None and scope_kind != ""
    has_ref = scope_ref is not None and scope_ref != ""

    if not has_kind and not has_ref:
        # Caller did not pin a scope — fall through to the implicit
        # default set. Today that's just personal:<user.id>; D.3
        # extends it without changing this branch.
        return tuple(ScopeRef(kind=s.kind, ref=s.ref) for s in default_scopes_for(user))

    if has_kind != has_ref:
        # Half-pair: refuse with a 403 envelope that points at the
        # documented "send both or neither" rule. Mirrors the upload
        # route's 422 reasoning but uses 403 because *on read* the
        # filter must be deterministic — a half-set pair would silently
        # degrade to "no filter" otherwise.
        raise ScopeAccessDenied(
            message="scope_kind and scope_ref must be provided together.",
            remediation=(
                "Send both scope_kind and scope_ref query params, or "
                "neither (the read defaults to your personal scope)."
            ),
        )

    if scope_kind not in SCOPE_KINDS:
        raise ScopeAccessDenied(
            message=(f"scope_kind '{scope_kind}' is not accessible to this user."),
            remediation=(
                "Pick one of the documented scope kinds you have access "
                "to: 'personal' for your own user id. Cross-community / "
                "cross-project reads are not supported until the Swym "
                "membership client lands (ADR-026 / EPIC-D D.3)."
            ),
        )

    # ``scope_kind`` is one of "personal" / "swym_community" /
    # "project" at this point.
    if scope_kind == "personal":
        if scope_ref == user.id:
            return (ScopeRef(kind="personal", ref=user.id),)
        raise ScopeAccessDenied(
            message="Cross-user personal scope reads are not allowed.",
            remediation=(
                "Only the owner can read their own personal scope. To "
                "read another user's documents, request access through a "
                "shared scope once 3DSwym membership lookup is wired "
                "(EPIC-D D.3)."
            ),
        )

    # ``swym_community`` and ``project`` both require a membership
    # lookup we don't have yet. TODO(EPIC-D D.3, ADR-026): swap the
    # 403 below for a real membership check once the Swym /
    # 3DPassport membership client is wired.
    raise ScopeAccessDenied(
        message=(
            f"Membership-gated scope kind '{scope_kind}' is not yet supported. "
            "Cross-community / cross-project reads ship in D.3."
        ),
        remediation=(
            "Drop the scope_kind / scope_ref query params to fall back to "
            "your personal scope. Community and project visibility ship "
            "with the 3DSwym membership client (ADR-026 / EPIC-D D.3)."
        ),
    )


def user_can_access(
    user: User,
    document_id: str,
    catalog: CatalogStore,
    *,
    settings: Settings,
) -> bool:
    """Return whether ``user`` can see ``document_id`` under the current policy.

    Used by ``/documents/{id}/...`` paths to enforce hidden-existence
    semantics: a route that calls this and gets ``False`` should raise
    HTTP 404 (not 403), so the API doesn't leak the existence of other
    users' content to enumeration probes.

    Returns ``True`` unconditionally under ``KW_AUTH_MODE=disabled``
    so the legacy escape hatch keeps seeing every document.

    A document with **no** scope links is treated as inaccessible (the
    upload route always writes at least the personal-scope link, so a
    scope-less row is either pre-D.1 legacy data or a bug — either way
    the safe default is "hidden"). The ``disabled`` bypass still lets
    operators read these rows for audit / migration purposes.

    Archived documents (``Document.archived_at IS NOT NULL``, ADR-020
    §4) are also hidden under any non-disabled mode — the catalog read
    methods already filter these out, but ``user_can_access`` is the
    last-line check used by per-document routes that don't go through
    ``list_documents``, so the archive flag is enforced here too. The
    catalog's ``get_document`` returns ``None`` for archived rows, so a
    ``get_document(...) is None`` short-circuit here is equivalent to
    "row is missing OR archived" — both deserve the same hidden-404
    treatment.
    """
    if _is_disabled_mode(settings):
        return True

    document = catalog.get_document(document_id)
    if document is None:
        # Either truly missing or archived — both render as "hidden" to
        # the caller. The 404 the route layer emits in response is the
        # documented hidden-existence behaviour.
        return False

    document_scopes = catalog.list_scopes_for_document(document_id)
    if not document_scopes:
        return False

    allowed = {(s.kind, s.ref) for s in default_scopes_for(user)}
    return any((s.kind, s.ref) in allowed for s in document_scopes)


def scope_access_denied_to_api_error(exc: ScopeAccessDenied) -> ApiError:
    """Translate :class:`ScopeAccessDenied` into the public 403 envelope.

    Centralised here so every route surface uses the same envelope code
    (``KW_FORBIDDEN``) and remediation pattern. The route layer raises
    the returned :class:`ApiError`; FastAPI's exception handler emits
    the JSON body.
    """
    return ApiError(
        status_code=403,
        code=ErrorCode.FORBIDDEN,
        message=exc.message,
        retryable=False,
        remediation=exc.remediation,
    )


__all__ = [
    "ALL_SCOPES_SENTINEL",
    "ScopeAccessDenied",
    "default_scopes_for",
    "resolve_caller_scopes",
    "scope_access_denied_to_api_error",
    "user_can_access",
]
