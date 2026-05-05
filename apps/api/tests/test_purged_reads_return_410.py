"""HTTP coverage for the ADR-027 §3 / slice 6 410 Gone read responses.

Pins the route contract:

- ``GET /documents/{id}`` where every version is ``PURGED`` → 410
  with the ``KW_PURGED`` envelope. A document where some versions
  are PURGED and others are not still 200s and surfaces the mixed
  statuses (the route layer does not filter purged versions out of
  the family — they remain visible so audit consumers can see them).
- ``GET /documents/{id}/versions/{version_id}/raw`` /
  ``/extraction`` / ``/semantic`` / ``/markdown`` where the
  version's status is ``PURGED`` → 410 with the ``KW_PURGED``
  envelope and the tombstone URI on ``error.detail.tombstone_uri``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.schemas.scope import Scope
from app.services.auth import encode_hs256

_SECRET = "k" * 32


@pytest.fixture
def bearer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_AUTH_MODE", "bearer")
    monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)


def _token(role: str, user_id: str = "tester") -> str:
    return encode_hs256(
        {"sub": user_id, "role": role, "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _make_version(
    document_id: str,
    *,
    version_id: str,
    version_number: int,
    status: DocumentVersionStatus,
) -> DocumentVersion:
    return DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=version_number,
        filename="file.txt",
        content_type="text/plain",
        file_size=10,
        sha256=("sha-" + version_id).ljust(64, "0"),
        storage_uri=f"memory://documents/{version_id}/file.txt",
        status=status,
    )


def _link_personal_scope(services, document_id: str, *, user_id: str = "tester") -> None:
    """Add a ``personal:<user_id>`` scope link so the bearer caller passes
    the D.5 scope filter on read paths."""
    services.documents.catalog.add_scope(
        document_id,
        Scope(
            kind="personal",
            ref=user_id,
            added_at=datetime(2026, 5, 4, tzinfo=UTC),
            added_by=user_id,
        ),
    )


def _purge_via_route(client: TestClient, document_id: str, *, admin_headers: dict) -> None:
    """Call the purge route so the version's status flips to PURGED via the
    real catalog method (mirrors the production transition path)."""
    response = client.post(
        "/admin/archive/purge_artifacts?confirm=true",
        json={"document_id": document_id},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text


# ─── GET /documents/{id} where every version is PURGED → 410 ──────────


class TestGetDocumentAllPurged:
    def test_returns_410_with_purged_envelope(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        admin_headers = {"Authorization": f"Bearer {_token('admin')}"}
        # Seed one version, archive, purge.
        version = _make_version(
            "doc-1",
            version_id="doc-1-v1",
            version_number=1,
            status=DocumentVersionStatus.VALIDATED,
        )
        services.storage.put(f"documents/{version.id}/file.txt", b"hello")
        services.documents.catalog.save_document_with_version(
            Document.with_first_version(version), version
        )
        _link_personal_scope(services, "doc-1")
        services.documents.catalog.flag_document_archived(
            "doc-1", archived_at=datetime(2026, 5, 4, tzinfo=UTC), actor="cascade"
        )
        _purge_via_route(client, "doc-1", admin_headers=admin_headers)

        # The standard read path.
        response = client.get("/documents/doc-1", headers=admin_headers)
        assert response.status_code == 410, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_PURGED"
        assert "doc-1" in body["error"]["message"]


# ─── GET /documents/{id} with mixed statuses → 200 ────────────────────


class TestGetDocumentMixedPurged:
    def test_mixed_purged_and_validated_returns_200_with_full_family(
        self, bearer_env: None
    ) -> None:
        """ADR-027 §3 / slice 6 decision: the route layer does NOT filter
        purged versions out of the family. They remain visible (with
        status=PURGED) so audit consumers see the full lineage. The
        catalog row 410s only when *every* version is PURGED."""
        client, services = _client_and_services()
        admin_headers = {"Authorization": f"Bearer {_token('admin')}"}
        v1 = _make_version(
            "doc-2",
            version_id="doc-2-v1",
            version_number=1,
            status=DocumentVersionStatus.VALIDATED,
        )
        services.storage.put(f"documents/{v1.id}/file.txt", b"v1")
        services.documents.catalog.save_document_with_version(Document.with_first_version(v1), v1)
        _link_personal_scope(services, "doc-2")
        # Hand-craft a v2 in PURGED status by saving + then mutating
        # via the catalog primitive directly (the FSM allows
        # VALIDATED → PURGED on a previously terminal version).
        v2 = _make_version(
            "doc-2",
            version_id="doc-2-v2",
            version_number=2,
            status=DocumentVersionStatus.VALIDATED,
        )
        services.storage.put(f"documents/{v2.id}/file.txt", b"v2")
        services.documents.catalog.append_version_to_document("doc-2", v2)
        # Archive then purge ONLY v2 by faking a single-version purge:
        # archive the doc, drop the v1 from the family in catalog
        # state so purge_one only touches v2 — but we want a real
        # mixed state, so call purge_version_artifacts directly.
        services.documents.catalog.purge_version_artifacts(
            "doc-2",
            "doc-2-v2",
            tombstone_uri="tombstone:purged:doc-2:doc-2-v2:2026-05-05T00:00:00+00:00",
            purged_at=datetime(2026, 5, 5, tzinfo=UTC),
            actor="tester",
        )

        response = client.get("/documents/doc-2", headers=admin_headers)
        assert response.status_code == 200, response.text
        body = response.json()
        statuses = {v["status"] for v in body["versions"]}
        assert statuses == {"VALIDATED", "PURGED"}


# ─── Per-route 410 on PURGED versions ─────────────────────────────────


class TestPurgedVersionRoutes:
    def _seed_purged_doc(self, client: TestClient, services) -> None:
        admin_headers = {"Authorization": f"Bearer {_token('admin')}"}
        version = _make_version(
            "doc-3",
            version_id="doc-3-v1",
            version_number=1,
            status=DocumentVersionStatus.VALIDATED,
        )
        services.storage.put(f"documents/{version.id}/file.txt", b"hello")
        services.documents.catalog.save_document_with_version(
            Document.with_first_version(version), version
        )
        _link_personal_scope(services, "doc-3")
        services.documents.catalog.flag_document_archived(
            "doc-3", archived_at=datetime(2026, 5, 4, tzinfo=UTC), actor="cascade"
        )
        _purge_via_route(client, "doc-3", admin_headers=admin_headers)

    def test_raw_route_returns_410(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        self._seed_purged_doc(client, services)
        admin_headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get(
            "/documents/doc-3/versions/doc-3-v1/raw",
            headers=admin_headers,
        )

        assert response.status_code == 410, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_PURGED"
        # Tombstone URI surfaced on ``detail`` so audit consumers can
        # correlate without joining against the audit log.
        assert body["detail"]["tombstone_uri"].startswith("tombstone:purged:doc-3:doc-3-v1:")

    def test_extraction_route_returns_410(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        self._seed_purged_doc(client, services)
        admin_headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get(
            "/documents/doc-3/versions/doc-3-v1/extraction",
            headers=admin_headers,
        )

        assert response.status_code == 410, response.text
        assert response.json()["error"]["code"] == "KW_PURGED"

    def test_semantic_route_returns_410(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        self._seed_purged_doc(client, services)
        admin_headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get(
            "/documents/doc-3/versions/doc-3-v1/semantic",
            headers=admin_headers,
        )

        assert response.status_code == 410, response.text
        assert response.json()["error"]["code"] == "KW_PURGED"

    def test_markdown_route_returns_410(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        self._seed_purged_doc(client, services)
        admin_headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get(
            "/documents/doc-3/versions/doc-3-v1/markdown",
            headers=admin_headers,
        )

        assert response.status_code == 410, response.text
        assert response.json()["error"]["code"] == "KW_PURGED"
