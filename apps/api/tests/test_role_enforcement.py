"""HTTP-level coverage for the slice-2 role-enforcement layer (#83 / ADR-019 §3).

Each role gates a specific class of endpoint:

- ``viewer`` — read-only catalog and knowledge layer.
- ``contributor`` — ingestion writes (upload, extract, semantic, chat).
- ``reviewer`` — review decisions (validate, reject).
- ``admin`` — admin-only endpoints (e.g. ``/admin/config``).

We use ``KW_AUTH_MODE=bearer`` with hand-minted HS256 tokens so each
test exercises one identity at the boundary between roles, then check
that the inheritance rule (admin ⊇ reviewer ⊇ contributor ⊇ viewer)
holds for the positive path.

Existing tests run under ``dev`` / ``disabled`` mode (default
``role="admin"``) and stay green untouched — admin satisfies every
gate.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.services.auth import encode_hs256

# ADR-019 §2: production secret must be ≥ 32 bytes; tests mirror that
# length so we exercise realistic byte-handling on every code path.
_SECRET = "k" * 32


@pytest.fixture
def bearer_env(monkeypatch):
    """Switch the app to bearer mode with a deterministic secret."""
    monkeypatch.setenv("KW_AUTH_MODE", "bearer")
    monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)


def _token(role: str, user_id: str = "tester") -> str:
    """Mint a token with a far-future expiry."""
    return encode_hs256(
        {"sub": user_id, "role": role, "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _client_and_services():
    """Build a fresh services container so audit/scope state is per-test."""
    services = build_services()
    return TestClient(create_app(services=services)), services


def _drive_to_needs_review(client: TestClient, headers: dict) -> dict:
    """Upload + extract + semantic, all as a contributor (the minimum role
    that can land a version in NEEDS_REVIEW), so the next test step can
    exercise ``validate`` / ``reject``."""
    upload = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"text body", "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 200, upload.text
    version = upload.json()
    client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract",
        headers=headers,
    ).raise_for_status()
    client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/semantic",
        headers=headers,
    ).raise_for_status()
    return version


def _assert_forbidden_envelope(response, *, current_role: str, required_role: str) -> None:
    """The 403 envelope must match ``KW_FORBIDDEN`` shape (per ADR-019 §5)."""
    assert response.status_code == 403, response.text
    body = response.json()
    error = body["error"]
    assert error["code"] == "KW_FORBIDDEN"
    assert error["status"] == 403
    assert error["retryable"] is False
    assert current_role in error["message"]
    assert required_role in error["message"]
    assert error["remediation"]


# ─── viewer can read but cannot write ────────────────────────────────


class TestViewerRole:
    def test_viewer_can_list_documents(self, bearer_env):
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('viewer')}"}

        response = client.get("/documents", headers=headers)

        assert response.status_code == 200
        assert "items" in response.json()

    def test_viewer_cannot_upload(self, bearer_env):
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('viewer')}"}

        response = client.post(
            "/documents/upload",
            files={"file": ("p.txt", b"x", "text/plain")},
            headers=headers,
        )

        _assert_forbidden_envelope(response, current_role="viewer", required_role="contributor")

    def test_viewer_cannot_call_chat(self, bearer_env):
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('viewer')}"}

        response = client.post(
            "/knowledge/chat",
            json={"question": "hello", "mode": "rag", "top_k": 5},
            headers=headers,
        )

        _assert_forbidden_envelope(response, current_role="viewer", required_role="contributor")


# ─── contributor inherits viewer + can write but cannot review ───────


class TestContributorRole:
    def test_contributor_can_upload(self, bearer_env):
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('contributor')}"}

        response = client.post(
            "/documents/upload",
            files={"file": ("p.txt", b"hello", "text/plain")},
            headers=headers,
        )

        assert response.status_code == 200, response.text

    def test_contributor_inherits_viewer_can_list(self, bearer_env):
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('contributor')}"}

        response = client.get("/documents", headers=headers)

        assert response.status_code == 200

    def test_contributor_cannot_validate(self, bearer_env):
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('contributor')}"}
        version = _drive_to_needs_review(client, headers=headers)

        response = client.post(
            f"/documents/{version['document_id']}/versions/{version['id']}/validate",
            json={},
            headers=headers,
        )

        _assert_forbidden_envelope(response, current_role="contributor", required_role="reviewer")

    def test_contributor_cannot_reject(self, bearer_env):
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('contributor')}"}
        version = _drive_to_needs_review(client, headers=headers)

        response = client.post(
            f"/documents/{version['document_id']}/versions/{version['id']}/reject",
            json={},
            headers=headers,
        )

        _assert_forbidden_envelope(response, current_role="contributor", required_role="reviewer")


# ─── reviewer inherits contributor but cannot reach admin endpoints ──


class TestReviewerRole:
    def test_reviewer_can_validate(self, bearer_env):
        """Same ``tester`` user uploads (as the only contributor in
        the in-memory store) and then validates — ``reviewer`` rank
        outranks ``contributor`` so the upload is accepted, and the
        per-document scope filter still permits the validate because
        the document was uploaded by the same user."""
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('reviewer')}"}
        version = _drive_to_needs_review(client, headers=headers)

        response = client.post(
            f"/documents/{version['document_id']}/versions/{version['id']}/validate",
            json={"reviewer_note": "ship it"},
            headers=headers,
        )

        assert response.status_code == 200

    def test_reviewer_cannot_read_admin_config(self, bearer_env):
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('reviewer')}"}

        response = client.get("/admin/config", headers=headers)

        _assert_forbidden_envelope(response, current_role="reviewer", required_role="admin")


# ─── admin inherits everything ────────────────────────────────────────


class TestAdminRole:
    def test_admin_can_validate(self, bearer_env):
        """Role-rank inheritance: admin ⊇ reviewer."""
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        version = _drive_to_needs_review(client, headers=headers)

        response = client.post(
            f"/documents/{version['document_id']}/versions/{version['id']}/validate",
            json={"reviewer_note": "admin shipped it"},
            headers=headers,
        )

        assert response.status_code == 200

    def test_admin_can_read_admin_config(self, bearer_env):
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/config", headers=headers)

        assert response.status_code == 200
        assert "upload" in response.json()

    def test_admin_can_upload(self, bearer_env):
        """Role-rank inheritance: admin ⊇ contributor."""
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/documents/upload",
            files={"file": ("p.txt", b"data", "text/plain")},
            headers=headers,
        )

        assert response.status_code == 200

    def test_admin_can_list_documents(self, bearer_env):
        """Role-rank inheritance: admin ⊇ viewer."""
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/documents", headers=headers)

        assert response.status_code == 200


# ─── direct unit tests for the dependency factory ────────────────────


class TestRequireRoleHelper:
    """Direct tests for the :func:`require_role` factory.

    Routes go through FastAPI's dependency injection; this tests the
    role-rank logic at unit level so a regression in the rank table
    is caught even when no integration test happens to exercise the
    relevant boundary.
    """

    def test_admin_satisfies_every_gate(self):
        from app.services.auth import User, require_role

        admin = User(id="a", role="admin")
        for required in ("viewer", "contributor", "reviewer", "admin"):
            dep = require_role(required)  # type: ignore[arg-type]
            assert dep(admin) is admin

    def test_lower_rank_raises_kw_forbidden(self):
        from app.errors import ApiError, ErrorCode
        from app.services.auth import User, require_role

        viewer = User(id="v", role="viewer")
        dep = require_role("contributor")
        with pytest.raises(ApiError) as exc_info:
            dep(viewer)
        assert exc_info.value.status_code == 403
        assert exc_info.value.code == ErrorCode.FORBIDDEN
        assert "viewer" in exc_info.value.message
        assert "contributor" in exc_info.value.message
        assert exc_info.value.retryable is False
        assert exc_info.value.remediation


class TestUnauthenticatedRequest:
    """A bearer-mode call without a token surfaces the 401 envelope from
    :func:`get_current_user` *before* the role gate runs — the role
    layer never sees an unauthenticated principal. Pinning the
    interaction here so the layering stays correct: auth (401) is
    closer to the handler than role (403)."""

    def test_bearer_mode_missing_authorization_header_returns_401(self, bearer_env):
        client, _ = _client_and_services()

        response = client.get("/documents")

        assert response.status_code == 401
        body = response.json()
        assert body["error"]["code"] == "KW_UNAUTHORIZED"
        assert body["error"]["retryable"] is False

    def test_bearer_mode_invalid_token_returns_401_not_403(self, bearer_env):
        client, _ = _client_and_services()
        headers = {"Authorization": "Bearer not-a-real-jwt"}

        response = client.post(
            "/documents/upload",
            files={"file": ("p.txt", b"x", "text/plain")},
            headers=headers,
        )

        # The contributor gate would have produced a 403; auth fails
        # first, so the caller sees the 401 instead.
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "KW_UNAUTHORIZED"
