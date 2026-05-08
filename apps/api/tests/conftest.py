"""Shared pytest fixtures for the API test suite.

PR-3 of ADR-006 / issue #40 flipped the
:attr:`Settings.extraction_inline` default from ``True`` to ``False``,
so a freshly-booted ``create_app()`` now returns 202 +
``ExtractionJobSnapshot`` from ``POST /documents/.../extract``. The
test suite, however, predates ADR-006: ~50 modules assert the
synchronous 200 + ``RawExtraction`` shape directly, and the demo
smoke runner does the same.

Rather than rewrite every assertion, this conftest pins the
**ambient** test default back to ``KW_EXTRACTION_INLINE=true`` (the
pre-ADR-006 behaviour). The autouse fixture is intentionally
conservative:

* It uses :meth:`pytest.MonkeyPatch.setenv`, so the override is
  cleaned up at the end of every test — no leak between modules.
* It checks ``os.environ`` first and **does not overwrite an already-set
  value**. Tests that explicitly opt into async mode call
  ``monkeypatch.setenv("KW_EXTRACTION_INLINE", "false")`` (or
  ``monkeypatch.delenv``) inside their own setup, and that override
  wins because (a) the env var is already populated by the time this
  fixture runs in the inner test scope or (b) the test calls
  ``monkeypatch.setenv`` after this fixture has applied its default,
  and the later call replaces the earlier one in the same monkeypatch
  stack.
* The PR-2 async tests in
  ``apps/api/tests/test_extraction_routes_async.py`` mutate the
  ``Settings`` object directly via ``object.__setattr__`` rather than
  via the env, so they are unaffected by this fixture either way.

Tests that need to observe the new async-by-default behaviour (e.g.
the PR-3 default-flag test) opt out by calling
``monkeypatch.delenv("KW_EXTRACTION_INLINE", raising=False)`` at the
top of the test body.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _default_to_inline_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default the test suite to inline extraction (pre-ADR-006 behaviour).

    Existing tests assume ``POST /documents/.../extract`` returns the
    ``RawExtraction`` body synchronously with HTTP 200. The PR-3 flag
    flip makes that the legacy path, so tests that don't explicitly
    opt into async mode default-on this fixture to keep contract
    stability for the assertions that pre-date PR-2.

    Tests that need the new async-by-default behaviour either:

    * call ``monkeypatch.delenv("KW_EXTRACTION_INLINE", raising=False)``
      to inherit the production default, or
    * call ``monkeypatch.setenv("KW_EXTRACTION_INLINE", "false")``
      explicitly.
    """
    if "KW_EXTRACTION_INLINE" not in os.environ:
        monkeypatch.setenv("KW_EXTRACTION_INLINE", "true")
