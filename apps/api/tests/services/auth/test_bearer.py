"""Direct unit tests for :class:`BearerJWTAuthService` (ADR-019).

The verifier rejects every credential failure with :class:`AuthError`
— the route layer maps that to HTTP 401. Generic error messages keep
the verifier from leaking which check failed (timing is constant via
``hmac.compare_digest``; messages are constant by hand).
"""

from __future__ import annotations

import pytest

from app.services.auth import AuthError, BearerJWTAuthService, encode_hs256

# A high-entropy fixture secret. The production secret must be ≥ 32
# bytes per ADR-019; this test value is the same length so we exercise
# realistic byte-handling.
_SECRET = "k" * 32
_OTHER_SECRET = "x" * 32


class _FakeRequest:
    """Stand-in for FastAPI's :class:`Request` — only ``headers`` is used."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


def _frozen_clock(now: float) -> object:
    """Tiny clock factory so the expiry path is exercisable without
    ``time.sleep``. Matches the duck-typed ``clock`` parameter on
    :class:`BearerJWTAuthService`."""

    def _now() -> float:
        return now

    return _now


def test_bearer_rejects_construction_without_secret():
    """ADR-019: missing ``KW_AUTH_SECRET`` must fail at construction so
    the operator sees the misconfiguration at app startup, not on the
    first 401."""
    with pytest.raises(RuntimeError, match="KW_AUTH_SECRET"):
        BearerJWTAuthService(secret="")


def test_bearer_rejects_blank_secret():
    """Whitespace-only secrets are equivalent to no secret."""
    with pytest.raises(RuntimeError, match="KW_AUTH_SECRET"):
        BearerJWTAuthService(secret="   ")


def test_bearer_accepts_valid_token():
    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(1_000.0))
    token = encode_hs256(
        {"sub": "alice", "role": "reviewer", "exp": 2_000, "iat": 900},
        secret=_SECRET,
    )
    request = _FakeRequest(headers={"Authorization": f"Bearer {token}"})

    user = service.authenticate(request)

    assert user.id == "alice"
    assert user.role == "reviewer"
    assert user.claims["sub"] == "alice"
    assert user.claims["exp"] == 2_000


def test_bearer_accepts_lowercase_authorization_header():
    """Starlette normalises header keys to lowercase; a real request
    passes the header that way."""
    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(1_000.0))
    token = encode_hs256(
        {"sub": "bob", "role": "viewer", "exp": 2_000, "iat": 900},
        secret=_SECRET,
    )
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})

    user = service.authenticate(request)

    assert user.id == "bob"


def test_bearer_rejects_missing_authorization_header():
    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(1_000.0))

    with pytest.raises(AuthError):
        service.authenticate(_FakeRequest())


def test_bearer_rejects_malformed_authorization_header():
    """Schema other than ``Bearer`` → AuthError."""
    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(1_000.0))
    request = _FakeRequest(headers={"Authorization": "Basic abc.def.ghi"})

    with pytest.raises(AuthError):
        service.authenticate(request)


def test_bearer_rejects_bad_signature():
    """A token signed with a different secret must not validate."""
    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(1_000.0))
    token = encode_hs256(
        {"sub": "eve", "role": "admin", "exp": 2_000, "iat": 900},
        secret=_OTHER_SECRET,
    )
    request = _FakeRequest(headers={"Authorization": f"Bearer {token}"})

    with pytest.raises(AuthError):
        service.authenticate(request)


def test_bearer_rejects_expired_token():
    """``exp`` strictly before the configured clock → AuthError."""
    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(3_000.0))
    token = encode_hs256(
        {"sub": "alice", "role": "viewer", "exp": 2_000, "iat": 900},
        secret=_SECRET,
    )
    request = _FakeRequest(headers={"Authorization": f"Bearer {token}"})

    with pytest.raises(AuthError):
        service.authenticate(request)


def test_bearer_rejects_token_at_exact_expiry():
    """``exp == now`` is treated as expired (closed-interval rejection)."""
    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(2_000.0))
    token = encode_hs256(
        {"sub": "alice", "role": "viewer", "exp": 2_000, "iat": 900},
        secret=_SECRET,
    )
    request = _FakeRequest(headers={"Authorization": f"Bearer {token}"})

    with pytest.raises(AuthError):
        service.authenticate(request)


def test_bearer_rejects_token_missing_sub_claim():
    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(1_000.0))
    token = encode_hs256(
        {"role": "admin", "exp": 2_000, "iat": 900},
        secret=_SECRET,
    )
    request = _FakeRequest(headers={"Authorization": f"Bearer {token}"})

    with pytest.raises(AuthError):
        service.authenticate(request)


def test_bearer_rejects_token_missing_role_claim():
    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(1_000.0))
    token = encode_hs256(
        {"sub": "alice", "exp": 2_000, "iat": 900},
        secret=_SECRET,
    )
    request = _FakeRequest(headers={"Authorization": f"Bearer {token}"})

    with pytest.raises(AuthError):
        service.authenticate(request)


def test_bearer_rejects_token_missing_iat_claim():
    """``iat`` is required so a future replay-protection layer has a
    stable anchor (ADR-019)."""
    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(1_000.0))
    token = encode_hs256(
        {"sub": "alice", "role": "viewer", "exp": 2_000},
        secret=_SECRET,
    )
    request = _FakeRequest(headers={"Authorization": f"Bearer {token}"})

    with pytest.raises(AuthError):
        service.authenticate(request)


def test_bearer_rejects_unknown_role():
    """Roles outside the canonical four are rejected — the verifier
    refuses to mint a :class:`User` whose role would later trip the
    role-gating layer with a confusing error."""
    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(1_000.0))
    token = encode_hs256(
        {"sub": "alice", "role": "superadmin", "exp": 2_000, "iat": 900},
        secret=_SECRET,
    )
    request = _FakeRequest(headers={"Authorization": f"Bearer {token}"})

    with pytest.raises(AuthError):
        service.authenticate(request)


def test_bearer_rejects_wrong_algorithm():
    """ADR-019 pins HS256. A token whose header says ``HS512`` must
    not validate even if its signature happens to land bytewise — we
    refuse on alg before checking the bytes."""
    import base64
    import json

    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(1_000.0))
    header = {"alg": "HS512", "typ": "JWT"}
    payload = {"sub": "alice", "role": "viewer", "exp": 2_000, "iat": 900}
    h = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
    p = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    token = f"{h}.{p}.deadbeef"
    request = _FakeRequest(headers={"Authorization": f"Bearer {token}"})

    with pytest.raises(AuthError):
        service.authenticate(request)


def test_bearer_rejects_garbage_token():
    service = BearerJWTAuthService(secret=_SECRET, clock=_frozen_clock(1_000.0))
    request = _FakeRequest(headers={"Authorization": "Bearer not-a-jwt"})

    with pytest.raises(AuthError):
        service.authenticate(request)
