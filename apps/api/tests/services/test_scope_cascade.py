"""Cascade-after-scope-removal flow (EPIC-D D.6 + D.7, ADR-020 §4).

Pins the flag-only cascade contract introduced by #262:

- Soft-removing the last active scope link archives the document.
- Soft-removing one of N active links DOES NOT archive the document.
- The cascade is idempotent — a second pass over an already-cascaded
  ``(scope_kind, scope_ref)`` produces no new audit-event traffic.
- KG cleanup is best-effort: a Neo4j hiccup leaves the catalog
  archive flag intact and the cascade still completes.

Both store impls run the same Protocol contract via the ``store``
fixture, mirroring the parametrised pattern used by
``tests/services/test_scope_persistence.py``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.schemas.scope import Scope
from app.services.audit_event_store import InMemoryAuditEventStore
from app.services.audit_log_handler import AuditLogHandler
from app.services.catalog_store import (
    CatalogStore,
    InMemoryCatalogStore,
    SQLiteCatalogStore,
)
from app.services.scope_cascade_service import (
    CascadeFailure,
    CascadeResult,
    ScopeCascadeService,
)

# ─── Fixtures ──────────────────────────────────────────────────────────


def _make_version(
    document_id: str,
    *,
    version_id: str | None = None,
    sha_seed: str | None = None,
) -> DocumentVersion:
    vid = version_id or f"{document_id}-v1"
    return DocumentVersion(
        id=vid,
        document_id=document_id,
        version_number=1,
        filename="file.txt",
        content_type="text/plain",
        file_size=10,
        sha256=(sha_seed or vid + "_").ljust(64, "0"),
        storage_uri=f"memory://documents/{vid}/file.txt",
        status=DocumentVersionStatus.STORED,
    )


def _scope(kind: str, ref: str, *, added_by: str = "alice") -> Scope:
    return Scope(
        kind=kind,  # type: ignore[arg-type]
        ref=ref,
        added_at=datetime(2026, 5, 4, 12, 0, tzinfo=UTC),
        added_by=added_by,
    )


def _seed(store: CatalogStore, document_id: str) -> Document:
    version = _make_version(document_id)
    document = Document.with_first_version(version)
    store.save_document_with_version(document, version)
    return document


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> CatalogStore:
    if request.param == "memory":
        return InMemoryCatalogStore()
    return SQLiteCatalogStore(tmp_path / "catalog.sqlite3")


@pytest.fixture
def audit_capture():
    """Attach an in-memory audit store via the structured-logging handler.

    Mirrors how the production wiring captures the
    ``document.archived_orphan`` event: the cascade emits via
    ``log.info("document.archived_orphan", extra={...})`` and the
    handler persists into the store. We attach to ``app.services``
    (the parent of ``scope_cascade_service``) so the handler picks the
    record up; remove on teardown to keep the global root logger clean.
    """
    store = InMemoryAuditEventStore()
    handler = AuditLogHandler(store)
    target = logging.getLogger("app.services")
    previous_level = target.level
    if target.level == logging.NOTSET:
        target.setLevel(logging.INFO)
    target.addHandler(handler)
    try:
        yield store
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)


# ─── Last-scope-removed → archive ──────────────────────────────────────


class TestSwymCommunityDeleted:
    def test_archives_every_orphan_document(
        self,
        store: CatalogStore,
        audit_capture: InMemoryAuditEventStore,
    ):
        """3 documents linked only to ``swym_community:abc`` → all 3 archived."""
        for doc_id in ("doc-1", "doc-2", "doc-3"):
            _seed(store, doc_id)
            store.add_scope(doc_id, _scope("swym_community", "abc"))

        cascade = ScopeCascadeService(catalog=store)
        result = cascade.on_swym_community_deleted("abc", actor="admin")

        assert isinstance(result, CascadeResult)
        assert result.scope_links_flagged == 3
        assert sorted(result.documents_archived) == ["doc-1", "doc-2", "doc-3"]
        assert result.failures == []

        # Every document now hidden from list_documents and get_document.
        assert store.list_documents() == []
        for doc_id in ("doc-1", "doc-2", "doc-3"):
            assert store.get_document(doc_id) is None

        # Three archived_orphan events with the documented payload.
        events = audit_capture.query(event_name="document.archived_orphan")
        assert len(events) == 3
        document_ids = {event.document_id for event in events}
        assert document_ids == {"doc-1", "doc-2", "doc-3"}
        for event in events:
            assert event.payload["scope_kind"] == "swym_community"
            assert event.payload["scope_ref"] == "abc"
            assert event.payload["actor"] == "admin"
            assert event.payload["reason"] == "all_scopes_removed"

    def test_multi_scope_document_is_not_archived(
        self,
        store: CatalogStore,
        audit_capture: InMemoryAuditEventStore,
    ):
        """A doc in both ``swym_community:abc`` and ``personal:dev`` survives."""
        _seed(store, "doc-1")
        store.add_scope("doc-1", _scope("swym_community", "abc"))
        store.add_scope("doc-1", _scope("personal", "dev", added_by="dev"))

        cascade = ScopeCascadeService(catalog=store)
        result = cascade.on_swym_community_deleted("abc", actor="admin")

        assert result.scope_links_flagged == 1
        assert result.documents_archived == []

        # personal:dev link survives; swym_community:abc link is flagged.
        remaining = store.list_scopes_for_document("doc-1")
        assert len(remaining) == 1
        assert remaining[0].kind == "personal"
        assert remaining[0].ref == "dev"

        # Document still visible — archive flag was NOT set.
        assert store.get_document("doc-1") is not None
        assert audit_capture.query(event_name="document.archived_orphan") == []

    def test_cascade_is_idempotent(
        self,
        store: CatalogStore,
        audit_capture: InMemoryAuditEventStore,
    ):
        """A second cascade on the same community is a complete no-op."""
        _seed(store, "doc-1")
        store.add_scope("doc-1", _scope("swym_community", "abc"))

        cascade = ScopeCascadeService(catalog=store)
        first = cascade.on_swym_community_deleted("abc", actor="admin")
        second = cascade.on_swym_community_deleted("abc", actor="admin")

        assert first.scope_links_flagged == 1
        assert first.documents_archived == ["doc-1"]
        # Second pass: link already flagged, doc already archived, nothing
        # new to do — and crucially, no second audit event.
        assert second.scope_links_flagged == 0
        assert second.documents_archived == []
        assert second.failures == []

        events = audit_capture.query(event_name="document.archived_orphan")
        assert len(events) == 1, "Idempotent cascade must NOT emit a duplicate event."

    def test_already_archived_document_preserves_original_timestamp(
        self,
        store: CatalogStore,
    ):
        """Re-archiving an already-archived doc keeps the first ``archived_at``."""
        _seed(store, "doc-1")
        store.add_scope("doc-1", _scope("swym_community", "abc"))

        cascade = ScopeCascadeService(catalog=store)
        cascade.on_swym_community_deleted("abc", actor="admin")
        # Capture the original archive timestamp via the internal accessor
        # (Protocol-level reads return None for archived rows).
        first = store._get_document_including_archived("doc-1")  # type: ignore[attr-defined]
        assert first is not None
        original_archived_at = first.archived_at
        assert original_archived_at is not None

        # Run cascade again — the archive flag must stay pinned to the
        # first transition, not bumped to "now".
        cascade.on_swym_community_deleted("abc", actor="admin")
        second = store._get_document_including_archived("doc-1")  # type: ignore[attr-defined]
        assert second is not None
        assert second.archived_at == original_archived_at


# ─── KG cleanup failure isolation ──────────────────────────────────────


class TestKgCleanupBestEffort:
    def test_kg_failure_does_not_block_archive(
        self,
        store: CatalogStore,
        audit_capture: InMemoryAuditEventStore,
        caplog: pytest.LogCaptureFixture,
    ):
        """Neo4j hiccup → catalog archive flag still lands; warning logged."""
        _seed(store, "doc-1")
        store.add_scope("doc-1", _scope("swym_community", "abc"))

        def boom(document_id: str) -> None:  # noqa: ARG001
            raise RuntimeError("neo4j unreachable")

        cascade = ScopeCascadeService(catalog=store, kg_reconciler=boom)
        # Use INFO so the audit-handler-bound ``document.archived_orphan``
        # event still flows; caplog will pick the WARNING up regardless.
        with caplog.at_level(logging.INFO, logger="app.services.scope_cascade_service"):
            result = cascade.on_swym_community_deleted("abc", actor="admin")

        assert result.documents_archived == ["doc-1"]
        # Even though KG cleanup raised, the archive event was still
        # emitted (catalog is the source of truth).
        events = audit_capture.query(event_name="document.archived_orphan")
        assert len(events) == 1

        # The fire-and-log warning surfaced at the boundary.
        assert any(
            record.message == "knowledge.archive_cascade_kg_cleanup_failed"
            for record in caplog.records
        )

    def test_kg_reconciler_called_per_archived_document(self, store: CatalogStore):
        """Cleanup callable receives every archived document_id."""
        for doc_id in ("doc-1", "doc-2"):
            _seed(store, doc_id)
            store.add_scope(doc_id, _scope("swym_community", "abc"))

        called_with: list[str] = []

        def reconciler(document_id: str) -> None:
            called_with.append(document_id)

        cascade = ScopeCascadeService(catalog=store, kg_reconciler=reconciler)
        cascade.on_swym_community_deleted("abc", actor="admin")

        assert sorted(called_with) == ["doc-1", "doc-2"]


# ─── Result shape introspection ────────────────────────────────────────


class TestRaceConditions:
    """Defensive paths exercised when concurrent state-changes interleave."""

    def test_doc_already_archived_with_active_link_does_not_re_emit(
        self,
        store: CatalogStore,
        audit_capture: InMemoryAuditEventStore,
    ):
        """Pre-archived doc with stale active link → no fresh audit event.

        Pins the ``is_fresh_archive`` branch: when ``flag_document_archived``
        observes an already-archived row, the cascade returns that row
        with the OLD ``archived_at`` and the per-document path skips
        the KG cleanup + audit emit so a re-cascade is a strict no-op.
        """
        _seed(store, "doc-1")
        store.add_scope("doc-1", _scope("swym_community", "abc"))

        # Pre-archive the document directly — the active scope link
        # remains, simulating an out-of-band archive (e.g. a hand-fix).
        store.flag_document_archived(
            "doc-1",
            archived_at=datetime(2026, 5, 4, 9, 0, tzinfo=UTC),
            actor="admin",
        )

        cascade = ScopeCascadeService(catalog=store)
        result = cascade.on_swym_community_deleted("abc", actor="admin")

        # The link was active so it still gets soft-removed (cascade
        # must not skip the link side just because the doc was already
        # archived — the link is still indexed for the future Admin tool).
        assert result.scope_links_flagged == 1
        # No fresh archive transition happened, so the result records 0
        # archives and no audit event was emitted.
        assert result.documents_archived == []
        events = audit_capture.query(event_name="document.archived_orphan")
        assert events == []


class TestCascadeResult:
    def test_failure_surfaces_in_result(self, store: CatalogStore):
        """A flag_document_archived raise lands in CascadeResult.failures."""

        # Wrap the store so flag_document_archived raises for one doc.
        class FlakyCatalog:
            def __init__(self, inner: CatalogStore):
                self._inner = inner

            def __getattr__(self, name: str):
                return getattr(self._inner, name)

            def flag_document_archived(self, document_id: str, **kwargs):
                if document_id == "doc-1":
                    raise RuntimeError("disk full")
                return self._inner.flag_document_archived(document_id, **kwargs)

        for doc_id in ("doc-1", "doc-2"):
            _seed(store, doc_id)
            store.add_scope(doc_id, _scope("swym_community", "abc"))

        flaky: CatalogStore = FlakyCatalog(store)  # type: ignore[assignment]
        cascade = ScopeCascadeService(catalog=flaky)
        result = cascade.on_swym_community_deleted("abc", actor="admin")

        # doc-2 was archived; doc-1 surfaces as a failure.
        assert result.documents_archived == ["doc-2"]
        assert len(result.failures) == 1
        assert isinstance(result.failures[0], CascadeFailure)
        assert result.failures[0].document_id == "doc-1"
        assert "disk full" in result.failures[0].error
