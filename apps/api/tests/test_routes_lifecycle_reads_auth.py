"""Auth + scope coverage for the four lifecycle GET-content routes
(#83 slice 3, ADR-019 §3, ADR-020 §2 D.5).

The routes under test are:

- ``GET /documents/{id}/versions/{vid}/extraction``
- ``GET /documents/{id}/versions/{vid}/semantic``
- ``GET /documents/{id}/versions/{vid}/markdown``
- ``GET /documents/{id}/versions/{vid}/raw``

All four returned document content with **no actor check and no scope
filter** before slice 3. They now require ``require_viewer`` (so an
anonymous bearer-mode caller is rejected with 401) and call
``assert_can_access_document`` after the existence check (so a caller
without scope sees a plain 404, indistinguishable from "version
doesn't exist" — D.5 hidden-existence semantics).

The tests exercise both gates per route: 401 in bearer mode without a
token, and 404 in dev mode when the caller's scope set excludes the
target document.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.services.auth import DevModeAuthService, encode_hs256

# Same secret length the existing role-enforcement tests use — ADR-019
# §2 requires production secrets to be ≥ 32 bytes.
_SECRET = "k" * 32


# ─── Setup helpers ────────────────────────────────────────────────────


@pytest.fixture
def bearer_env(monkeypatch):
    """Switch the app to bearer mode with a deterministic secret so
    requests without an Authorization header are rejected at the
    auth boundary (the slice-1 path)."""
    monkeypatch.setenv("KW_AUTH_MODE", "bearer")
    monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)


def _viewer_token(user_id: str = "viewer-1") -> str:
    return encode_hs256(
        {"sub": user_id, "role": "viewer", "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _contributor_token(user_id: str = "contrib-1") -> str:
    return encode_hs256(
        {"sub": user_id, "role": "contributor", "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _build_client():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _swap_user(services, user_id: str) -> None:
    object.__setattr__(services, "auth", DevModeAuthService(user_id=user_id))


def _seed_extracted_version(client: TestClient, headers: dict | None = None) -> dict:
    """Upload + extract + generate semantic so all four GET routes have
    something to return. Driven through the route layer (not direct
    ``services.documents.upload``) so the personal-scope link is created
    the way a real client sees it."""
    upload = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"Hello world\nSecond line\n", "text/plain")},
        headers=headers or {},
    )
    assert upload.status_code == 200, upload.text
    version = upload.json()
    extract = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract",
        headers=headers or {},
    )
    assert extract.status_code == 200, extract.text
    semantic = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/semantic",
        headers=headers or {},
    )
    assert semantic.status_code == 200, semantic.text
    return version


_ROUTES = [
    "extraction",
    "semantic",
    "markdown",
    "raw",
]


# ─── 401 in bearer mode without an Authorization header ───────────────


class TestUnauthenticatedAccess:
    """In bearer mode, every per-version content read needs a token."""

    @pytest.mark.parametrize("route", _ROUTES)
    def test_route_rejects_anonymous_caller(self, bearer_env, route: str) -> None:
        # Seed a version as a contributor so something exists to ask for.
        client, _ = _build_client()
        contrib_headers = {"Authorization": f"Bearer {_contributor_token()}"}
        version = _seed_extracted_version(client, headers=contrib_headers)

        # Now drop the token — the route must reject before hitting the
        # catalog, so the response should be 401, not 404.
        anonymous = client.get(
            f"/documents/{version['document_id']}/versions/{version['id']}/{route}"
        )
        assert anonymous.status_code == 401, anonymous.text


# ─── D.5 hidden-existence: 404 when caller lacks scope ────────────────


class TestScopeHidesContent:
    """A caller who can't see the document gets 404 (not 403, not 200)
    so enumeration probes can't tell hidden from missing."""

    @pytest.mark.parametrize("route", _ROUTES)
    def test_other_user_sees_404_not_content(self, monkeypatch, route: str) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        # Seed as ``dev`` (the default DevModeAuthService user).
        client, services = _build_client()
        version = _seed_extracted_version(client)

        # Switch identity to ``alice``. ``alice`` has no scope link to
        # ``dev``'s document, so the version-content reads must hide it.
        _swap_user(services, "alice")
        response = client.get(
            f"/documents/{version['document_id']}/versions/{version['id']}/{route}"
        )
        assert response.status_code == 404, response.text


# ─── Positive path: original owner still sees their own content ───────


class TestOwnerCanRead:
    """The slice-3 patch must not break the happy path for the
    document's owner — they still get the content with 200."""

    def test_owner_sees_extraction(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _build_client()
        version = _seed_extracted_version(client)
        response = client.get(
            f"/documents/{version['document_id']}/versions/{version['id']}/extraction"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["parser_name"] == "plain_text"

    def test_owner_sees_semantic(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _build_client()
        version = _seed_extracted_version(client)
        response = client.get(
            f"/documents/{version['document_id']}/versions/{version['id']}/semantic"
        )
        assert response.status_code == 200, response.text

    def test_owner_sees_markdown(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _build_client()
        version = _seed_extracted_version(client)
        response = client.get(
            f"/documents/{version['document_id']}/versions/{version['id']}/markdown"
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/markdown")

    def test_owner_sees_raw_bytes(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _build_client()
        version = _seed_extracted_version(client)
        response = client.get(f"/documents/{version['document_id']}/versions/{version['id']}/raw")
        assert response.status_code == 200, response.text
        # The original upload body must round-trip verbatim.
        assert response.content == b"Hello world\nSecond line\n"
