"""``DisabledAuthService`` — legacy escape hatch for back-compat.

ADR-019 §2 defines three operating modes:

- ``dev`` (default) — fixed identity from ``KW_AUTH_DEV_USER``. See
  :mod:`app.services.auth.dev_mode`.
- ``disabled`` (this module) — anonymous user with ``admin`` role.
  Behaviour matches pre-ADR-019: every write endpoint accepts every
  caller. Kept as an explicit opt-in for callers that haven't yet
  switched to ``dev`` or ``bearer``; it will be removed once nothing
  in CI / docs / dashboards still asks for it.
- ``bearer`` — see :mod:`app.services.auth.bearer`.

The service emits a single startup-style warning the first time
``authenticate`` runs so an operator who explicitly opted into
``disabled`` (or whose tooling still sets it) does not silently keep
an open API.
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
    when ``KW_AUTH_MODE="disabled"`` is explicitly set (legacy escape
    hatch). ``admin`` is the role to keep parity with pre-ADR-019
    behaviour where every endpoint was open.
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
