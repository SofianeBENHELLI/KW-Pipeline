"""Parametrized coverage for ``CatalogStore.list_archived_documents`` (D.9).

Pins the read-side admin-listing primitive that backs
``GET /admin/archive/archived_documents``. Both store impls run the
same Protocol contract via the ``store`` fixture, mirroring the
existing :mod:`tests.services.test_archive_filter` shape.

What's covered:

- Empty store returns ``([], None)``.
- Active docs are excluded; only ``archived_at IS NOT NULL`` rows surface.
- Sort order is ``archived_at DESC`` with ``id`` ASC tie-break.
- Cursor walk: ``(page1, page2, page3...)`` covers every row exactly
  once and the final page emits ``next_cursor=None``.
- ``InvalidCursor`` on a malformed token.
- ``Document.scopes`` includes soft-removed links so the route can
  surface "last active scope" — the standard read path filters them
  out, but the admin tool needs the full row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.schemas.scope import Scope
from app.services.catalog_store import (
    CatalogStore,
    InMemoryCatalogStore,
    InvalidCursor,
    SQLiteCatalogStore,
)


def _make_version(document_id: str) -> DocumentVersion:
    vid = f"{document_id}-v1"
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


def _seed_archived(
    store: CatalogStore,
    document_id: str,
    *,
    archived_at: datetime,
) -> Document:
    version = _make_version(document_id)
    document = Document.with_first_version(version)
    store.save_document_with_version(document, version)
    store.flag_document_archived(
        document_id,
        archived_at=archived_at,
        actor="cascade",
    )
    return document


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> CatalogStore:
    if request.param == "memory":
        return InMemoryCatalogStore()
    return SQLiteCatalogStore(tmp_path / "catalog.sqlite3")


# ─── Empty / hidden semantics ─────────────────────────────────────────


class TestEmptyAndFilter:
    def test_empty_store_returns_empty_page(self, store: CatalogStore) -> None:
        page, next_cursor = store.list_archived_documents(cursor=None, limit=10)
        assert page == []
        assert next_cursor is None

    def test_active_doc_is_excluded(self, store: CatalogStore) -> None:
        version = _make_version("doc-active")
        document = Document.with_first_version(version)
        store.save_document_with_version(document, version)
        _seed_archived(
            store,
            "doc-archived",
            archived_at=datetime(2026, 5, 4, 12, 0, tzinfo=UTC),
        )

        page, _ = store.list_archived_documents(cursor=None, limit=10)
        ids = [d.id for d in page]
        assert ids == ["doc-archived"]


# ─── Sort order ───────────────────────────────────────────────────────


class TestSortOrder:
    def test_archived_at_desc(self, store: CatalogStore) -> None:
        base = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        _seed_archived(store, "doc-old", archived_at=base)
        _seed_archived(store, "doc-mid", archived_at=base + timedelta(hours=1))
        _seed_archived(store, "doc-new", archived_at=base + timedelta(hours=2))

        page, _ = store.list_archived_documents(cursor=None, limit=10)
        assert [d.id for d in page] == ["doc-new", "doc-mid", "doc-old"]

    def test_id_asc_tie_break_for_same_archived_at(self, store: CatalogStore) -> None:
        same = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        _seed_archived(store, "doc-b", archived_at=same)
        _seed_archived(store, "doc-a", archived_at=same)
        _seed_archived(store, "doc-c", archived_at=same)

        page, _ = store.list_archived_documents(cursor=None, limit=10)
        # All same timestamp → id ASC tie-break.
        assert [d.id for d in page] == ["doc-a", "doc-b", "doc-c"]


# ─── Cursor pagination ────────────────────────────────────────────────


class TestCursorPagination:
    def test_walk_visits_every_row_exactly_once(self, store: CatalogStore) -> None:
        base = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        for i in range(5):
            _seed_archived(store, f"doc-{i}", archived_at=base + timedelta(hours=i))

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(10):  # safety net
            page, cursor = store.list_archived_documents(cursor=cursor, limit=2)
            seen.extend(d.id for d in page)
            if cursor is None:
                break
        # All 5 docs seen, newest-first ordering preserved across pages.
        assert seen == ["doc-4", "doc-3", "doc-2", "doc-1", "doc-0"]

    def test_invalid_cursor_raises(self, store: CatalogStore) -> None:
        with pytest.raises(InvalidCursor):
            store.list_archived_documents(cursor="not-a-cursor!!!", limit=10)

    def test_limit_exact_total_returns_no_cursor(self, store: CatalogStore) -> None:
        base = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        _seed_archived(store, "doc-a", archived_at=base)
        _seed_archived(store, "doc-b", archived_at=base + timedelta(hours=1))

        page, next_cursor = store.list_archived_documents(cursor=None, limit=2)
        assert [d.id for d in page] == ["doc-b", "doc-a"]
        # Limit equals total — no more pages.
        assert next_cursor is None


# ─── Soft-removed scopes leak through ─────────────────────────────────


class TestSoftRemovedScopesVisible:
    def test_soft_removed_scopes_are_visible_to_admin_listing(
        self,
        store: CatalogStore,
    ) -> None:
        """The admin route uses ``Document.scopes`` to surface "last
        scope removed". That field on the standard read path filters
        out soft-removed rows, but the admin listing needs them.
        """
        archived_at = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        _seed_archived(store, "doc-1", archived_at=archived_at)
        store.add_scope(
            "doc-1",
            Scope(
                kind="personal",
                ref="alice",
                added_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
                added_by="alice",
            ),
        )
        store.remove_scope("doc-1", "personal", "alice")

        page, _ = store.list_archived_documents(cursor=None, limit=10)
        assert len(page) == 1
        # The soft-removed link is on ``scopes`` because the admin path
        # bypasses the active-only filter.
        assert any(s.removed_at is not None for s in page[0].scopes)
