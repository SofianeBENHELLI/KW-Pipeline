"""Tests for :func:`build_auth_service` (ADR-019).

The factory's contract is "look at ``KW_AUTH_MODE``, return the
matching impl, never silently fall back". These tests pin that
contract via :class:`Settings` (so we don't have to monkeypatch the
process env — Pydantic Settings reads from kwargs first).
"""

from __future__ import annotations

import pytest

from app.services.auth import (
    BearerJWTAuthService,
    DevModeAuthService,
    DisabledAuthService,
    build_auth_service,
)
from app.settings import Settings


def test_factory_default_returns_disabled_mode():
    """No env var set → ``disabled`` (the documented MVP default).

    This is the load-bearing assertion for backward compatibility:
    every existing test, demo seed script, and frontend call must
    keep working without setting ``KW_AUTH_MODE``.
    """
    service = build_auth_service(Settings())

    assert isinstance(service, DisabledAuthService)
    assert service.name == "disabled"


def test_factory_disabled_mode_is_explicit():
    service = build_auth_service(Settings(auth_mode="disabled"))

    assert isinstance(service, DisabledAuthService)


def test_factory_disabled_mode_is_case_insensitive():
    service = build_auth_service(Settings(auth_mode="Disabled"))

    assert isinstance(service, DisabledAuthService)


def test_factory_dev_mode_returns_dev_service():
    service = build_auth_service(Settings(auth_mode="dev", auth_dev_user="alice"))

    assert isinstance(service, DevModeAuthService)
    assert service.name == "dev"


def test_factory_bearer_mode_returns_bearer_service_when_secret_set():
    service = build_auth_service(Settings(auth_mode="bearer", auth_secret="x" * 32))

    assert isinstance(service, BearerJWTAuthService)
    assert service.name == "bearer"


def test_factory_bearer_mode_raises_when_secret_missing():
    """ADR-019: bearer mode without a secret is a startup-time failure."""
    with pytest.raises(RuntimeError, match="KW_AUTH_SECRET"):
        build_auth_service(Settings(auth_mode="bearer", auth_secret=""))


def test_factory_unknown_mode_raises():
    """Unknown modes are a misconfiguration; failing fast at startup
    is safer than silently falling back to ``disabled``."""
    with pytest.raises(RuntimeError, match="Unknown KW_AUTH_MODE"):
        build_auth_service(Settings(auth_mode="oauth2"))
