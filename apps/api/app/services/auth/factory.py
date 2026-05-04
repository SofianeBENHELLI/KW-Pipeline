"""Pick the active :class:`AuthService` based on ``KW_AUTH_MODE``.

ADR-019 §2 defines three modes:

- ``dev`` (default): :class:`DevModeAuthService` — fixed identity from
  ``KW_AUTH_DEV_USER`` (falls back to a ``"dev"`` admin user). Keeps
  the out-of-the-box demo flow open while attributing every review
  decision to a recognisable actor in the audit log.
- ``disabled``: :class:`DisabledAuthService` — anonymous user with
  ``admin`` role. Legacy escape hatch for callers that have not yet
  migrated; loud startup warning.
- ``bearer``: :class:`BearerJWTAuthService` — HS256 JWT validated
  against ``KW_AUTH_SECRET``.

The factory is the only place that mentions every concrete impl, so
adding a fourth mode (e.g. the future 3DX context handoff) is one
``elif`` branch plus the new module.
"""

from __future__ import annotations

import logging

from app.settings import Settings

from .bearer import BearerJWTAuthService
from .dev_mode import DevModeAuthService
from .disabled import DisabledAuthService
from .protocol import AuthService

log = logging.getLogger(__name__)


def build_auth_service(settings: Settings | None = None) -> AuthService:
    """Construct the auth service the running app should use.

    Reads :class:`Settings` per call so per-test ``monkeypatch.setenv``
    is honoured; the cost is negligible (one env walk).

    Raises :class:`RuntimeError` for unknown modes — better to fail
    fast at startup than to silently fall back to ``disabled`` and
    leave an open API.
    """
    settings = settings or Settings()
    mode = settings.auth_mode.strip().lower() or "dev"

    if mode == "disabled":
        log.warning(
            "auth.mode_selected",
            extra={
                "auth_mode": "disabled",
                "remediation": (
                    "KW_AUTH_MODE is unset or 'disabled'. The API accepts "
                    "every request as the anonymous admin user. Set "
                    "KW_AUTH_MODE=dev or KW_AUTH_MODE=bearer before "
                    "exposing this deployment."
                ),
            },
        )
        return DisabledAuthService()

    if mode == "dev":
        log.info(
            "auth.mode_selected",
            extra={"auth_mode": "dev", "dev_user": settings.auth_dev_user.strip() or "dev"},
        )
        return DevModeAuthService(user_id=settings.auth_dev_user)

    if mode == "bearer":
        # Construction enforces the secret precondition; let the
        # ``RuntimeError`` propagate so a misconfigured deployment
        # fails fast at app startup.
        service = BearerJWTAuthService(secret=settings.auth_secret)
        log.info("auth.mode_selected", extra={"auth_mode": "bearer"})
        return service

    raise RuntimeError(
        f"Unknown KW_AUTH_MODE={mode!r}; expected one of 'disabled' / 'dev' / 'bearer'."
    )


__all__ = ["build_auth_service"]
