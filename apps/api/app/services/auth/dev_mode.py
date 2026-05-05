"""``DevModeAuthService`` — fixed identity for local dev / CI / demos.

ADR-019 §2 ``dev`` mode. The service reads the optional
``KW_AUTH_DEV_USER`` env var to pick the identity it stamps on every
request. The default user (``id="dev"``, ``role="admin"``) keeps the
out-of-the-box demo flow open while still attributing audit events to
a recognisable actor — so a presenter can validate / reject documents
during a demo and the audit table records ``actor="dev"`` instead of a
random anonymous string.

This mode is **not** for production. It is the auth-equivalent of
``InMemoryGraphStore`` — a deterministic shape that lets the test
suite exercise the actor-on-audit path without configuring a token.
"""

from __future__ import annotations

import logging
from typing import Any

from .protocol import Role, User

log = logging.getLogger(__name__)

DEFAULT_DEV_USER_ID = "dev"
"""Id used when ``KW_AUTH_DEV_USER`` is unset. Picks a short stable
string so test assertions against the audit payload don't have to
mirror the system username."""

DEFAULT_DEV_ROLE: Role = "admin"
"""Role assigned to the dev user. ``admin`` because the dev mode is the
"contributor's local" shape — the contributor has every permission on
their own machine."""


class DevModeAuthService:
    """Return a fixed dev identity for every request.

    Construct from :class:`app.settings.Settings` (or pass ``user_id``
    directly in tests). The startup log lights up once on first
    ``authenticate`` so an operator who left this enabled in a
    non-local environment notices.
    """

    name: str = "dev"

    def __init__(
        self,
        *,
        user_id: str | None = None,
        role: Role = DEFAULT_DEV_ROLE,
    ) -> None:
        # Normalise: an env var that's set but blank shouldn't override
        # the default to ``""`` — that would land empty actor strings
        # in the audit table.
        normalised = (user_id or "").strip() or DEFAULT_DEV_USER_ID
        self._user = User(id=normalised, role=role, claims={"source": "dev"})
        self._warned: bool = False

    def authenticate(self, request: Any) -> User:  # noqa: ARG002 - request unused
        if not self._warned:
            log.warning(
                "auth.dev_mode_active",
                extra={
                    "auth_mode": self.name,
                    "dev_user": self._user.id,
                    "remediation": (
                        "Dev-mode auth is for local development, CI, and "
                        "demos. Switch to KW_AUTH_MODE=bearer in any "
                        "shared environment."
                    ),
                },
            )
            self._warned = True
        return self._user


__all__ = ["DevModeAuthService", "DEFAULT_DEV_USER_ID", "DEFAULT_DEV_ROLE"]
