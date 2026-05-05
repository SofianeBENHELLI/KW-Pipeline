"""HTTP coverage for ``POST /admin/archive/purge_batch`` (ADR-027 §4, slice 5).

Pins the route contract:

- 422 (``KW_UNPROCESSABLE_ENTITY``) when the list exceeds 100 ids.
- 200 with mixed success/failure rows when some docs fail (e.g. one
  is not archived, one is missing). The successful rows still
  carry the per-doc ``PurgeArtifactsResponse``; the failed rows
  carry ``error_code`` + ``error_message``.
- Dry-run symmetry: ``?dry_run=true`` returns the impact summary
  for every doc with no state change and no audit rows.
- 403 (``KW_FORBIDDEN``) when the caller lacks the ``admin`` role.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

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


# ─── Fixtures ──────────────────────────────────────────────────────────


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
    previous_level = target.level
    if target.level == logging.NOTSET:
        target.setLevel(logging.INFO)
    target.addHandler(handler)
    try:
        yield store
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)


def _token(role: str, user_id: str = "tester") -> str:
    return encode_hs256(
        {"sub": user_id, "role": role, "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _make_version(document_id: str, status: DocumentVersionStatus) -> DocumentVersion:
    vid = f"{document_id}-v1"
    return DocumentVersion(
        id=vid,
        document_id=document_id,
        version_number=1,
        filename="file.txt",
        content_type="text/plain",
        file_size=42,
        sha256=("sha-" + vid).ljust(64, "0"),
        storage_uri=f"memory://documents/{vid}/file.txt",
        status=status,
    )


def _seed_active(services, document_id: str) -> None:
    version = _make_version(document_id, DocumentVersionStatus.VALIDATED)
    services.storage.put(f"documents/{version.id}/file.txt", b"hello world")
    services.documents.catalog.save_document_with_version(
        Document.with_first_version(version), version
    )


def _seed_archived(services, document_id: str, *, archived_at: datetime) -> None:
    version = _make_version(document_id, DocumentVersionStatus.VALIDATED)
    services.storage.put(f"documents/{version.id}/file.txt", b"hello world")
    services.documents.catalog.save_document_with_version(
        Document.with_first_version(version), version
    )
    services.documents.catalog.flag_document_archived(
        document_id,
        archived_at=archived_at,
        actor="cascade",
    )


# ─── 422 — batch too large ────────────────────────────────────────────


class TestBatchTooLarge:
    def test_returns_422_when_list_exceeds_100(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/purge_batch?confirm=true",
            json={"document_ids": [f"doc-{i}" for i in range(101)]},
            headers=headers,
        )

        assert response.status_code == 422, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_UNPROCESSABLE_ENTITY"
        assert "100" in body["error"]["message"]


# ─── Mixed success/failure ────────────────────────────────────────────


class TestMixedResults:
    def test_partial_failure_does_not_abort_batch(
        self,
        bearer_env: None,
        audit_capture: InMemoryAuditEventStore,
    ) -> None:
        client, services = _client_and_services()
        archived_at = datetime(2026, 5, 4, tzinfo=UTC)
        _seed_archived(services, "doc-ok-1", archived_at=archived_at)
        _seed_archived(services, "doc-ok-2", archived_at=archived_at)
        _seed_active(services, "doc-not-archived")
        # doc-missing intentionally not seeded
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/purge_batch?confirm=true",
            json={
                "document_ids": [
                    "doc-ok-1",
                    "doc-not-archived",
                    "doc-missing",
                    "doc-ok-2",
                ]
            },
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dry_run"] is False
        assert len(body["results"]) == 4

        by_id = {r["document_id"]: r for r in body["results"]}
        assert by_id["doc-ok-1"]["success"] is True
        assert by_id["doc-ok-1"]["purge_response"]["versions_purged"][0]["status_before"] == (
            "VALIDATED"
        )
        assert by_id["doc-ok-2"]["success"] is True

        assert by_id["doc-not-archived"]["success"] is False
        assert by_id["doc-not-archived"]["error_code"] == "KW_CONFLICT"
        assert by_id["doc-not-archived"]["purge_response"] is None

        assert by_id["doc-missing"]["success"] is False
        assert by_id["doc-missing"]["error_code"] == "KW_NOT_FOUND"

        # Two docs were actually purged → two audit rows.
        events = audit_capture.query(event_name="document.artifacts_purged")
        assert len(events) == 2
        purged_ids = sorted(e.document_id for e in events)
        assert purged_ids == ["doc-ok-1", "doc-ok-2"]

        # The surviving "not archived" doc was untouched.
        survivor = services.documents.catalog.get_version("doc-not-archived", "doc-not-archived-v1")
        assert survivor.status is DocumentVersionStatus.VALIDATED


# ─── Dry-run symmetry ─────────────────────────────────────────────────


class TestDryRunSymmetry:
    def test_dry_run_returns_impact_for_all_docs_no_state_change(
        self,
        bearer_env: None,
        audit_capture: InMemoryAuditEventStore,
    ) -> None:
        client, services = _client_and_services()
        archived_at = datetime(2026, 5, 4, tzinfo=UTC)
        _seed_archived(services, "doc-1", archived_at=archived_at)
        _seed_archived(services, "doc-2", archived_at=archived_at)
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/purge_batch?dry_run=true",
            json={"document_ids": ["doc-1", "doc-2"]},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dry_run"] is True
        assert all(r["success"] is True for r in body["results"])
        assert all(r["purge_response"]["dry_run"] is True for r in body["results"])

        # No state change.
        for doc_id in ("doc-1", "doc-2"):
            v = services.documents.catalog.get_version(doc_id, f"{doc_id}-v1")
            assert v.status is DocumentVersionStatus.VALIDATED
            assert v.storage_uri.startswith("memory://")
        # No audit rows.
        events = audit_capture.query(event_name="document.artifacts_purged")
        assert events == []


# ─── 403 on non-admin caller ──────────────────────────────────────────


class TestForbiddenForNonAdmin:
    def test_reviewer_token_is_rejected(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_archived(services, "doc-1", archived_at=datetime.now(UTC))
        headers = {"Authorization": f"Bearer {_token('reviewer')}"}

        response = client.post(
            "/admin/archive/purge_batch?confirm=true",
            json={"document_ids": ["doc-1"]},
            headers=headers,
        )

        assert response.status_code == 403, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_FORBIDDEN"
