"""Authentication scaffold (ADR-019).

Three operating modes selected by ``KW_AUTH_MODE``: ``disabled`` (the
current default — anonymous admin user, behaviour unchanged),
``dev`` (fixed identity for local dev / CI / demos), and ``bearer``
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

__all__ = [
    "ANONYMOUS_USER_ID",
    "AuthError",
    "AuthService",
    "BearerJWTAuthService",
    "DEFAULT_DEV_ROLE",
    "DEFAULT_DEV_USER_ID",
    "DevModeAuthService",
    "DisabledAuthService",
    "Role",
    "User",
    "build_auth_service",
    "encode_hs256",
    "get_current_user",
]
