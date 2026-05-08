"""ADR-006 / #40 PR-3: assert the production default is async.

PR-1 (#309) shipped the worker harness behind ``KW_EXTRACTION_INLINE=true``.
PR-2 (#329) added the dual-shape route plus the
``QUEUED_FOR_EXTRACTION`` FSM state. PR-3 flips the
:attr:`Settings.extraction_inline` default from ``True`` to ``False``
so a freshly-booted ``create_app()`` returns 202 +
``ExtractionJobSnapshot`` from ``POST /documents/.../extract`` without
any operator configuration.

The conftest in ``apps/api/tests/conftest.py`` pins the rest of the
suite back to the legacy inline path via an autouse env override —
this single test opts out by calling ``monkeypatch.delenv`` so it
observes the real production default.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.schemas.extraction import ExtractionJobSnapshot

PLAIN = "text/plain"


def test_default_extract_route_is_async_when_env_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boot a fresh app *without* ``KW_EXTRACTION_INLINE`` in the env.

    The conftest's autouse ``_default_to_inline_extraction`` fixture
    sets ``KW_EXTRACTION_INLINE=true`` for the whole suite. We undo
    that here with ``monkeypatch.delenv`` (later calls in the same
    monkeypatch stack win), so the :class:`Settings` instance built
    inside ``create_app`` falls through to the new PR-3 default of
    ``False``.

    The contract under that default is the PR-2 async shape: 202 with
    an :class:`ExtractionJobSnapshot` body whose ``status`` is
    ``QUEUED_FOR_EXTRACTION``.
    """
    monkeypatch.delenv("KW_EXTRACTION_INLINE", raising=False)

    app = create_app()
    with TestClient(app) as client:
        upload = client.post(
            "/documents/upload",
            files={"file": ("note.txt", b"async-by-default", PLAIN)},
        )
        assert upload.status_code == 200, upload.text
        version = upload.json()

        response = client.post(
            f"/documents/{version['document_id']}/versions/{version['id']}/extract",
        )
        assert response.status_code == 202, response.text
        snapshot = ExtractionJobSnapshot.model_validate(response.json())
        assert snapshot.status == DocumentVersionStatus.QUEUED_FOR_EXTRACTION
        assert snapshot.document_id == version["document_id"]
        assert snapshot.version_id == version["id"]
