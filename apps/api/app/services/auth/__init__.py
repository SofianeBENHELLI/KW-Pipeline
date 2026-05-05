"""Authentication scaffold (ADR-019).

Three operating modes selected by ``KW_AUTH_MODE``: ``dev`` (the
default — fixed ``"dev"`` admin user from ``KW_AUTH_DEV_USER``, keeps
the out-of-the-box demo flow open while attributing every review
decision to a recognisable actor), ``disabled`` (legacy escape hatch —
anonymous admin user, behaviour matches pre-ADR-019), and ``bearer``
(HS256 JWT, MVP only — production scheme is the deferred 3DX
context handoff).

The package exports the boundary types (:class:`User`, :class:`Role`,
:class:`AuthError`, :class:`AuthService`), the per-mode services, the
factory, and the FastAPI dependency. Everything else is an
implementation detail.
"""

from __future__ import annotations

from .bearer import BearerJWTAuthService, encode_hs256
from .dependencies import get_current_user
from .dev_mode import DEFAULT_DEV_ROLE, DEFAULT_DEV_USER_ID, DevModeAuthService
from .disabled import ANONYMOUS_USER_ID, DisabledAuthService
from .factory import build_auth_service
from .protocol import AuthError, AuthService, Role, User
from .scope_dependencies import assert_can_access_document, get_caller_scopes
from .scope_filter import (
    ALL_SCOPES_SENTINEL,
    ScopeAccessDenied,
    default_scopes_for,
    resolve_caller_scopes,
    user_can_access,
)

__all__ = [
    "ALL_SCOPES_SENTINEL",
    "ANONYMOUS_USER_ID",
    "AuthError",
    "AuthService",
    "BearerJWTAuthService",
    "DEFAULT_DEV_ROLE",
    "DEFAULT_DEV_USER_ID",
    "DevModeAuthService",
    "DisabledAuthService",
    "Role",
    "ScopeAccessDenied",
    "User",
    "assert_can_access_document",
    "build_auth_service",
    "default_scopes_for",
    "encode_hs256",
    "get_caller_scopes",
    "get_current_user",
    "resolve_caller_scopes",
    "user_can_access",
]
