"""Route-level guardrails for `POST /documents/upload`.

Covers issue #37: oversized uploads return 413, disallowed content types
return 415, env vars are read at request time so each test can configure
them independently.
"""

from fastapi.testclient import TestClient

from app.main import create_app


def _client():
    return TestClient(create_app())


class TestUploadSizeLimit:
    def test_oversized_upload_returns_413(self, monkeypatch):
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "10")
        client = _client()

        # 11 bytes — one over the 10-byte ceiling.
        response = client.post(
            "/documents/upload",
            files={"file": ("big.txt", b"01234567890", "text/plain")},
        )

        assert response.status_code == 413
        assert response.json()["detail"] == "Upload exceeds limit of 10 bytes"

    def test_within_limit_is_accepted(self, monkeypatch):
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "32")
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("ok.txt", b"hello world", "text/plain")},
        )

        assert response.status_code == 200

    def test_default_limit_applies_when_env_unset(self, monkeypatch):
        """Without MAX_UPLOAD_BYTES the default 50 MiB is enforced — a tiny
        upload sails through."""
        monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("small.txt", b"hi", "text/plain")},
        )

        assert response.status_code == 200


class TestContentTypeAllowlist:
    def test_disallowed_content_type_returns_415(self):
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("evil.bin", b"payload", "application/octet-stream")},
        )

        assert response.status_code == 415
        assert response.json()["detail"] == (
            "Content type 'application/octet-stream' is not allowed. Allowed: text/plain"
        )

    def test_allowlist_accepts_parameterised_content_type(self):
        """`text/plain; charset=utf-8` must be accepted when `text/plain` is
        on the allowlist — RFC 7231 lets clients append parameters and we
        gate on the bare media type."""
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("note.txt", b"hello", "text/plain; charset=utf-8")},
        )

        assert response.status_code == 200

    def test_custom_allowlist_respected(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_CONTENT_TYPES", "application/json,text/plain")
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("data.json", b"{}", "application/json")},
        )

        assert response.status_code == 200

    def test_custom_allowlist_lists_sorted_types_in_error(self, monkeypatch):
        """When multiple types are allowed, the 415 detail lists them sorted
        and comma-joined for stable error messages."""
        monkeypatch.setenv("ALLOWED_CONTENT_TYPES", "text/plain,application/json")
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("evil.bin", b"x", "application/octet-stream")},
        )

        assert response.status_code == 415
        assert response.json()["detail"] == (
            "Content type 'application/octet-stream' is not allowed. "
            "Allowed: application/json, text/plain"
        )

    def test_default_allowlist_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("ALLOWED_CONTENT_TYPES", raising=False)
        client = _client()

        # text/plain (default) → accepted.
        ok = client.post(
            "/documents/upload",
            files={"file": ("a.txt", b"bytes", "text/plain")},
        )
        assert ok.status_code == 200

        # text/html → rejected by the default allowlist.
        nope = client.post(
            "/documents/upload",
            files={"file": ("a.html", b"<p/>", "text/html")},
        )
        assert nope.status_code == 415
