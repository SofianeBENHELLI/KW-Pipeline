"""HTTP coverage for ``POST /admin/archive/relink_scope`` (ADR-027 §1.2, slice 2).

Pins the route contract:

- 404 when no row exists for the ``(document_id, kind, ref)`` triple
  (active or soft-removed).
- 422 when ``?confirm=true`` is missing for a real mutation.
- 200 with no state change for ``?dry_run=true``.
- 200 with state change + an ``admin.scope_link.relinked`` audit row
  for the real mutation; the row's ``removed_at`` is cleared and the
  document re-appears in scope-filtered listings.
- Idempotent: re-linking an already-active row returns 200 with no
  extra audit row (empty log is the idempotency signal — same shape
  as ``unarchive``).
- 403 (KW_FORBIDDEN) when the caller lacks the ``admin`` role.

Reuses the audit-handler fixture pattern from
``test_admin_archive_unarchive.py`` so both admin emit paths flow
through the same wiring.
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
from app.schemas.scope import Scope
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


def _seed_with_active_link(
    services,
    document_id: str,
    *,
    scope_kind: str,
    scope_ref: str,
) -> Document:
    """Persist a document and add a single active scope link."""
    version = _make_version(document_id)
    document = Document.with_first_version(version)
    services.documents.catalog.save_document_with_version(document, version)
    services.documents.catalog.add_scope(
        document_id,
        Scope(
            kind=scope_kind,  # type: ignore[arg-type]
            ref=scope_ref,
            added_at=datetime(2026, 5, 4, 9, 0, tzinfo=UTC),
            added_by="alice",
        ),
    )
    return document


def _seed_with_removed_link(
    services,
    document_id: str,
    *,
    scope_kind: str,
    scope_ref: str,
) -> Document:
    """Persist a document, add a scope link, then soft-remove it."""
    document = _seed_with_active_link(
        services,
        document_id,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
    )
    services.documents.catalog.remove_scope(document_id, scope_kind, scope_ref)
    return document


# ─── 404 path ──────────────────────────────────────────────────────────


class TestNotFound:
    def test_returns_404_when_link_never_existed(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        # Seed a document but NO scope link — the (kind, ref) triple
        # has never been written, so the admin tool should 404.
        version = _make_version("doc-1")
        services.documents.catalog.save_document_with_version(
            Document.with_first_version(version),
            version,
        )
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/relink_scope?confirm=true",
            json={
                "document_id": "doc-1",
                "scope_kind": "swym_community",
                "scope_ref": "ghost-community",
            },
            headers=headers,
        )

        assert response.status_code == 404, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_NOT_FOUND"
        assert "doc-1" in body["error"]["message"]
        assert "ghost-community" in body["error"]["message"]


# ─── ?confirm=true defence-in-depth ────────────────────────────────────


class TestConfirmRequired:
    def test_missing_confirm_and_dry_run_returns_422(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_with_removed_link(services, "doc-1", scope_kind="swym_community", scope_ref="abc")
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/relink_scope",
            json={
                "document_id": "doc-1",
                "scope_kind": "swym_community",
                "scope_ref": "abc",
            },
            headers=headers,
        )

        assert response.status_code == 422, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_UNPROCESSABLE_ENTITY"

        # Defence-in-depth: state was NOT mutated.
        link = services.documents.catalog.get_scope_link("doc-1", "swym_community", "abc")
        assert link is not None
        assert link.removed_at is not None


# ─── ?dry_run=true ─────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_returns_summary_without_mutation(
        self,
        bearer_env: None,
        audit_capture: InMemoryAuditEventStore,
    ) -> None:
        client, services = _client_and_services()
        _seed_with_removed_link(services, "doc-1", scope_kind="swym_community", scope_ref="abc")
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/relink_scope?dry_run=true",
            json={
                "document_id": "doc-1",
                "scope_kind": "swym_community",
                "scope_ref": "abc",
            },
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dry_run"] is True
        assert body["relinked_at"] is None
        # ``removed_at_before`` reflects the soft-removed timestamp on
        # disk — must be a real datetime, not None.
        assert body["removed_at_before"] is not None

        # No state change: row still soft-removed.
        link = services.documents.catalog.get_scope_link("doc-1", "swym_community", "abc")
        assert link is not None
        assert link.removed_at is not None

        # Document still hidden from scope-filtered listing because
        # the link is still soft-removed.
        page, _ = services.documents.catalog.list_documents_in_scope(
            "swym_community", "abc", cursor=None, limit=10
        )
        assert page == []

        # No audit row.
        events = audit_capture.query(event_name="admin.scope_link.relinked")
        assert events == []


# ─── Real mutation (200 + audit) ──────────────────────────────────────


class TestActualRelink:
    def test_clears_removed_at_and_emits_audit_event(
        self,
        bearer_env: None,
        audit_capture: InMemoryAuditEventStore,
    ) -> None:
        client, services = _client_and_services()
        _seed_with_removed_link(services, "doc-1", scope_kind="swym_community", scope_ref="abc")
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/relink_scope?confirm=true",
            json={
                "document_id": "doc-1",
                "scope_kind": "swym_community",
                "scope_ref": "abc",
            },
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dry_run"] is False
        assert body["removed_at_before"] is not None
        assert body["relinked_at"] is not None

        # State change: row's ``removed_at`` cleared.
        link = services.documents.catalog.get_scope_link("doc-1", "swym_community", "abc")
        assert link is not None
        assert link.removed_at is None
        # ``added_by`` overwritten with the admin actor — that's the
        # documented "re-link is a fresh audit event" behaviour from #262.
        assert link.added_by == "tester"

        # Document re-visible in scope filters.
        page, _ = services.documents.catalog.list_documents_in_scope(
            "swym_community", "abc", cursor=None, limit=10
        )
        assert [d.id for d in page] == ["doc-1"]

        # Audit row emitted with the documented payload.
        events = audit_capture.query(event_name="admin.scope_link.relinked")
        assert len(events) == 1
        event = events[0]
        assert event.document_id == "doc-1"
        assert event.payload["scope_kind"] == "swym_community"
        assert event.payload["scope_ref"] == "abc"
        assert event.payload["actor"] == "tester"
        assert event.payload["actor_role"] == "admin"
        assert event.payload["removed_at_before"] is not None
        assert event.payload["relinked_at"] is not None


# ─── Idempotent (already-active) ──────────────────────────────────────


class TestIdempotent:
    def test_already_active_link_returns_200_without_audit_event(
        self,
        bearer_env: None,
        audit_capture: InMemoryAuditEventStore,
    ) -> None:
        client, services = _client_and_services()
        _seed_with_active_link(services, "doc-1", scope_kind="swym_community", scope_ref="abc")
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/archive/relink_scope?confirm=true",
            json={
                "document_id": "doc-1",
                "scope_kind": "swym_community",
                "scope_ref": "abc",
            },
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dry_run"] is False
        assert body["removed_at_before"] is None
        # ``relinked_at`` is None on a no-op so callers can detect
        # idempotent vs fresh transitions without scraping the audit
        # log.
        assert body["relinked_at"] is None

        # No extra audit row.
        events = audit_capture.query(event_name="admin.scope_link.relinked")
        assert events == []


# ─── 403 on non-admin caller ──────────────────────────────────────────


class TestForbiddenForNonAdmin:
    def test_reviewer_token_is_rejected(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_with_removed_link(services, "doc-1", scope_kind="swym_community", scope_ref="abc")
        headers = {"Authorization": f"Bearer {_token('reviewer')}"}

        response = client.post(
            "/admin/archive/relink_scope?confirm=true",
            json={
                "document_id": "doc-1",
                "scope_kind": "swym_community",
                "scope_ref": "abc",
            },
            headers=headers,
        )

        assert response.status_code == 403, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_FORBIDDEN"
        assert "reviewer" in body["error"]["message"]
        assert "admin" in body["error"]["message"]
