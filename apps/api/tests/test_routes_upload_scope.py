"""HTTP-level coverage for upload-time scope persistence (EPIC-D D.1, #218).

The route must:

- default to ``personal:<current_user.id>`` when neither query param is set;
- accept an explicit ``scope_kind`` + ``scope_ref`` pair and persist it;
- reject unknown scope kinds with HTTP 422;
- still record a personal scope in legacy ``KW_AUTH_MODE=disabled`` mode,
  using the ANONYMOUS_USER_ID as the ref.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.services.auth import ANONYMOUS_USER_ID


def _client() -> TestClient:
    return TestClient(create_app())


def _upload(client: TestClient, params: dict | None = None) -> dict:
    response = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"text body", "text/plain")},
        params=params or {},
    )
    return response


class TestUploadScopeDefault:
    def test_no_scope_params_defaults_to_personal_scope_for_dev_user(self, monkeypatch):
        # Default auth mode is dev → user id is "dev".
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)
        client = _client()

        response = _upload(client)

        assert response.status_code == 200
        body = response.json()
        # The response surfaces the scope link the upload was placed in.
        assert "scopes" in body
        kinds = {(s["kind"], s["ref"]) for s in body["scopes"]}
        assert ("personal", "dev") in kinds


class TestUploadScopeExplicit:
    def test_explicit_swym_community_scope_is_recorded(self, monkeypatch):
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        services = build_services()
        client = TestClient(create_app(services=services))

        response = _upload(
            client,
            params={"scope_kind": "swym_community", "scope_ref": "abc-123"},
        )

        assert response.status_code == 200
        body = response.json()
        kinds = {(s["kind"], s["ref"]) for s in body["scopes"]}
        assert ("swym_community", "abc-123") in kinds

        # Catalog observation: the scope row is also persisted.
        scopes = services.documents.catalog.list_scopes_for_document(body["document_id"])
        assert any(s.kind == "swym_community" and s.ref == "abc-123" for s in scopes)

    def test_explicit_project_scope_is_recorded(self, monkeypatch):
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client = _client()

        response = _upload(
            client,
            params={"scope_kind": "project", "scope_ref": "proj-9"},
        )

        assert response.status_code == 200
        body = response.json()
        kinds = {(s["kind"], s["ref"]) for s in body["scopes"]}
        assert ("project", "proj-9") in kinds


class TestUploadScopeValidation:
    def test_invalid_scope_kind_returns_422(self, monkeypatch):
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client = _client()

        response = _upload(
            client,
            params={"scope_kind": "bogus", "scope_ref": "abc"},
        )

        assert response.status_code == 422

    def test_half_scope_pair_returns_422(self, monkeypatch):
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client = _client()

        response = _upload(client, params={"scope_kind": "swym_community"})

        assert response.status_code == 422


class TestUploadScopeDisabledAuthMode:
    def test_disabled_auth_mode_uses_anonymous_user_id_as_personal_ref(self, monkeypatch):
        """Legacy ``KW_AUTH_MODE=disabled`` still creates a personal scope.

        The ref is the ANONYMOUS_USER_ID sentinel so a future
        "find rows that ran without real auth" question is one filter.
        """
        monkeypatch.setenv("KW_AUTH_MODE", "disabled")
        client = _client()

        response = _upload(client)

        assert response.status_code == 200
        body = response.json()
        kinds = {(s["kind"], s["ref"]) for s in body["scopes"]}
        assert ("personal", ANONYMOUS_USER_ID) in kinds
