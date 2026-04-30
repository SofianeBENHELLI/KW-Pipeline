"""HTTP error-path coverage for routes that aren't fully exercised by the
happy-path integration tests."""

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app


def _client():
    return TestClient(create_app())


def test_create_app_accepts_externally_built_services():
    """`create_app(services=...)` must use the provided container instead of
    building a fresh one — required by tests and by future deployment wiring."""
    services = build_services()
    app = create_app(services=services)

    assert app.state.services is services


class TestNotFoundPaths:
    def test_get_unknown_document_returns_404(self):
        response = _client().get("/documents/missing-id")

        assert response.status_code == 404
        assert response.json()["detail"] == "Document not found."

    def test_extract_unknown_document_returns_404(self):
        response = _client().post("/documents/missing-doc/versions/missing-version/extract")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_semantic_unknown_extraction_returns_404(self):
        client = _client()

        upload = client.post(
            "/documents/upload",
            files={"file": ("p.txt", b"some bytes", "text/plain")},
        ).json()

        # No extraction has been triggered yet → /semantic should 404 the raw lookup.
        response = client.post(
            f"/documents/{upload['document_id']}/versions/{upload['id']}/semantic"
        )

        assert response.status_code == 404


class TestEmptyAndDuplicate:
    def test_empty_upload_does_not_create_document(self):
        client = _client()

        client.post(
            "/documents/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )

        assert client.get("/documents").json() == []

    def test_duplicate_extract_returns_conflict(self):
        client = _client()

        original = client.post(
            "/documents/upload",
            files={"file": ("a.txt", b"shared", "text/plain")},
        ).json()
        duplicate = client.post(
            "/documents/upload",
            files={"file": ("b.txt", b"shared", "text/plain")},
        ).json()

        assert duplicate["status"] == "DUPLICATE_DETECTED"
        assert duplicate["duplicate_of_version_id"] == original["id"]

        response = client.post(
            f"/documents/{duplicate['document_id']}/versions/{duplicate['id']}/extract"
        )

        assert response.status_code == 409


class TestCorsMiddleware:
    """Verify the CORS allowlist is read from the env var and that origins
    outside the list are silently denied (no `Access-Control-Allow-Origin`
    header echoed back, which is how Starlette's CORSMiddleware signals
    rejection — it still returns the preflight response, just without the
    permissive headers)."""

    @pytest.fixture
    def configured_client(self, monkeypatch):
        monkeypatch.setenv(
            "CORS_ALLOWED_ORIGINS",
            "http://localhost:5173, https://orbital.example.com",
        )
        return TestClient(create_app())

    def test_preflight_from_allowed_origin_echoes_origin_header(self, configured_client):
        response = configured_client.options(
            "/documents",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert "GET" in response.headers["access-control-allow-methods"]

    def test_preflight_from_unknown_origin_is_not_allowed(self, configured_client):
        response = configured_client.options(
            "/documents",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert "access-control-allow-origin" not in response.headers

    def test_default_app_has_empty_allowlist(self, monkeypatch):
        """With no env var set, no origin should ever be allowed."""
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        client = TestClient(create_app())

        response = client.options(
            "/documents",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert "access-control-allow-origin" not in response.headers
