"""HTTP coverage for the corpus atlas summary route (#312).

Empty-corpus 200, schema-version stamping, query-param validation, and
the D.5 scope-cache contract on the route layer.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.services.auth import DevModeAuthService


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _swap_user(services, user_id: str) -> None:
    object.__setattr__(services, "auth", DevModeAuthService(user_id=user_id))


def _upload_owned_document(client: TestClient, filename: str = "policy.txt") -> str:
    response = client.post(
        "/documents/upload",
        files={"file": (filename, b"hello world", "text/plain")},
    )
    assert response.status_code == 200, response.text
    return response.json()["document_id"]


# ── 200 on empty corpus ──────────────────────────────────────────────


class TestEmptyCorpus:
    def test_returns_200_with_empty_blocks(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        response = client.get("/knowledge/atlas")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["schema_version"] == "v0.1"
        assert body["top_topics"] == []
        assert body["validation_coverage"]["total_documents"] == 0
        assert body["recent_documents"] == []
        assert body["bridge_documents"] == []
        assert body["outlier_relations"] == []


# ── 422 on invalid query params ──────────────────────────────────────


class TestQueryValidation:
    def test_top_topics_limit_zero_returns_422(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        response = client.get("/knowledge/atlas", params={"top_topics_limit": 0})
        assert response.status_code == 422

    def test_outlier_limit_above_ceiling_returns_422(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        response = client.get("/knowledge/atlas", params={"outlier_relations_limit": 999})
        assert response.status_code == 422

    def test_recent_limit_negative_returns_422(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        response = client.get("/knowledge/atlas", params={"recent_documents_limit": -3})
        assert response.status_code == 422


# ── D.5 hidden-existence on the route ────────────────────────────────


class TestScopeFilterRoute:
    def test_other_user_does_not_see_owners_documents(self, monkeypatch) -> None:
        # Dev uploads a document; switch identity to alice → her atlas
        # response excludes dev's document from every block (validation
        # coverage, recent imports). The closure-cache contract is
        # exercised end-to-end via the route's per-request predicate.
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        _upload_owned_document(client, filename="dev.txt")

        _swap_user(services, "alice")
        response = client.get("/knowledge/atlas")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["validation_coverage"]["total_documents"] == 0
        assert body["recent_documents"] == []

    def test_owner_sees_their_own_documents(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        doc_id = _upload_owned_document(client, filename="dev.txt")

        response = client.get("/knowledge/atlas")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["validation_coverage"]["total_documents"] == 1
        recent_ids = {row["document_id"] for row in body["recent_documents"]}
        assert doc_id in recent_ids
