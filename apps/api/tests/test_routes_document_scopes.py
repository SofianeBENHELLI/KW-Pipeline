"""HTTP coverage for ``GET /documents/{id}/scopes`` (#91, ADR-020 §2).

The route returns the active workspace scope links for one document.
It applies the standard slice-3 gate stack:

- ``Depends(require_viewer)`` — anonymous bearer-mode caller → 401.
- ``assert_can_access_document(...)`` — D.5 hidden-existence: a
  caller without scope on the document → 404 (not 403).
- Otherwise → 200 with the list of :class:`Scope` rows the catalog
  persists.

The dedicated route lets clients inspect a document's scope
membership without inferring it from the ``GET /knowledge/catalog``
side-effect or the upload response shape.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.schemas.scope import Scope
from app.services.auth import DevModeAuthService, encode_hs256

_SECRET = "k" * 32


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _swap_user(services, user_id: str) -> None:
    object.__setattr__(services, "auth", DevModeAuthService(user_id=user_id))


def _bearer_token(role: str = "viewer", user_id: str = "viewer-1") -> str:
    return encode_hs256(
        {"sub": user_id, "role": role, "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _upload(client: TestClient, *, body: bytes = b"hello", filename: str = "policy.txt"):
    response = client.post(
        "/documents/upload",
        files={"file": (filename, body, "text/plain")},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ─── Happy path: owner sees their own scope link ──────────────────────


class TestOwnerReadsScopes:
    def test_owner_sees_personal_scope_link(self, monkeypatch) -> None:
        # ``KW_AUTH_MODE`` unset → DevModeAuthService stamps user "dev"
        # by default. The upload route writes a personal:dev scope link
        # automatically.
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        uploaded = _upload(client)

        response = client.get(f"/documents/{uploaded['document_id']}/scopes")

        assert response.status_code == 200, response.text
        body = response.json()
        scopes = body["scopes"]
        assert len(scopes) == 1
        assert scopes[0]["kind"] == "personal"
        assert scopes[0]["ref"] == "dev"
        # ``added_by`` is the actor's user id, ``added_at`` is the
        # upload timestamp — present on every row.
        assert scopes[0]["added_by"] == "dev"
        assert "added_at" in scopes[0]
        assert scopes[0]["removed_at"] is None

    def test_multiple_scope_links_round_trip(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        uploaded = _upload(client)

        # Add a second active scope link directly on the catalog so
        # the response surfaces both. The route never filters by
        # caller scope visibility (the assert_can_access_document gate
        # already cleared the document); it returns every active link.
        services.documents.catalog.add_scope(
            uploaded["document_id"],
            Scope(
                kind="project",
                ref="alpha",
                added_at=datetime(2026, 5, 8, tzinfo=UTC),
                added_by="dev",
            ),
        )

        response = client.get(f"/documents/{uploaded['document_id']}/scopes")
        assert response.status_code == 200, response.text
        scopes = response.json()["scopes"]
        kinds = {(s["kind"], s["ref"]) for s in scopes}
        assert ("personal", "dev") in kinds
        assert ("project", "alpha") in kinds
        assert len(scopes) == 2


# ─── 404 — hidden existence (other user / missing doc) ────────────────


class TestHiddenExistence:
    def test_other_user_sees_404_not_scope_list(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        uploaded = _upload(client)

        # Switch identity to ``alice`` — no scope link to ``dev``'s doc.
        _swap_user(services, "alice")
        response = client.get(f"/documents/{uploaded['document_id']}/scopes")
        assert response.status_code == 404, response.text
        # Same envelope ``GET /documents/{id}`` returns when the row is
        # missing — bytewise indistinguishable from "doesn't exist".
        assert "Document not found" in response.text

    def test_missing_document_returns_404(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        response = client.get("/documents/doc-does-not-exist/scopes")
        assert response.status_code == 404, response.text


# ─── 401 — bearer mode without an Authorization header ───────────────


class TestUnauthenticatedAccess:
    def test_anonymous_caller_in_bearer_mode_is_rejected(self, monkeypatch) -> None:
        monkeypatch.setenv("KW_AUTH_MODE", "bearer")
        monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
        monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)
        client, _ = _client_and_services()
        # Seed a document as a contributor; we won't read it as them —
        # we drop the token on the read.
        contrib_headers = {"Authorization": f"Bearer {_bearer_token('contributor')}"}
        upload = client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"hello", "text/plain")},
            headers=contrib_headers,
        )
        assert upload.status_code == 200, upload.text
        document_id = upload.json()["document_id"]

        # No Authorization header → 401, before any catalog work.
        anon = client.get(f"/documents/{document_id}/scopes")
        assert anon.status_code == 401, anon.text
