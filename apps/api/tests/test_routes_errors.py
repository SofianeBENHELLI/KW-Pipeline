"""HTTP error-path coverage for routes that aren't fully exercised by the
happy-path integration tests."""

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
