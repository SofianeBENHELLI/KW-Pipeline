"""HTTP coverage for ``POST /admin/archive/purge_artifacts`` (ADR-027 §1.3, slice 4).

Pins the route contract:

- 404 when the document does not exist (active or archived).
- 409 (``KW_CONFLICT``) when the document exists but is not archived
  — the §1.3 archive-then-purge precondition.
- 422 when ``?confirm=true`` is missing for a real mutation.
- 200 with no state change + no storage delete + no audit row for
  ``?dry_run=true``.
- 200 with state change + audit row for the real mutation: status
  flips to ``PURGED``, ``storage_uri`` becomes the tombstone URI,
  the underlying storage backend reports the object missing, and
  the audit row carries the documented payload.
- Idempotent: re-purging an already-PURGED version returns 200 with
  the existing tombstone URI and no extra audit row.
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

# ADR-019 §2: the production secret must be ≥ 32 bytes; tests mirror
# that length so we exercise realistic byte handling on every code
# path.
_SECRET = "k" * 32


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def bearer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_AUTH_MODE", "bearer")
    monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)


@pytest.fixture
def audit_capture():
    """Attach an in-memory audit store via the structured-logging handler.

    Captures both ``document.artifacts_purged`` (from the route layer)
    and ``admin.document.unarchived`` (in case a sibling test mixes
    actions). Mirrors the wiring in ``test_admin_archive_unarchive``.
    """
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


def _make_version(
    document_id: str,
    *,
    version_id: str | None = None,
    status: DocumentVersionStatus = DocumentVersionStatus.VALIDATED,
) -> DocumentVersion:
    vid = version_id or f"{document_id}-v1"
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


def _seed_active(services, document_id: str) -> Document:
    version = _make_version(document_id)
    services.storage.put(f"documents/{version.id}/file.txt", b"hello world")
    document = Document.with_first_version(version)
    services.documents.catalog.save_document_with_version(document, version)
    return document


def _seed_archived(
    services,
    document_id: str,
    *,
    archived_at: datetime,
    status: DocumentVersionStatus = DocumentVersionStatus.VALIDATED,
) -> Document:
    """Seed a doc, store its bytes, then flag-archive it."""
    version = _make_version(document_id, status=status)
    services.storage.put(f"documents/{version.id}/file.txt", b"hello world")
    document = Document.with_first_version(version)
    services.documents.catalog.save_document_with_version(document, version)
    services.documents.catalog.flag_document_archived(
        document_id,
        archived_at=archived_at,
        actor="cascade",
    )
    return document


# ─── 404 path ──────────────────────────────────────────────────────────


class TestNotFound:
    def test_returns_404_when_document_missing(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/purge_artifacts?confirm=true",
            json={"document_id": "doc-missing"},
            headers=headers,
        )

        assert response.status_code == 404, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_NOT_FOUND"


# ─── 409 — archive precondition ───────────────────────────────────────


class TestArchivePrecondition:
    def test_returns_409_when_document_not_archived(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_active(services, "doc-1")
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/purge_artifacts?confirm=true",
            json={"document_id": "doc-1"},
            headers=headers,
        )

        assert response.status_code == 409, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_CONFLICT"
        assert "not archived" in body["error"]["message"].lower()

        # State unchanged: status stays VALIDATED, bytes still on disk.
        version = services.documents.catalog.get_version("doc-1", "doc-1-v1")
        assert version.status is DocumentVersionStatus.VALIDATED
        assert version.storage_uri.startswith("memory://")
        assert services.storage.objects.get(version.storage_uri) == b"hello world"


# ─── ?confirm=true defence-in-depth ────────────────────────────────────


class TestConfirmRequired:
    def test_missing_confirm_returns_422(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_archived(services, "doc-1", archived_at=datetime(2026, 5, 4, tzinfo=UTC))
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/purge_artifacts",
            json={"document_id": "doc-1"},
            headers=headers,
        )

        assert response.status_code == 422, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_UNPROCESSABLE_ENTITY"

        # State NOT mutated.
        version = services.documents.catalog.get_version("doc-1", "doc-1-v1")
        assert version.status is DocumentVersionStatus.VALIDATED


# ─── ?dry_run=true ─────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_returns_impact_summary_without_mutation(
        self,
        bearer_env: None,
        audit_capture: InMemoryAuditEventStore,
    ) -> None:
        client, services = _client_and_services()
        _seed_archived(services, "doc-1", archived_at=datetime(2026, 5, 4, tzinfo=UTC))
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/purge_artifacts?dry_run=true",
            json={"document_id": "doc-1"},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["document_id"] == "doc-1"
        assert body["dry_run"] is True
        assert len(body["versions_purged"]) == 1
        row = body["versions_purged"][0]
        assert row["version_id"] == "doc-1-v1"
        assert row["status_before"] == "VALIDATED"
        assert row["storage_uri_before"].startswith("memory://")
        assert row["tombstone_uri"].startswith("tombstone:purged:doc-1:doc-1-v1:")
        assert row["purged_at"] is None
        assert row["bytes_estimate"] == 42

        # No state change.
        version = services.documents.catalog.get_version("doc-1", "doc-1-v1")
        assert version.status is DocumentVersionStatus.VALIDATED
        assert version.storage_uri.startswith("memory://")
        # Bytes still on disk.
        assert services.storage.objects.get(version.storage_uri) == b"hello world"
        # No audit row.
        events = audit_capture.query(event_name="document.artifacts_purged")
        assert events == []


# ─── Real mutation (200 + audit + storage delete) ─────────────────────


class TestActualPurge:
    def test_purges_artifacts_and_emits_audit_event(
        self,
        bearer_env: None,
        audit_capture: InMemoryAuditEventStore,
    ) -> None:
        client, services = _client_and_services()
        _seed_archived(services, "doc-1", archived_at=datetime(2026, 5, 4, tzinfo=UTC))
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        original_uri = services.documents.catalog.get_version("doc-1", "doc-1-v1").storage_uri

        response = client.post(
            "/admin/archive/purge_artifacts?confirm=true",
            json={"document_id": "doc-1"},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dry_run"] is False
        assert len(body["versions_purged"]) == 1
        row = body["versions_purged"][0]
        assert row["status_before"] == "VALIDATED"
        assert row["storage_uri_before"] == original_uri
        assert row["tombstone_uri"].startswith("tombstone:purged:doc-1:doc-1-v1:")
        assert row["purged_at"] is not None

        # Catalog flipped: status PURGED, storage_uri = tombstone.
        version = services.documents.catalog.get_version("doc-1", "doc-1-v1")
        assert version.status is DocumentVersionStatus.PURGED
        assert version.storage_uri.startswith("tombstone:purged:")

        # Storage backend reports the object as missing.
        assert original_uri not in services.storage.objects

        # Audit row emitted with the documented payload.
        events = audit_capture.query(event_name="document.artifacts_purged")
        assert len(events) == 1
        event = events[0]
        assert event.document_id == "doc-1"
        assert event.payload["actor"] == "tester"
        assert event.payload["actor_role"] == "admin"
        assert event.payload["storage_uri_before"] == original_uri
        assert event.payload["tombstone_uri"].startswith("tombstone:purged:")
        assert event.payload["dry_run"] is False


# ─── Idempotent (already-PURGED) ──────────────────────────────────────


class TestIdempotent:
    def test_already_purged_version_returns_200_without_extra_audit_event(
        self,
        bearer_env: None,
        audit_capture: InMemoryAuditEventStore,
    ) -> None:
        client, services = _client_and_services()
        _seed_archived(services, "doc-1", archived_at=datetime(2026, 5, 4, tzinfo=UTC))
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        # First purge — real mutation, one audit row.
        first = client.post(
            "/admin/archive/purge_artifacts?confirm=true",
            json={"document_id": "doc-1"},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        first_tombstone = first.json()["versions_purged"][0]["tombstone_uri"]

        # Second purge — idempotent: same tombstone URI, no second
        # audit row.
        second = client.post(
            "/admin/archive/purge_artifacts?confirm=true",
            json={"document_id": "doc-1"},
            headers=headers,
        )
        assert second.status_code == 200, second.text
        body = second.json()
        row = body["versions_purged"][0]
        # status_before is now PURGED; the existing tombstone is echoed.
        assert row["status_before"] == "PURGED"
        assert row["tombstone_uri"] == first_tombstone
        assert row["purged_at"] is None  # no fresh purge timestamp

        events = audit_capture.query(event_name="document.artifacts_purged")
        assert len(events) == 1  # only the first call emitted


# ─── 403 on non-admin caller ──────────────────────────────────────────


class TestForbiddenForNonAdmin:
    def test_reviewer_token_is_rejected(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_archived(services, "doc-1", archived_at=datetime(2026, 5, 4, tzinfo=UTC))
        headers = {"Authorization": f"Bearer {_token('reviewer')}"}

        response = client.post(
            "/admin/archive/purge_artifacts?confirm=true",
            json={"document_id": "doc-1"},
            headers=headers,
        )

        assert response.status_code == 403, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_FORBIDDEN"
