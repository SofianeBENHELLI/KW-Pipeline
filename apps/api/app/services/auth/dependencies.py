"""FastAPI dependency that resolves the active :class:`User` per request.

Designed so route handlers add a single ``current_user: User =
Depends(get_current_user)`` parameter to opt into auth. The
:class:`AuthService` is read from ``request.app.state.services.auth``
(set in :func:`app.main.create_app`) so the same dependency works in
in-memory tests (their ``TestClient`` shares the app's services
container) and in production wirings.

ADR-019 §5: this dependency raises HTTP 401 when authentication
fails. Role enforcement (HTTP 403 for insufficient role) is a
follow-up slice — this module only resolves identity.
"""

from __future__ import annotations

from fastapi import Request

from app.errors import ApiError, ErrorCode

from .protocol import AuthError, AuthService, User


def get_current_user(request: Request) -> User:
    """Authenticate ``request`` and return the resolved :class:`User`.

    Looks up the active :class:`AuthService` from
    ``request.app.state.services.auth`` — this is the same container
    every other route already reads (graph store, semantic outputs,
    audit events). Pulling auth from there means tests that build a
    custom :class:`PipelineServices` automatically pick up the
    matching auth mode without extra wiring.

    Raises:
        ApiError(401): when :class:`AuthService.authenticate` raises
            :class:`AuthError`. The envelope uses
            :data:`ErrorCode.UNAUTHORIZED` so the frontend can display
            a stable "session expired / sign in" surface (deferred to
            a follow-up slice; the backend contract is in place now).
    """
    auth: AuthService = request.app.state.services.auth
    try:
        return auth.authenticate(request)
    except AuthError as exc:
        raise ApiError(
            status_code=401,
            code=ErrorCode.UNAUTHORIZED,
            message=str(exc) or "missing or invalid token",
            retryable=False,
            remediation=(
                "Provide a valid Authorization: Bearer <jwt> header "
                "signed with KW_AUTH_SECRET. See ADR-019 for the "
                "claim shape."
            ),
        ) from exc


__all__ = ["get_current_user"]
