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


def test_factory_default_returns_dev_mode():
    """No env var set → ``dev`` (the documented MVP default).

    Picking ``dev`` as default keeps every existing test, demo seed
    script, and frontend call working without setting ``KW_AUTH_MODE``,
    AND every review decision lands a recognisable ``actor="dev"`` in
    the audit table — strictly better than the legacy ``anonymous``
    sentinel ``disabled`` mode used to land.
    """
    service = build_auth_service(Settings())

    assert isinstance(service, DevModeAuthService)
    assert service.name == "dev"


def test_factory_empty_mode_falls_back_to_dev():
    """An explicitly-empty ``KW_AUTH_MODE`` (e.g. ``KW_AUTH_MODE=``)
    should land the documented default rather than silently switching
    behaviour."""
    service = build_auth_service(Settings(auth_mode=""))

    assert isinstance(service, DevModeAuthService)


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
