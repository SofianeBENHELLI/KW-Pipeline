"""Issue #120 — regression: install_error_handlers must be wired in create_app.

Until #120 the custom ``ApiError`` envelope in ``app/errors.py`` was dormant
because nothing called ``install_error_handlers(app)``. These tests pin the
public envelope shape so a future refactor can't silently drop it.

The legacy ``detail`` field is preserved alongside the new ``error.code`` /
``error.message`` / ``error.status`` block so any older client that reads
``response.json()["detail"]`` keeps working.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_404_returns_envelope_and_legacy_detail():
    response = _client().get("/documents/missing-id")
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == {
        "code": "KW_NOT_FOUND",
        "message": "Document not found.",
        "status": 404,
        "retryable": False,
        "remediation": None,
    }
    assert body["detail"] == "Document not found."


def test_415_returns_envelope_for_disallowed_content_type(monkeypatch):
    """The 415 path now raises ``ApiError`` with the specific
    ``KW_UPLOAD_UNSUPPORTED_TYPE`` code (issue #97) instead of falling
    through to the status-derived ``KW_UNSUPPORTED_MEDIA_TYPE`` code.
    The remediation hint points operators at the allowlist env var."""
    monkeypatch.delenv("KW_ALLOWED_CONTENT_TYPES", raising=False)
    monkeypatch.delenv("ALLOWED_CONTENT_TYPES", raising=False)
    client = _client()
    response = client.post(
        "/documents/upload",
        files={"file": ("blob.bin", b"hi", "application/octet-stream")},
    )
    assert response.status_code == 415
    body = response.json()
    assert body["error"]["code"] == "KW_UPLOAD_UNSUPPORTED_TYPE"
    assert body["error"]["status"] == 415
    assert body["error"]["retryable"] is False
    assert "KW_ALLOWED_CONTENT_TYPES" in body["error"]["remediation"]
    # Legacy field still carries the human-readable detail.
    assert "not allowed" in body["detail"]


def test_validation_error_uses_validation_error_code():
    """RequestValidationError gets its own 422 ``KW_VALIDATION_ERROR``
    code so clients can distinguish "your payload was malformed" from
    "the server rejected this transition" (also 422 but uses
    ``KW_UNPROCESSABLE_ENTITY`` or ``KW_LIFECYCLE_CONFLICT``)."""
    client = _client()
    # Bad ``limit`` query param → FastAPI/Pydantic raises RequestValidationError.
    response = client.get("/documents?limit=not-a-number")
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "KW_VALIDATION_ERROR"
    assert body["error"]["retryable"] is False
    assert body["error"]["remediation"]
    # Detail keeps the structured Pydantic error list for debugging.
    assert isinstance(body["detail"], list)


def test_api_error_explicit_code_message_and_headers():
    """Direct ``ApiError`` construction round-trips ``code`` / ``message`` /
    ``retryable`` / ``remediation`` / ``headers`` through the envelope.
    We mount a one-off route on a fresh app so the test doesn't depend
    on any production route raising ApiError."""
    from fastapi import FastAPI

    from app.errors import ApiError, install_error_handlers

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/_boom")
    def _boom() -> None:
        raise ApiError(
            status_code=418,
            code="IM_A_TEAPOT",
            message="Out of coffee.",
            retryable=True,
            remediation="Brew a fresh pot and retry.",
            detail={"reason": "drained"},
            headers={"X-Brewing": "off"},
        )

    response = TestClient(app).get("/_boom")
    assert response.status_code == 418
    body = response.json()
    assert body["error"] == {
        "code": "IM_A_TEAPOT",
        "message": "Out of coffee.",
        "status": 418,
        "retryable": True,
        "remediation": "Brew a fresh pot and retry.",
    }
    assert body["detail"] == {"reason": "drained"}
    assert response.headers["X-Brewing"] == "off"


def test_message_from_detail_falls_back_to_http_phrase_for_unmapped_status():
    """When the route raises a bare ``HTTPException(detail=<dict-without-message>)``,
    the envelope's ``message`` falls back to the HTTP phrase rather than
    ``str(detail)`` so end users don't see Python repr in their UI."""
    from fastapi import FastAPI, HTTPException

    from app.errors import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/_pep")
    def _pep() -> None:
        # 451 has no entry in _STATUS_FALLBACK_CODES; the handler should
        # still produce a sane envelope by deriving the code from the
        # HTTP phrase.
        raise HTTPException(status_code=451, detail={"unrelated": "payload"})

    response = TestClient(app).get("/_pep")
    assert response.status_code == 451
    body = response.json()
    # No mapping for 451 → falls back to KW_HTTP_ERROR.
    assert body["error"]["code"] == "KW_HTTP_ERROR"
    # No string under the dict's "message"/"detail"/"error" keys → falls
    # back to the HTTP phrase ("Unavailable For Legal Reasons").
    assert body["error"]["message"]
    # Bare HTTPException is treated as non-retryable with no remediation
    # hint — only ApiError carries those.
    assert body["error"]["retryable"] is False
    assert body["error"]["remediation"] is None
    # Original detail dict is preserved for debugging.
    assert body["detail"] == {"unrelated": "payload"}
