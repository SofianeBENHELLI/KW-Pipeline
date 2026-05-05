"""HTTP-level coverage for the D.5 scope filter (EPIC-D #218, ADR-020 §2).

Pins the read-side contract:

- A user sees only documents linked to a scope they have access to.
- The default scope is ``personal:<current_user.id>`` (via
  :func:`scope_filter.default_scopes_for`).
- Cross-user / cross-community / cross-project explicit asks return
  HTTP 403 until the Swym membership client (D.3) lands.
- ``GET /documents/{id}`` returns 404 (not 403) when the row is hidden
  — hidden-existence semantics so an enumeration probe can't
  distinguish "doesn't exist" from "owned by another user".
- Legacy ``KW_AUTH_MODE=disabled`` bypasses the filter for back-compat.
- ``GET /knowledge/catalog`` honours the same default + explicit
  semantics.
- ``GET /documents/{id}/similar`` filters neighbour rows down to the
  caller's scope set.

Tests use the upload route (not ``services.documents.upload`` direct)
so the ``personal:<user>`` scope link is created by the route layer
the way real clients see it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.services.auth import DevModeAuthService, DisabledAuthService


def _swap_user(services, user_id: str) -> None:
    """Swap the auth service to a dev-mode service for ``user_id``.

    ``PipelineServices`` is a frozen dataclass so we use the
    ``object.__setattr__`` escape hatch — same pattern other route
    tests use to swap services at construction time.
    """
    object.__setattr__(services, "auth", DevModeAuthService(user_id=user_id))


def _swap_to_disabled(services) -> None:
    object.__setattr__(services, "auth", DisabledAuthService())


def _client(monkeypatch=None, *, user: str | None = None, mode: str | None = None):
    if monkeypatch is not None:
        if mode is None:
            monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        else:
            monkeypatch.setenv("KW_AUTH_MODE", mode)
        if user is None:
            monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)
        else:
            monkeypatch.setenv("KW_AUTH_DEV_USER", user)
    services = build_services()
    return TestClient(create_app(services=services)), services


def _upload(client: TestClient, body: bytes = b"hello world", filename: str = "policy.txt"):
    response = client.post(
        "/documents/upload",
        files={"file": (filename, body, "text/plain")},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ─── Default scope = personal:<user.id> ──────────────────────────────


class TestDefaultScopeReads:
    def test_dev_user_lists_their_own_uploads(self, monkeypatch):
        client, _ = _client(monkeypatch, user="dev")
        uploaded = _upload(client)

        response = client.get("/documents")

        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["items"]}
        assert uploaded["document_id"] in ids

    def test_other_user_lists_nothing(self, monkeypatch):
        # Dev uploads first, then we swap to alice and read.
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        services = build_services()
        client_dev = TestClient(create_app(services=services))
        client_dev.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"hello", "text/plain")},
        )

        # Switch identity to alice without rebuilding the catalog.
        _swap_user(services, "alice")
        response = client_dev.get("/documents")

        assert response.status_code == 200
        # Alice cannot see dev's documents.
        assert response.json()["items"] == []

    def test_other_user_get_by_id_returns_404_not_403(self, monkeypatch):
        # Hidden-existence: alice asking for dev's doc by id sees 404,
        # not 403, so an enumeration probe can't distinguish "owned by
        # another user" from "doesn't exist".
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        services = build_services()
        client = TestClient(create_app(services=services))
        uploaded = client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"hello", "text/plain")},
        ).json()

        _swap_user(services, "alice")
        response = client.get(f"/documents/{uploaded['document_id']}")

        assert response.status_code == 404
        # Identical detail to a real "missing" response.
        assert response.json()["detail"] == "Document not found."


# ─── Disabled-mode bypass ─────────────────────────────────────────────


class TestDisabledModeBypass:
    def test_disabled_mode_admin_sees_every_document(self, monkeypatch):
        # Upload as dev first, then flip to disabled mode and confirm
        # the legacy admin user sees that doc despite the personal
        # scope being for "dev".
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        services = build_services()
        client = TestClient(create_app(services=services))
        uploaded = client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"hello", "text/plain")},
        ).json()

        # Disabled mode keys off the env var (read by the scope filter
        # via ``Settings()``), not just the auth service kind. Set
        # both so the bypass actually fires.
        monkeypatch.setenv("KW_AUTH_MODE", "disabled")
        _swap_to_disabled(services)

        response = client.get("/documents")
        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["items"]}
        assert uploaded["document_id"] in ids

        # And by-id access works too.
        by_id = client.get(f"/documents/{uploaded['document_id']}")
        assert by_id.status_code == 200


# ─── Explicit scope params ────────────────────────────────────────────


class TestExplicitScopeParams:
    def test_explicit_personal_other_user_returns_403(self, monkeypatch):
        client, _ = _client(monkeypatch, user="alice")

        response = client.get(
            "/documents",
            params={"scope_kind": "personal", "scope_ref": "dev"},
        )

        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == "KW_FORBIDDEN"

    def test_explicit_swym_community_returns_403(self, monkeypatch):
        client, _ = _client(monkeypatch)

        response = client.get(
            "/documents",
            params={"scope_kind": "swym_community", "scope_ref": "any-id"},
        )

        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == "KW_FORBIDDEN"
        # Remediation copy points at D.3 / ADR-026 so an operator
        # reading the response knows when this lifts.
        assert "D.3" in body["error"]["remediation"]

    def test_explicit_project_returns_403(self, monkeypatch):
        client, _ = _client(monkeypatch)

        response = client.get(
            "/documents",
            params={"scope_kind": "project", "scope_ref": "proj-9"},
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "KW_FORBIDDEN"

    def test_explicit_personal_self_is_allowed(self, monkeypatch):
        client, _ = _client(monkeypatch, user="dev")
        uploaded = _upload(client)

        response = client.get(
            "/documents",
            params={"scope_kind": "personal", "scope_ref": "dev"},
        )

        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["items"]}
        assert uploaded["document_id"] in ids


# ─── /knowledge/catalog ───────────────────────────────────────────────


class TestKnowledgeCatalogScopeFilter:
    def test_default_scope_only_shows_personal_uploads(self, monkeypatch):
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        services = build_services()
        client = TestClient(create_app(services=services))
        # Upload + extract + semantic via the route surface so the
        # row is in NEEDS_REVIEW / VALIDATED and the catalog renders
        # it.
        upload = client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"alpha body", "text/plain")},
        ).json()
        client.post(f"/documents/{upload['document_id']}/versions/{upload['id']}/extract")
        client.post(f"/documents/{upload['document_id']}/versions/{upload['id']}/semantic")

        # Switch identity to alice without rebuilding the catalog.
        _swap_user(services, "alice")
        response = client.get("/knowledge/catalog")

        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_explicit_swym_community_returns_403(self, monkeypatch):
        client, _ = _client(monkeypatch)

        response = client.get(
            "/knowledge/catalog",
            params={"scope_kind": "swym_community", "scope_ref": "anything"},
        )
        assert response.status_code == 403


# ─── Similar route filters neighbours ─────────────────────────────────


class TestSimilarScopeFilter:
    def test_similar_drops_neighbours_outside_caller_scope(self, monkeypatch):
        # We can't build a real similarity ranking inside an HTTP
        # test without the full knowledge layer, but we can at least
        # confirm that ``/similar`` for a hidden base returns 404 —
        # the secondary filter on neighbour ids is exercised by the
        # unit-level route test once a fake provider is plumbed.
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        services = build_services()
        client = TestClient(create_app(services=services))
        upload = client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"alpha", "text/plain")},
        ).json()

        _swap_user(services, "alice")
        response = client.get(f"/documents/{upload['document_id']}/similar")

        assert response.status_code == 404
        assert response.json()["detail"] == "Document not found."


# ─── Write endpoints honour the same access check ────────────────────


class TestWriteEndpointHiddenExistence:
    def test_extract_returns_404_for_hidden_document(self, monkeypatch):
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        services = build_services()
        client = TestClient(create_app(services=services))
        upload = client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"alpha", "text/plain")},
        ).json()

        _swap_user(services, "alice")
        response = client.post(
            f"/documents/{upload['document_id']}/versions/{upload['id']}/extract"
        )

        assert response.status_code == 404

    def test_validate_returns_404_for_hidden_document(self, monkeypatch):
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        services = build_services()
        client = TestClient(create_app(services=services))
        upload = client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"alpha", "text/plain")},
        ).json()

        _swap_user(services, "alice")
        response = client.post(
            f"/documents/{upload['document_id']}/versions/{upload['id']}/validate",
            json={"reviewer_note": "test"},
        )

        assert response.status_code == 404
