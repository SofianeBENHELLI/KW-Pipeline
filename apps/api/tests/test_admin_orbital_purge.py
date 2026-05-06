"""HTTP coverage for ``POST /admin/orbital/purge_document`` (#292).

Orbital is the sanctioned hard-delete surface (see issue #292 §5):
combined archive + purge_artifacts + KG cleanup in a single audited
call, gated by a confirmation_filename match. Every other surface
remains flag-only per the deletion-rules feedback in memory.

Pins the route contract:

- 422 when ``?confirm=true`` is missing.
- 404 when the document doesn't exist.
- 422 when the operator-typed ``confirmation_filename`` doesn't match
  the document's ``original_filename`` exactly.
- 200 with archive + purge cascade + ``orbital.document.purge`` audit
  row when the request is well-formed and confirmation matches.
- 403 when the caller is not an admin.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.services.audit_event_store import InMemoryAuditEventStore
from app.services.audit_log_handler import AuditLogHandler
from app.services.auth import encode_hs256

_SECRET = "k" * 32


@pytest.fixture
def bearer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_AUTH_MODE", "bearer")
    monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)


@pytest.fixture
def audit_capture():
    store = InMemoryAuditEventStore()
    handler = AuditLogHandler(store)
    target = logging.getLogger("app.routes")
    previous = target.level
    if target.level == logging.NOTSET:
        target.setLevel(logging.INFO)
    target.addHandler(handler)
    try:
        yield store
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)


def _token(role: str, user_id: str = "tester") -> str:
    return encode_hs256(
        {"sub": user_id, "role": role, "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _seed_active(services, document_id: str, filename: str = "policy.pdf"):
    version = DocumentVersion(
        id=f"{document_id}-v1",
        document_id=document_id,
        version_number=1,
        filename=filename,
        content_type="application/pdf",
        file_size=12,
        sha256=("sha-" + document_id).ljust(64, "0"),
        storage_uri=f"memory://documents/{document_id}-v1/{filename}",
        status=DocumentVersionStatus.VALIDATED,
    )
    services.storage.put(f"documents/{document_id}-v1/{filename}", b"hello world!")
    document = Document.with_first_version(version)
    # Override the auto-derived filename so we can test the
    # confirmation-filename mismatch branch independently.
    document = document.model_copy(update={"original_filename": filename})
    services.documents.catalog.save_document_with_version(document, version)
    return document


class TestOrbitalPurgeDocument:
    def test_missing_confirm_query_returns_422(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_active(services, "doc-1")
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/orbital/purge_document",
            json={"document_id": "doc-1", "confirmation_filename": "policy.pdf"},
            headers=headers,
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "KW_UNPROCESSABLE_ENTITY"

    def test_unknown_document_returns_404(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/orbital/purge_document?confirm=true",
            json={"document_id": "missing", "confirmation_filename": "x.pdf"},
            headers=headers,
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "KW_NOT_FOUND"

    def test_filename_mismatch_returns_422_and_does_not_mutate(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_active(services, "doc-1", filename="policy.pdf")
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/orbital/purge_document?confirm=true",
            json={
                "document_id": "doc-1",
                "confirmation_filename": "wrong.pdf",
            },
            headers=headers,
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "KW_VALIDATION_ERROR"

        # State unchanged.
        version = services.documents.catalog.get_version("doc-1", "doc-1-v1")
        assert version.status is DocumentVersionStatus.VALIDATED
        assert version.storage_uri.startswith("memory://")

    def test_happy_path_cascades_archive_purge_and_audits(
        self, bearer_env: None, audit_capture: InMemoryAuditEventStore
    ) -> None:
        client, services = _client_and_services()
        _seed_active(services, "doc-1", filename="policy.pdf")
        headers = {"Authorization": f"Bearer {_token('admin', 'alice')}"}

        response = client.post(
            "/admin/orbital/purge_document?confirm=true",
            json={
                "document_id": "doc-1",
                "confirmation_filename": "policy.pdf",
            },
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["document_id"] == "doc-1"
        assert body["original_filename"] == "policy.pdf"
        assert body["archived_at"] is not None
        assert len(body["versions_purged"]) == 1
        assert body["versions_purged"][0]["status_before"] == "VALIDATED"

        # Document is archived now (read paths skip it).
        active = services.documents.catalog.get_document("doc-1")
        assert active is None

        # Version is PURGED with a tombstone URI.
        version = services.documents.catalog.get_version("doc-1", "doc-1-v1")
        assert version.status is DocumentVersionStatus.PURGED
        assert version.storage_uri.startswith("tombstone:")

        # Bytes physically removed.
        assert services.storage.objects.get("memory://documents/doc-1-v1/policy.pdf") is None

        # Audit row written for the orbital purge.
        events = audit_capture.query()
        names = [e.event_name for e in events]
        assert "orbital.document.purge" in names
        purge_event = next(e for e in events if e.event_name == "orbital.document.purge")
        assert purge_event.payload["document_id"] == "doc-1"
        assert purge_event.payload["original_filename"] == "policy.pdf"
        assert purge_event.payload["versions_purged"] == 1
        assert purge_event.payload["actor"] == "alice"

    def test_non_admin_returns_403(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_active(services, "doc-1")
        headers = {"Authorization": f"Bearer {_token('contributor')}"}

        response = client.post(
            "/admin/orbital/purge_document?confirm=true",
            json={"document_id": "doc-1", "confirmation_filename": "policy.pdf"},
            headers=headers,
        )

        assert response.status_code == 403


class TestOrbitalPurgeAll:
    """Coverage for ``POST /admin/orbital/purge_all`` (#292 — bulk override)."""

    def test_missing_confirm_query_returns_422(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/orbital/purge_all",
            json={"confirmation_phrase": "PURGE ALL DOCUMENTS"},
            headers=headers,
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "KW_UNPROCESSABLE_ENTITY"

    def test_wrong_phrase_returns_422_and_does_not_mutate(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_active(services, "doc-1")
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/orbital/purge_all?confirm=true",
            json={"confirmation_phrase": "purge all documents"},
            headers=headers,
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "KW_VALIDATION_ERROR"

        # State unchanged.
        version = services.documents.catalog.get_version("doc-1", "doc-1-v1")
        assert version.status is DocumentVersionStatus.VALIDATED

    def test_happy_path_purges_every_active_document(
        self, bearer_env: None, audit_capture: InMemoryAuditEventStore
    ) -> None:
        client, services = _client_and_services()
        _seed_active(services, "doc-1", filename="a.pdf")
        _seed_active(services, "doc-2", filename="b.pdf")
        _seed_active(services, "doc-3", filename="c.pdf")
        headers = {"Authorization": f"Bearer {_token('admin', 'alice')}"}

        response = client.post(
            "/admin/orbital/purge_all?confirm=true",
            json={"confirmation_phrase": "PURGE ALL DOCUMENTS"},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["documents_purged"] == 3
        assert body["failed"] == 0
        assert {r["document_id"] for r in body["results"]} == {
            "doc-1",
            "doc-2",
            "doc-3",
        }

        # Catalog read paths now return zero active documents.
        active = services.documents.catalog.list_documents()
        assert active == []

        # Each version is PURGED with a tombstone URI.
        for doc_id in ("doc-1", "doc-2", "doc-3"):
            version = services.documents.catalog.get_version(doc_id, f"{doc_id}-v1")
            assert version.status is DocumentVersionStatus.PURGED
            assert version.storage_uri.startswith("tombstone:")

        # Audit: one orbital.knowledge_space.purge summary event +
        # one orbital.document.purge per row.
        events = audit_capture.query()
        names = [e.event_name for e in events]
        assert names.count("orbital.document.purge") == 3
        assert names.count("orbital.knowledge_space.purge") == 1
        summary = next(e for e in events if e.event_name == "orbital.knowledge_space.purge")
        assert summary.payload["documents_purged"] == 3
        assert summary.payload["failed"] == 0
        assert summary.payload["actor"] == "alice"

    def test_empty_catalog_is_a_no_op_with_zero_count(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/orbital/purge_all?confirm=true",
            json={"confirmation_phrase": "PURGE ALL DOCUMENTS"},
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "documents_purged": 0,
            "failed": 0,
            "results": [],
            "failures": [],
        }

    def test_non_admin_returns_403(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('contributor')}"}

        response = client.post(
            "/admin/orbital/purge_all?confirm=true",
            json={"confirmation_phrase": "PURGE ALL DOCUMENTS"},
            headers=headers,
        )

        assert response.status_code == 403
