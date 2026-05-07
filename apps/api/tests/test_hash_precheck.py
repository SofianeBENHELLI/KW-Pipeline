"""HTTP coverage for ``GET /documents/by-hash/{sha256}`` (#292).

The route lets the Forge widget detect duplicates *before* streaming
bytes across the wire — see ADR feedback in issue #292 §1. It is
read-only: the catalog never gains a new version because of a
precheck call.
"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def _upload(client: TestClient, content: bytes, filename: str = "policy.txt") -> dict:
    response = client.post(
        "/documents/upload",
        files={"file": (filename, content, "text/plain")},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestHashPrecheck:
    def test_unknown_hash_returns_exists_false(self) -> None:
        client = _client()
        digest = hashlib.sha256(b"nothing in catalog yet").hexdigest()

        response = client.get(f"/documents/by-hash/{digest}")

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "exists": False,
            "sha256": digest,
            "document_id": None,
            "version_id": None,
            "version_number": None,
            "original_filename": None,
        }

    def test_known_hash_returns_existing_version_metadata(self) -> None:
        client = _client()
        body = b"duplicate body bytes"
        upload = _upload(client, body, filename="first.txt")
        digest = hashlib.sha256(body).hexdigest()

        response = client.get(f"/documents/by-hash/{digest}")

        assert response.status_code == 200
        out = response.json()
        assert out["exists"] is True
        assert out["sha256"] == digest
        assert out["document_id"] == upload["document_id"]
        assert out["version_id"] == upload["id"]
        assert out["version_number"] == upload["version_number"]
        assert out["original_filename"] == "first.txt"

    def test_precheck_does_not_mutate_catalog(self) -> None:
        client = _client()
        body = b"some bytes"
        digest = hashlib.sha256(body).hexdigest()

        before = client.get("/documents").json()
        client.get(f"/documents/by-hash/{digest}")
        after = client.get("/documents").json()

        assert before == after

    def test_uppercase_hash_is_normalised(self) -> None:
        client = _client()
        body = b"another body"
        _upload(client, body, filename="x.txt")
        digest_upper = hashlib.sha256(body).hexdigest().upper()

        response = client.get(f"/documents/by-hash/{digest_upper}")

        assert response.status_code == 200
        out = response.json()
        assert out["exists"] is True
        # Server normalises to lowercase before responding.
        assert out["sha256"] == digest_upper.lower()

    def test_invalid_hash_returns_422(self) -> None:
        client = _client()

        response = client.get("/documents/by-hash/not-a-real-hash")

        assert response.status_code == 422
