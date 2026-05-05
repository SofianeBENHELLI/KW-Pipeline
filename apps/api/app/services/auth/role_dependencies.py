"""Role-gated FastAPI dependency (ADR-019 §3 — slice 2 enforcement).

:func:`require_role` returns a dependency that resolves the active
:class:`User` via :func:`get_current_user` and asserts the caller's
role rank is at least the configured minimum. Higher-rank roles
inherit lower-rank permissions: an admin can do everything a reviewer
can, etc. Insufficient rank → HTTP 403 with the project-standard
``KW_FORBIDDEN`` envelope (same shape :func:`get_caller_scopes`
already emits, so the frontend renders one error path for both
"out of scope" and "wrong role").

Identity resolution itself stays in :mod:`dependencies` — this module
adds role enforcement *above* it. The existing access checks (the
D.5 scope filter) and this role check stack: a route declares
``Depends(require_role("reviewer"))`` and still calls
``assert_can_access_document`` for the per-document hidden-existence
check.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends

from app.errors import ApiError, ErrorCode

from .dependencies import get_current_user
from .protocol import Role, User

# Numeric ranks for the four canonical roles. ``viewer`` is the
# lowest-privilege bucket and ``admin`` is the highest. Higher-rank
# roles inherit lower-rank permissions: a route gated on
# ``contributor`` accepts ``contributor``, ``reviewer``, and ``admin``.
#
# Kept as a plain dict (not an Enum) so the role strings stay the
# canonical wire form — the audit table, the JWT claim, and this
# rank table all share the exact same vocabulary.
ROLE_RANK: dict[Role, int] = {
    "viewer": 0,
    "contributor": 1,
    "reviewer": 2,
    "admin": 3,
}


def require_role(min_role: Role) -> Callable[[User], User]:
    """FastAPI dependency factory that enforces a minimum role rank.

    Returns a dependency that pulls the active :class:`User` (via
    :func:`get_current_user`, which still emits the 401 envelope when
    auth itself fails) and raises :class:`ApiError` 403 with
    :data:`ErrorCode.FORBIDDEN` when the caller's role rank is below
    the threshold. The resolved :class:`User` is returned on success
    so a route handler can replace its existing
    ``Depends(get_current_user)`` parameter without re-wiring.

    The 403 envelope mirrors the shape ``scope_access_denied_to_api_error``
    already uses (``KW_FORBIDDEN`` code, project-standard message /
    remediation / retryable=False) so the frontend renders one
    "you don't have permission" surface for both role and scope
    rejections.
    """

    def dep(user: User = Depends(get_current_user)) -> User:  # noqa: B008 - FastAPI dep wiring
        if ROLE_RANK[user.role] < ROLE_RANK[min_role]:
            raise ApiError(
                status_code=403,
                code=ErrorCode.FORBIDDEN,
                message=(f"Role '{user.role}' lacks permission; '{min_role}' or higher required."),
                retryable=False,
                remediation=(
                    "Ask an admin to grant a role with sufficient "
                    "privilege, or sign in with an account that has "
                    "the required role. See ADR-019 §3 for the role "
                    "matrix."
                ),
            )
        return user

    return dep


# Pre-bound dependency callables for the four canonical roles. Routes
# import these directly so :func:`require_role` is never called inside
# an argument default (avoiding ruff B008 — which forbids in-default
# function calls because they execute at module-import time, before
# tests have a chance to monkeypatch the surrounding state).
require_viewer = require_role("viewer")
require_contributor = require_role("contributor")
require_reviewer = require_role("reviewer")
require_admin = require_role("admin")


__all__ = [
    "ROLE_RANK",
    "require_admin",
    "require_contributor",
    "require_reviewer",
    "require_role",
    "require_viewer",
]
