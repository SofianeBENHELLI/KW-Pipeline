"""Direct unit tests for :class:`DevModeAuthService` (ADR-019)."""

from __future__ import annotations

from app.services.auth import (
    DEFAULT_DEV_ROLE,
    DEFAULT_DEV_USER_ID,
    DevModeAuthService,
)


class _FakeRequest:
    """Minimal stand-in for FastAPI's :class:`Request`.

    Dev mode ignores the request entirely (it's a fixed identity), so
    the fake doesn't need to expose anything beyond instantiation.
    """


def test_dev_mode_returns_default_user_when_env_unset():
    service = DevModeAuthService()

    user = service.authenticate(_FakeRequest())

    assert user.id == DEFAULT_DEV_USER_ID
    assert user.role == DEFAULT_DEV_ROLE
    # Sanity: claims dict is present (audit handler reads ``actor``,
    # but downstream code can read ``claims`` for richer context once
    # bearer mode lights up).
    assert isinstance(user.claims, dict)


def test_dev_mode_returns_configured_user_id():
    service = DevModeAuthService(user_id="alice")

    user = service.authenticate(_FakeRequest())

    assert user.id == "alice"
    assert user.role == DEFAULT_DEV_ROLE


def test_dev_mode_blank_user_id_falls_back_to_default():
    """An env var set to whitespace shouldn't land an empty actor on
    audit events; the service normalises to the documented default."""
    service = DevModeAuthService(user_id="   ")

    user = service.authenticate(_FakeRequest())

    assert user.id == DEFAULT_DEV_USER_ID


def test_dev_mode_returns_same_user_across_calls():
    """Sanity: stateless after construction. Two requests in a row
    yield the same identity (the pinned audit shape relies on this)."""
    service = DevModeAuthService(user_id="bob")

    first = service.authenticate(_FakeRequest())
    second = service.authenticate(_FakeRequest())

    assert first == second
