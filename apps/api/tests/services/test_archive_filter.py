"""Read-side hiding of flag-archived documents (ADR-020 §4, EPIC-D D.6).

Pins the catalog read-path filter that hides documents whose
``archived_at`` is set:

- :meth:`CatalogStore.list_documents` skips archived rows.
- :meth:`CatalogStore.get_document` returns ``None`` for archived rows
  (the route layer maps that to a 404, hidden-existence semantics).
- :meth:`CatalogStore.list_documents_in_scope` skips archived rows even
  when an active scope link still exists from before the archive
  transition.

Both store impls run the same Protocol contract via the ``store``
fixture. The internal admin-tool accessor
``_get_document_including_archived`` is intentionally excluded — the
future Archive/Purge Admin tool gets its own coverage when that ADR
lands.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.schemas.scope import Scope
from app.services.catalog_store import (
    CatalogStore,
    InMemoryCatalogStore,
    SQLiteCatalogStore,
)


def _make_version(document_id: str, version_id: str | None = None) -> DocumentVersion:
    vid = version_id or f"{document_id}-v1"
    return DocumentVersion(
        id=vid,
        document_id=document_id,
        version_number=1,
        filename="file.txt",
        content_type="text/plain",
        file_size=10,
        sha256=(vid + "_").ljust(64, "0"),
        storage_uri=f"memory://documents/{vid}/file.txt",
        status=DocumentVersionStatus.STORED,
    )


def _seed(store: CatalogStore, document_id: str) -> Document:
    version = _make_version(document_id)
    document = Document.with_first_version(version)
    store.save_document_with_version(document, version)
    return document


def _scope(kind: str, ref: str, *, added_by: str = "alice") -> Scope:
    return Scope(
        kind=kind,  # type: ignore[arg-type]
        ref=ref,
        added_at=datetime(2026, 5, 4, 12, 0, tzinfo=UTC),
        added_by=added_by,
    )


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> CatalogStore:
    if request.param == "memory":
        return InMemoryCatalogStore()
    return SQLiteCatalogStore(tmp_path / "catalog.sqlite3")


# ─── list_documents ────────────────────────────────────────────────────


class TestListDocumentsHidesArchived:
    def test_archived_doc_does_not_appear_in_list(self, store: CatalogStore):
        _seed(store, "doc-archived")
        _seed(store, "doc-active")

        store.flag_document_archived(
            "doc-archived",
            archived_at=datetime.now(UTC),
            actor="admin",
        )

        ids = [d.id for d in store.list_documents()]
        assert ids == ["doc-active"]

    def test_all_archived_returns_empty_list(self, store: CatalogStore):
        for doc_id in ("doc-1", "doc-2"):
            _seed(store, doc_id)
            store.flag_document_archived(
                doc_id,
                archived_at=datetime.now(UTC),
                actor="admin",
            )

        assert store.list_documents() == []


# ─── get_document ──────────────────────────────────────────────────────


class TestGetDocumentReturnsNoneForArchived:
    def test_archived_doc_returns_none(self, store: CatalogStore):
        _seed(store, "doc-1")
        store.flag_document_archived(
            "doc-1",
            archived_at=datetime.now(UTC),
            actor="admin",
        )

        assert store.get_document("doc-1") is None

    def test_active_doc_still_returns(self, store: CatalogStore):
        """Sanity: the filter only fires on archived rows."""
        _seed(store, "doc-1")

        result = store.get_document("doc-1")
        assert result is not None
        assert result.id == "doc-1"
        assert result.archived_at is None


# ─── list_documents_in_scope ───────────────────────────────────────────


class TestListDocumentsInScopeHidesArchived:
    def test_archived_doc_hidden_even_with_active_scope_link(
        self,
        store: CatalogStore,
    ):
        """An archived doc whose scope link is still active is still hidden.

        The cascade flags scope links AND the document; this test
        documents the safety property in the reverse direction —
        archive flag wins over a stale active link.
        """
        _seed(store, "doc-1")
        store.add_scope("doc-1", _scope("personal", "dev", added_by="dev"))
        # Flag the archive WITHOUT touching the scope link, so we have
        # the bizarre-but-valid state of "archived doc with active link".
        store.flag_document_archived(
            "doc-1",
            archived_at=datetime.now(UTC),
            actor="admin",
        )

        page, next_cursor = store.list_documents_in_scope(
            "personal",
            "dev",
            cursor=None,
            limit=10,
        )
        assert page == []
        assert next_cursor is None

    def test_active_doc_in_scope_still_listed(self, store: CatalogStore):
        """Sanity: non-archived doc with an active scope link still surfaces."""
        _seed(store, "doc-1")
        store.add_scope("doc-1", _scope("personal", "dev", added_by="dev"))

        page, _ = store.list_documents_in_scope(
            "personal",
            "dev",
            cursor=None,
            limit=10,
        )
        assert [d.id for d in page] == ["doc-1"]


# ─── archived_at flow on the public schema ─────────────────────────────


class TestArchivedAtRoundTrip:
    def test_internal_accessor_returns_archived_doc_with_timestamp(
        self,
        store: CatalogStore,
    ):
        """``_get_document_including_archived`` is the admin-tool back door.

        It is NOT part of the public Protocol — but we pin its behaviour
        so the future Archive/Purge Admin tool has a documented entry
        point. The cascade service relies on it for the idempotent
        re-archive path.
        """
        _seed(store, "doc-1")
        archived_at = datetime.now(UTC)
        store.flag_document_archived(
            "doc-1",
            archived_at=archived_at,
            actor="admin",
        )

        result = store._get_document_including_archived("doc-1")  # type: ignore[attr-defined]
        assert result is not None
        assert result.id == "doc-1"
        assert result.archived_at is not None
        # Round-trip equality check is robust to ISO string serialization
        # in the SQLite path.
        assert result.archived_at == archived_at

    def test_flag_document_archived_missing_raises_keyerror(self, store: CatalogStore):
        with pytest.raises(KeyError, match="Document not found"):
            store.flag_document_archived(
                "missing",
                archived_at=datetime.now(UTC),
                actor="admin",
            )

    def test_flag_document_archived_is_idempotent(self, store: CatalogStore):
        """Re-archiving preserves the original ``archived_at`` (audit-faithful)."""
        _seed(store, "doc-1")
        first_ts = datetime(2026, 5, 4, 9, 0, tzinfo=UTC)
        second_ts = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)

        store.flag_document_archived("doc-1", archived_at=first_ts, actor="admin")
        store.flag_document_archived("doc-1", archived_at=second_ts, actor="admin")

        result = store._get_document_including_archived("doc-1")  # type: ignore[attr-defined]
        assert result is not None
        assert result.archived_at == first_ts
