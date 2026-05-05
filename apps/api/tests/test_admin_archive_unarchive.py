"""HTTP coverage for ``POST /admin/archive/unarchive`` (ADR-027 §1.1, slice 1).

Pins the route contract:

- 404 when the document doesn't exist (active or archived).
- 422 when ``?confirm=true`` is missing for a real mutation.
- 200 with no state change + no audit row for ``?dry_run=true``.
- 200 with state change + an ``admin.document.unarchived`` audit row
  for the real mutation; the document reappears on the standard
  read path.
- Idempotent: unarchiving an already-active document returns 200
  with no extra audit row.
- 403 (KW_FORBIDDEN) when the caller lacks the ``admin`` role.

The audit log is captured via the same ``InMemoryAuditEventStore`` +
``AuditLogHandler`` pattern that ``test_scope_cascade.py`` uses, so
both surfaces (cascade-emitted ``document.archived_orphan`` and
admin-tool-emitted ``admin.document.unarchived``) flow through the
same wiring.
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
    """Switch the app to bearer mode with a deterministic secret.

    ``KW_AUTH_DEV_USER`` is cleared so the dev-mode default (admin)
    doesn't shadow the bearer principal — without that we can't
    exercise the 403 path with a non-admin token.
    """
    monkeypatch.setenv("KW_AUTH_MODE", "bearer")
    monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)


@pytest.fixture
def audit_capture():
    """Attach an in-memory audit store via the structured-logging handler.

    Mirrors how production wiring captures admin events: the route
    emits via ``log.info("admin.document.unarchived", extra={...})``
    and the handler persists the row to the store. We attach to
    ``app.routes`` (the parent of ``app.routes.admin``) so the
    handler picks the record up; remove on teardown to keep the
    global root logger clean.
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
    """Mint a JWT with a far-future expiry."""
    return encode_hs256(
        {"sub": user_id, "role": role, "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _make_version(document_id: str) -> DocumentVersion:
    return DocumentVersion(
        id=f"{document_id}-v1",
        document_id=document_id,
        version_number=1,
        filename="file.txt",
        content_type="text/plain",
        file_size=10,
        sha256=("sha-" + document_id).ljust(64, "0"),
        storage_uri=f"memory://documents/{document_id}-v1/file.txt",
        status=DocumentVersionStatus.STORED,
    )


def _seed_archived(services, document_id: str, *, archived_at: datetime) -> Document:
    """Persist a document and flag-archive it in one shot."""
    version = _make_version(document_id)
    document = Document.with_first_version(version)
    services.documents.catalog.save_document_with_version(document, version)
    services.documents.catalog.flag_document_archived(
        document_id,
        archived_at=archived_at,
        actor="cascade",
    )
    return document


def _seed_active(services, document_id: str) -> Document:
    """Persist a document without archiving it (idempotent unarchive case)."""
    version = _make_version(document_id)
    document = Document.with_first_version(version)
    services.documents.catalog.save_document_with_version(document, version)
    return document


# ─── 404 path ──────────────────────────────────────────────────────────


class TestNotFound:
    def test_returns_404_when_document_missing(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/unarchive?confirm=true",
            json={"document_id": "doc-missing"},
            headers=headers,
        )

        assert response.status_code == 404, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_NOT_FOUND"
        assert "doc-missing" in body["error"]["message"]


# ─── ?confirm=true defence-in-depth ────────────────────────────────────


class TestConfirmRequired:
    def test_missing_confirm_and_dry_run_returns_422(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        archived_at = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        _seed_archived(services, "doc-1", archived_at=archived_at)
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/unarchive",
            json={"document_id": "doc-1"},
            headers=headers,
        )

        assert response.status_code == 422, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_UNPROCESSABLE_ENTITY"

        # The state was NOT mutated — defence-in-depth means a
        # missing-confirm request cannot leak a side effect.
        document = services.documents.catalog._get_document_including_archived("doc-1")  # type: ignore[attr-defined]
        assert document is not None
        assert document.archived_at == archived_at

    def test_dry_run_and_confirm_together_returns_400(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_archived(services, "doc-1", archived_at=datetime.now(UTC))
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/unarchive?confirm=true&dry_run=true",
            json={"document_id": "doc-1"},
            headers=headers,
        )

        assert response.status_code == 400, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_BAD_REQUEST"


# ─── ?dry_run=true ─────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_returns_impact_summary_without_mutation(
        self,
        bearer_env: None,
        audit_capture: InMemoryAuditEventStore,
    ) -> None:
        client, services = _client_and_services()
        archived_at = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        _seed_archived(services, "doc-1", archived_at=archived_at)
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/unarchive?dry_run=true",
            json={"document_id": "doc-1"},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["document_id"] == "doc-1"
        assert body["dry_run"] is True
        assert body["unarchived_at"] is None
        # ``archived_at_before`` matches what's currently on the row.
        assert body["archived_at_before"].startswith("2026-05-04T12:00:00")

        # No state change.
        document = services.documents.catalog._get_document_including_archived("doc-1")  # type: ignore[attr-defined]
        assert document is not None
        assert document.archived_at == archived_at

        # No audit row.
        events = audit_capture.query(event_name="admin.document.unarchived")
        assert events == []


# ─── Real mutation (200 + audit) ──────────────────────────────────────


class TestActualUnarchive:
    def test_clears_archived_at_and_emits_audit_event(
        self,
        bearer_env: None,
        audit_capture: InMemoryAuditEventStore,
    ) -> None:
        client, services = _client_and_services()
        archived_at = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        _seed_archived(services, "doc-1", archived_at=archived_at)
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/unarchive?confirm=true",
            json={"document_id": "doc-1"},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["document_id"] == "doc-1"
        assert body["dry_run"] is False
        assert body["archived_at_before"].startswith("2026-05-04T12:00:00")
        assert body["unarchived_at"] is not None

        # State change: ``archived_at`` cleared, doc visible to standard read path.
        document = services.documents.catalog.get_document("doc-1")
        assert document is not None
        assert document.archived_at is None
        # And the doc reappears in ``list_documents`` (the read path
        # that hides archived rows).
        listed_ids = [d.id for d in services.documents.catalog.list_documents()]
        assert "doc-1" in listed_ids

        # Audit row emitted with the documented payload.
        events = audit_capture.query(event_name="admin.document.unarchived")
        assert len(events) == 1
        event = events[0]
        assert event.document_id == "doc-1"
        assert event.payload["actor"] == "tester"
        assert event.payload["actor_role"] == "admin"
        assert event.payload["archived_at_before"].startswith("2026-05-04T12:00:00")
        assert event.payload["unarchived_at"] is not None


# ─── Idempotent (already-active) ──────────────────────────────────────


class TestIdempotent:
    def test_already_active_doc_returns_200_without_audit_event(
        self,
        bearer_env: None,
        audit_capture: InMemoryAuditEventStore,
    ) -> None:
        client, services = _client_and_services()
        _seed_active(services, "doc-1")
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/unarchive?confirm=true",
            json={"document_id": "doc-1"},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["archived_at_before"] is None
        assert body["unarchived_at"] is None
        assert body["dry_run"] is False

        # No extra audit row — the empty audit log is the idempotency signal.
        events = audit_capture.query(event_name="admin.document.unarchived")
        assert events == []


# ─── 403 on non-admin caller ──────────────────────────────────────────


class TestForbiddenForNonAdmin:
    def test_reviewer_token_is_rejected(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_archived(services, "doc-1", archived_at=datetime.now(UTC))
        headers = {"Authorization": f"Bearer {_token('reviewer')}"}

        response = client.post(
            "/admin/archive/unarchive?confirm=true",
            json={"document_id": "doc-1"},
            headers=headers,
        )

        assert response.status_code == 403, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_FORBIDDEN"
        # The 403 envelope mirrors the require_admin contract from
        # ADR-019 §3 — current vs required role surfaced in the message.
        assert "reviewer" in body["error"]["message"]
        assert "admin" in body["error"]["message"]
