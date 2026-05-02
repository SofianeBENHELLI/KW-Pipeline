"""Public error-code contract tests (#97).

Every code in ``app.errors.ErrorCode`` that the route layer raises with
a specific ``ApiError(code=...)`` is pinned here. Adding a new code
should add a regression test below; renaming/removing one is a
breaking change.

Tests cover the (status, code, retryable, remediation) tuple — the
public contract the frontend depends on.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.errors import ErrorCode
from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_upload_empty_emits_kw_upload_empty(monkeypatch):
    monkeypatch.setenv("KW_ALLOWED_CONTENT_TYPES", "text/plain")
    response = _client().post(
        "/documents/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == ErrorCode.UPLOAD_EMPTY
    assert body["error"]["status"] == 400
    assert body["error"]["retryable"] is False
    assert body["error"]["remediation"]
    assert "zero-length" in body["error"]["remediation"]


def test_upload_too_large_emits_kw_upload_too_large(monkeypatch):
    """Set ``MAX_UPLOAD_BYTES`` very low so a tiny payload trips the limit."""
    monkeypatch.setenv("KW_ALLOWED_CONTENT_TYPES", "text/plain")
    monkeypatch.setenv("KW_MAX_UPLOAD_BYTES", "16")
    response = _client().post(
        "/documents/upload",
        files={"file": ("big.txt", b"x" * 64, "text/plain")},
    )
    assert response.status_code == 413
    body = response.json()
    assert body["error"]["code"] == ErrorCode.UPLOAD_TOO_LARGE
    assert body["error"]["retryable"] is False
    assert "MAX_UPLOAD_BYTES" in body["error"]["remediation"]


def test_upload_unsupported_type_emits_kw_upload_unsupported_type(monkeypatch):
    monkeypatch.delenv("KW_ALLOWED_CONTENT_TYPES", raising=False)
    monkeypatch.delenv("ALLOWED_CONTENT_TYPES", raising=False)
    response = _client().post(
        "/documents/upload",
        files={"file": ("x.bin", b"hi", "application/octet-stream")},
    )
    assert response.status_code == 415
    body = response.json()
    assert body["error"]["code"] == ErrorCode.UPLOAD_UNSUPPORTED_TYPE
    assert body["error"]["retryable"] is False
    assert "KW_ALLOWED_CONTENT_TYPES" in body["error"]["remediation"]


def test_lifecycle_conflict_emits_kw_lifecycle_conflict(monkeypatch):
    """Validating a version that's still STORED (not yet NEEDS_REVIEW) is
    a lifecycle conflict — surfaces as 409 with ``KW_LIFECYCLE_CONFLICT``."""
    monkeypatch.setenv("KW_ALLOWED_CONTENT_TYPES", "text/plain")
    client = _client()
    upload = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"hello world", "text/plain")},
    )
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]
    version_id = upload.json()["id"]

    # No extract / generate-semantic — version is still STORED.
    response = client.post(
        f"/documents/{document_id}/versions/{version_id}/validate",
        json={"reviewer_note": None},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == ErrorCode.LIFECYCLE_CONFLICT
    assert body["error"]["retryable"] is False
    assert "lifecycle" in body["error"]["remediation"].lower()


def test_idempotency_replay_emits_kw_idempotency_replay(monkeypatch):
    """Reusing an Idempotency-Key with a different body is a replay
    conflict — surfaces as 422 with ``KW_IDEMPOTENCY_REPLAY``."""
    monkeypatch.setenv("KW_ALLOWED_CONTENT_TYPES", "text/plain")
    client = _client()
    headers = {"Idempotency-Key": "test-key-1"}
    first = client.post(
        "/documents/upload",
        files={"file": ("a.txt", b"first body", "text/plain")},
        headers=headers,
    )
    assert first.status_code == 200

    second = client.post(
        "/documents/upload",
        files={"file": ("a.txt", b"different body", "text/plain")},
        headers=headers,
    )
    assert second.status_code == 422
    body = second.json()
    assert body["error"]["code"] == ErrorCode.IDEMPOTENCY_REPLAY
    assert body["error"]["retryable"] is False
    assert "Idempotency-Key" in body["error"]["remediation"]
