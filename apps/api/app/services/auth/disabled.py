"""``DisabledAuthService`` — current default for backward compatibility.

ADR-019 §2 defines three operating modes:

- ``disabled`` (this module) — anonymous user with ``admin`` role.
  Behaviour is unchanged from pre-ADR-019: every existing test, demo
  seed script, and frontend call works without setting any env var.
  This mode is the default during the MVP transition and will be
  removed once ``bearer`` is the default and every write surface ships
  auth-aware UI.
- ``dev`` — see :mod:`app.services.auth.dev_mode`.
- ``bearer`` — see :mod:`app.services.auth.bearer`.

The service emits a single startup-style warning the first time
``authenticate`` runs so an operator who deployed without flipping the
mode does not silently keep an open API.
"""

from __future__ import annotations

import logging
from typing import Any

from .protocol import User

log = logging.getLogger(__name__)

ANONYMOUS_USER_ID = "anonymous"
"""The id stamped on audit events when no real principal is configured.
Stable string so a future "find rows that ran in disabled mode"
question is one SQL filter."""


class DisabledAuthService:
    """No-op auth: every request is the same anonymous admin user.

    Returned by :func:`app.services.auth.factory.build_auth_service`
    when ``KW_AUTH_MODE`` is unset or ``"disabled"`` (the current
    default). ``admin`` is the role to keep parity with today's
    behaviour where every endpoint is open.
    """

    name: str = "disabled"

    def __init__(self) -> None:
        # Track whether we've already logged the loud warning so a
        # high-traffic API doesn't spam the log on every request.
        self._warned: bool = False

    def authenticate(self, request: Any) -> User:  # noqa: ARG002 - request unused
        if not self._warned:
            log.warning(
                "auth.disabled_mode_active",
                extra={
                    "auth_mode": self.name,
                    "remediation": (
                        "Set KW_AUTH_MODE=dev or KW_AUTH_MODE=bearer "
                        "before exposing this API outside trusted networks. "
                        "See docs/adr/ADR-019-authentication-and-authorization.md."
                    ),
                },
            )
            self._warned = True
        return User(id=ANONYMOUS_USER_ID, role="admin", claims={})


__all__ = ["DisabledAuthService", "ANONYMOUS_USER_ID"]
