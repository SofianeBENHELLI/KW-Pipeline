"""``BearerJWTAuthService`` — HS256 bearer-token MVP.

ADR-019 §2 ``bearer`` mode. Validates the
``Authorization: Bearer <jwt>`` header against the symmetric secret
``KW_AUTH_SECRET`` (HS256). This is the **MVP** scheme — the
production scheme for the embedded 3DEXPERIENCE deployment is the
3DX context handoff, deferred to a follow-up ADR.

Why HS256 and not RS256
-----------------------
HS256 needs one shared secret; RS256 needs a key-management story
(JWKS endpoint, rotation, public-key distribution) we are explicitly
not building in this slice. The shared secret is fine for an internal
service-to-service handshake (Iterop callbacks, scheduled jobs); it
is **not** fine for browser-issued tokens. ADR-019 says so explicitly.

Why a stdlib HS256 implementation instead of PyJWT
--------------------------------------------------
The MVP only needs HS256 + four claims (``sub``, ``role``, ``exp``,
``iat``). The standard library covers everything we need (``hmac``
for the signature, ``json`` for the payload, ``base64`` for the
URL-safe encoding) in ~50 lines. Pulling PyJWT in would add a
runtime dependency we'd then have to deprecate when the production
3DX handoff lands. When that handoff lands and we need RS256 / JWKS,
the proper crypto library (``cryptography`` already pinned via
``voyageai``) joins as a direct dep at the same time.

Claim shape
-----------
``sub``  — user id (string). Stamped on audit events as ``actor``.
``role`` — one of the four canonical role strings (see
            :class:`app.services.auth.protocol.Role`).
``exp``  — expiry (Unix seconds). Required.
``iat``  — issued-at (Unix seconds). Required so a future
            replay-protection layer has a stable anchor.

Any token missing one of these claims, with a bad signature, with an
unknown role, or expired is rejected with a generic
:class:`AuthError` — the route layer maps that to HTTP 401.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, get_args

from .protocol import AuthError, Role, User

log = logging.getLogger(__name__)


# Allowed roles set, derived from the Literal so adding a role updates
# both the type and the runtime guard at once.
_ALLOWED_ROLES: frozenset[str] = frozenset(get_args(Role))

# Required claim names. ``sub`` is the actor; ``role`` drives future
# role-gating; ``exp`` and ``iat`` are the temporal envelope.
_REQUIRED_CLAIMS: frozenset[str] = frozenset({"sub", "role", "exp", "iat"})

# Single supported algorithm. A future PR (3DX handoff) introduces
# RS256 alongside; that PR also turns this into a tuple and routes by
# the JWS header's ``alg``.
_ALG = "HS256"


def _b64url_decode(segment: str) -> bytes:
    """RFC 7515 base64url decode, tolerating missing padding.

    JWTs strip trailing ``=`` padding for compactness; Python's
    ``urlsafe_b64decode`` is strict about it. We pad manually so a
    well-formed token from any compliant signer parses.
    """
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode((segment + padding).encode("ascii"))


def _sign(message: bytes, secret: bytes) -> bytes:
    """HMAC-SHA256 over the JWS signing input."""
    return hmac.new(secret, message, hashlib.sha256).digest()


class BearerJWTAuthService:
    """Validate ``Authorization: Bearer <jwt>`` against a shared HS256 secret.

    Construction requires ``KW_AUTH_SECRET`` (passed in as ``secret``);
    a missing or blank secret raises :class:`RuntimeError` so the
    operator gets a startup-time failure rather than a silent
    auth-bypass.

    The service is stateless after construction — the secret bytes,
    role allowlist, and a clock callable are the only fields. Tests
    inject a deterministic clock so the expiry path is exercisable
    without ``time.sleep``.
    """

    name: str = "bearer"

    def __init__(
        self,
        *,
        secret: str,
        clock: Any = None,
    ) -> None:
        normalised = (secret or "").strip()
        if not normalised:
            # Construction-time failure on purpose: dependencies.py
            # builds this lazily, so a missing secret surfaces at app
            # startup, not at the first 401 in production.
            raise RuntimeError(
                "KW_AUTH_SECRET is required when KW_AUTH_MODE=bearer. "
                "Set the secret to a high-entropy random string (>=32 bytes) "
                "before constructing BearerJWTAuthService."
            )
        self._secret_bytes = normalised.encode("utf-8")
        # ``time.time`` is the production clock; tests pass a callable
        # returning a frozen value to make expiry deterministic. Stored
        # as Any to avoid import gymnastics for ``Callable[[], float]``.
        self._clock = clock or time.time

    def authenticate(self, request: Any) -> User:
        token = self._extract_bearer(request)
        claims = self._decode_and_verify(token)
        return User(
            id=str(claims["sub"]),
            role=claims["role"],  # already validated to be a Role member
            claims=claims,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _extract_bearer(self, request: Any) -> str:
        """Read ``Authorization: Bearer <token>`` from a request-like.

        Tolerates the duck-typed test fakes that expose ``headers`` as
        either a dict-like or an object with ``.get``. The header name
        match is case-insensitive — Starlette's ``Headers`` already
        does that, plain dicts in tests don't, so we normalise here.
        """
        header_value: str | None = None
        headers = getattr(request, "headers", None)
        if headers is not None:
            # Starlette ``Headers`` is dict-like with case-insensitive
            # ``get``. Plain ``dict`` is case-sensitive; iterate to be
            # safe.
            try:
                header_value = headers.get("authorization") or headers.get("Authorization")
            except AttributeError:
                for key, value in dict(headers).items():
                    if key.lower() == "authorization":
                        header_value = value
                        break
        if not header_value:
            raise AuthError("missing or invalid token")

        parts = header_value.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise AuthError("missing or invalid token")
        return parts[1].strip()

    def _decode_and_verify(self, token: str) -> dict[str, Any]:
        """Parse, verify, and return the claim dict.

        Raises :class:`AuthError` for every credential failure: bad
        encoding, wrong algorithm, signature mismatch, missing claims,
        unknown role, expired token.
        """
        segments = token.split(".")
        if len(segments) != 3:
            raise AuthError("missing or invalid token")
        header_b64, payload_b64, signature_b64 = segments

        try:
            header = json.loads(_b64url_decode(header_b64))
            payload = json.loads(_b64url_decode(payload_b64))
            signature = _b64url_decode(signature_b64)
        except (ValueError, json.JSONDecodeError) as exc:
            raise AuthError("missing or invalid token") from exc

        if not isinstance(header, dict) or header.get("alg") != _ALG:
            raise AuthError("missing or invalid token")
        if not isinstance(payload, dict):
            raise AuthError("missing or invalid token")

        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected = _sign(signing_input, self._secret_bytes)
        # ``compare_digest`` is constant-time: prevents the trivial
        # timing oracle where the signer can probe valid prefixes.
        if not hmac.compare_digest(expected, signature):
            raise AuthError("missing or invalid token")

        missing = _REQUIRED_CLAIMS - payload.keys()
        if missing:
            raise AuthError("missing or invalid token")

        role = payload["role"]
        if not isinstance(role, str) or role not in _ALLOWED_ROLES:
            raise AuthError("missing or invalid token")

        exp = payload["exp"]
        if not isinstance(exp, int | float):
            raise AuthError("missing or invalid token")
        # ``>=`` not ``>`` so an exp exactly equal to ``now`` is treated
        # as expired. This avoids a one-second window where a token
        # whose expiry just landed still authenticates.
        if float(exp) <= float(self._clock()):
            raise AuthError("missing or invalid token")

        iat = payload["iat"]
        if not isinstance(iat, int | float):
            raise AuthError("missing or invalid token")

        return payload


def encode_hs256(payload: dict[str, Any], *, secret: str) -> str:
    """Test helper: produce a valid HS256 token with ``payload`` claims.

    Lives next to the verifier so tests don't import a third-party
    JWT library just to construct fixtures. **Not** part of the
    production auth path — the API is a verifier only; tokens are
    minted upstream (Iterop, the future 3DX handoff).
    """
    secret_bytes = secret.encode("utf-8")
    header = {"alg": _ALG, "typ": "JWT"}
    header_b64 = (
        base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    payload_b64 = (
        base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature_b64 = (
        base64.urlsafe_b64encode(_sign(signing_input, secret_bytes)).rstrip(b"=").decode("ascii")
    )
    return f"{header_b64}.{payload_b64}.{signature_b64}"


__all__ = ["BearerJWTAuthService", "encode_hs256"]
