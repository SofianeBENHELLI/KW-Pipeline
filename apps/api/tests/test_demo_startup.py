"""Demo MVP startup path (issue #130).

Covers three layers of the env-driven demo wiring:

* :class:`app.settings.Settings` exposes ``persistent`` and ``data_dir``
  with the correct defaults so the existing test suite — which never
  sets ``KW_PERSISTENT`` — keeps booting in-memory.
* :func:`app.main._build_app` consults ``KW_PERSISTENT`` and routes
  through :func:`app.dependencies.build_persistent_services` when
  truthy. The default-args path is still the in-memory wiring.
* :func:`app.demo.main` (the ``kw-demo`` console script) sets the demo
  env-var defaults *without* clobbering values an operator already
  exported.
"""

from __future__ import annotations

import importlib

import pytest

from app import main as main_module
from app.settings import Settings


def test_settings_persistent_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare environment must yield the in-memory wiring defaults.

    The whole MVP test suite depends on ``Settings()`` returning
    ``persistent=False`` so ``create_app()`` keeps building the
    in-memory variant. Pin the defaults explicitly.
    """
    monkeypatch.delenv("KW_PERSISTENT", raising=False)
    monkeypatch.delenv("KW_DATA_DIR", raising=False)

    settings = Settings()

    assert settings.persistent is False
    assert settings.data_dir == ".kw-pipeline"


@pytest.mark.parametrize("truthy", ["true", "True", "1"])
def test_settings_persistent_parses_truthy_env(
    monkeypatch: pytest.MonkeyPatch, truthy: str
) -> None:
    """Pydantic Settings should accept the standard truthy spellings."""
    monkeypatch.setenv("KW_PERSISTENT", truthy)

    assert Settings().persistent is True


def test_settings_data_dir_overrides_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``KW_DATA_DIR`` lets a presenter point the demo at a custom root."""
    monkeypatch.setenv("KW_DATA_DIR", "/tmp/kw-demo-state")

    assert Settings().data_dir == "/tmp/kw-demo-state"


def test_build_app_uses_in_memory_services_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``KW_PERSISTENT`` unset, ``_build_app`` must NOT touch the
    persistent builder. Spy on it and assert it stayed quiet — the
    existing test suite would break the moment this regresses.
    """
    monkeypatch.delenv("KW_PERSISTENT", raising=False)
    persistent_calls: list[object] = []

    def _spy(*args: object, **kwargs: object) -> object:
        persistent_calls.append((args, kwargs))
        raise AssertionError("build_persistent_services must not be called by default")

    monkeypatch.setattr(main_module, "build_persistent_services", _spy)

    app = main_module._build_app()

    assert app is not None
    assert persistent_calls == []


def test_build_app_routes_through_persistent_when_kw_persistent_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """``KW_PERSISTENT=true`` must flip ``_build_app`` to the SQLite +
    filesystem services. Use a real ``tmp_path`` data-dir and a spy
    that records the call before delegating to the real builder so the
    resulting app still serves requests.
    """
    real_builder = main_module.build_persistent_services
    captured: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _spy(*args: object, **kwargs: object) -> object:
        captured.append((args, kwargs))
        return real_builder(*args, **kwargs)

    monkeypatch.setenv("KW_PERSISTENT", "true")
    monkeypatch.setenv("KW_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main_module, "build_persistent_services", _spy)

    app = main_module._build_app()

    assert app is not None
    assert len(captured) == 1
    args, _ = captured[0]
    # ``create_app`` calls ``build_persistent_services(data_dir)`` positionally;
    # any other call shape is a regression worth catching.
    assert args == (str(tmp_path),)


def test_module_level_app_was_built_via_helper() -> None:
    """The module-level ``app`` symbol uvicorn imports must come from
    ``_build_app`` so its env-var contract is honoured at import time.
    Reimport ``app.main`` to assert the symbol exists and is a FastAPI.
    """
    reloaded = importlib.reload(main_module)

    assert reloaded.app is not None
    # FastAPI exposes ``router`` and ``openapi_url`` — cheap structural
    # check that avoids importing the FastAPI class just for isinstance.
    assert hasattr(reloaded.app, "router")
    assert hasattr(reloaded.app, "openapi_url")


def test_kw_demo_main_sets_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """``app.demo.main`` should populate the three demo env vars when
    they are unset and then hand off to uvicorn. Patch ``uvicorn.run``
    to a recorder so the test does not actually open a port.

    NOTE: ``demo.main`` mutates ``os.environ`` via ``setdefault``.
    ``monkeypatch.delenv`` only records a teardown-restore if the var
    was set at call time, so we explicitly seed each var to a sentinel
    BEFORE calling ``demo.main`` — that way monkeypatch always has a
    teardown rollback registered and the env stays clean for the next
    test (avoids polluting ``test_routes_errors`` etc.).
    """
    import os

    import uvicorn

    # Seed sentinels so monkeypatch records a rollback for each var.
    monkeypatch.setenv("KW_PERSISTENT", "__sentinel__")
    monkeypatch.setenv("KW_CORS_ALLOWED_ORIGINS", "__sentinel__")
    monkeypatch.setenv("KW_ALLOWED_CONTENT_TYPES", "__sentinel__")
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "__sentinel__")
    # …then drop them so ``setdefault`` actually fires inside ``main``.
    del os.environ["KW_PERSISTENT"]
    del os.environ["KW_CORS_ALLOWED_ORIGINS"]
    del os.environ["KW_ALLOWED_CONTENT_TYPES"]
    del os.environ["KW_KNOWLEDGE_LAYER_ENABLED"]

    runs: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda *a, **kw: runs.append((a, kw)),
    )

    from app import demo

    demo.main()

    assert os.environ["KW_PERSISTENT"] == "true"
    assert os.environ["KW_CORS_ALLOWED_ORIGINS"] == "http://localhost:5173"
    assert "application/pdf" in os.environ["KW_ALLOWED_CONTENT_TYPES"]
    assert "wordprocessingml.document" in os.environ["KW_ALLOWED_CONTENT_TYPES"]
    assert "text/plain" in os.environ["KW_ALLOWED_CONTENT_TYPES"]
    assert os.environ["KW_KNOWLEDGE_LAYER_ENABLED"] == "true"
    assert len(runs) == 1
    args, kwargs = runs[0]
    assert args == ("app.main:app",)
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8000
    assert kwargs["reload"] is True


def test_kw_demo_main_does_not_override_preset_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a presenter already set one of the demo env vars (e.g. a
    different CORS origin for a remote frontend), the console script
    must leave it alone — ``setdefault`` semantics.
    """
    monkeypatch.setenv("KW_PERSISTENT", "false")
    monkeypatch.setenv("KW_CORS_ALLOWED_ORIGINS", "https://demo.example.com")
    monkeypatch.setenv(
        "KW_ALLOWED_CONTENT_TYPES",
        "text/plain",
    )
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "false")

    import os

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

    from app import demo

    demo.main()

    assert os.environ["KW_PERSISTENT"] == "false"
    assert os.environ["KW_CORS_ALLOWED_ORIGINS"] == "https://demo.example.com"
    assert os.environ["KW_ALLOWED_CONTENT_TYPES"] == "text/plain"
    assert os.environ["KW_KNOWLEDGE_LAYER_ENABLED"] == "false"
