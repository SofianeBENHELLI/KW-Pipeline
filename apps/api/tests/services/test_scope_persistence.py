"""Workspace scope CRUD on the catalog (ADR-020 §1, EPIC-D D.1, #218).

Both store impls (:class:`InMemoryCatalogStore` and
:class:`SQLiteCatalogStore`) implement the same Protocol contract, so
every scenario is parametrized over both via the ``store`` fixture.
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


def _make_version(
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    sha256: str | None = None,
) -> DocumentVersion:
    return DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=1,
        filename="file.txt",
        content_type="text/plain",
        file_size=10,
        sha256=sha256 or (version_id + "_").ljust(64, "0"),
        storage_uri=f"memory://documents/{version_id}/file.txt",
        status=DocumentVersionStatus.STORED,
    )


def _make_document(version: DocumentVersion) -> Document:
    return Document.with_first_version(version)


def _scope(
    kind: str = "personal",
    ref: str = "dev",
    added_by: str = "dev",
    added_at: datetime | None = None,
) -> Scope:
    return Scope(
        kind=kind,  # type: ignore[arg-type]
        ref=ref,
        added_at=added_at or datetime(2026, 5, 4, 12, 0, tzinfo=UTC),
        added_by=added_by,
    )


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> CatalogStore:
    """Yield each catalog impl in turn so every test runs against both.

    Mirrors the in-memory / SQLite split that backs the production wiring
    (``build_services`` vs ``build_persistent_services``).
    """
    if request.param == "memory":
        return InMemoryCatalogStore()
    return SQLiteCatalogStore(tmp_path / "catalog.sqlite3")


def _seed_document(store: CatalogStore, document_id: str = "doc-1") -> Document:
    version = _make_version(document_id=document_id, version_id=f"{document_id}-v1")
    document = _make_document(version)
    store.save_document_with_version(document, version)
    return document


class TestAddScope:
    def test_add_scope_records_link(self, store: CatalogStore):
        _seed_document(store)
        store.add_scope("doc-1", _scope(kind="personal", ref="dev"))

        scopes = store.list_scopes_for_document("doc-1")

        assert len(scopes) == 1
        assert scopes[0].kind == "personal"
        assert scopes[0].ref == "dev"
        assert scopes[0].added_by == "dev"

    def test_add_scope_is_idempotent_on_kind_ref(self, store: CatalogStore):
        """Re-adding the same (kind, ref) is a no-op — the first-write
        ``added_at`` / ``added_by`` are preserved so the audit trail
        records who *originally* linked the document."""
        _seed_document(store)
        first = _scope(
            kind="swym_community",
            ref="abc-123",
            added_by="alice",
            added_at=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
        )
        store.add_scope("doc-1", first)
        second = _scope(
            kind="swym_community",
            ref="abc-123",
            added_by="bob",
            added_at=datetime(2026, 5, 4, 11, 0, tzinfo=UTC),
        )
        store.add_scope("doc-1", second)

        scopes = store.list_scopes_for_document("doc-1")
        assert len(scopes) == 1
        assert scopes[0].added_by == "alice"

    def test_add_scope_supports_multi_scope_per_document(self, store: CatalogStore):
        _seed_document(store)
        store.add_scope("doc-1", _scope(kind="personal", ref="dev"))
        store.add_scope("doc-1", _scope(kind="swym_community", ref="abc-123"))
        store.add_scope("doc-1", _scope(kind="project", ref="proj-9"))

        scopes = store.list_scopes_for_document("doc-1")
        kinds = {(s.kind, s.ref) for s in scopes}
        assert kinds == {
            ("personal", "dev"),
            ("swym_community", "abc-123"),
            ("project", "proj-9"),
        }


class TestListScopesForDocument:
    def test_returns_empty_for_unlinked_document(self, store: CatalogStore):
        _seed_document(store)
        assert store.list_scopes_for_document("doc-1") == []

    def test_returns_empty_for_unknown_document(self, store: CatalogStore):
        assert store.list_scopes_for_document("missing-doc") == []


class TestListDocumentsInScope:
    def test_returns_documents_linked_to_the_scope(self, store: CatalogStore):
        _seed_document(store, document_id="d1")
        _seed_document(store, document_id="d2")
        _seed_document(store, document_id="d3")
        store.add_scope("d1", _scope(kind="swym_community", ref="abc"))
        store.add_scope("d2", _scope(kind="swym_community", ref="abc"))
        store.add_scope("d3", _scope(kind="swym_community", ref="other"))

        page, next_cursor = store.list_documents_in_scope(
            "swym_community", "abc", cursor=None, limit=10
        )

        assert {d.id for d in page} == {"d1", "d2"}
        assert next_cursor is None

    def test_pagination_emits_next_cursor_until_drained(self, store: CatalogStore):
        for i in range(5):
            _seed_document(store, document_id=f"d{i}")
            store.add_scope(f"d{i}", _scope(kind="project", ref="p1"))

        first_page, cursor1 = store.list_documents_in_scope("project", "p1", cursor=None, limit=2)
        assert len(first_page) == 2
        assert cursor1 is not None

        second_page, cursor2 = store.list_documents_in_scope(
            "project", "p1", cursor=cursor1, limit=2
        )
        assert len(second_page) == 2
        assert cursor2 is not None

        third_page, cursor3 = store.list_documents_in_scope(
            "project", "p1", cursor=cursor2, limit=2
        )
        assert len(third_page) == 1
        assert cursor3 is None

        # Concatenating every page yields every document exactly once.
        seen = {d.id for d in first_page + second_page + third_page}
        assert seen == {f"d{i}" for i in range(5)}

    def test_returns_empty_for_unknown_scope(self, store: CatalogStore):
        page, next_cursor = store.list_documents_in_scope(
            "swym_community", "never", cursor=None, limit=10
        )
        assert page == []
        assert next_cursor is None


class TestRemoveScope:
    def test_remove_scope_drops_link(self, store: CatalogStore):
        _seed_document(store)
        store.add_scope("doc-1", _scope(kind="personal", ref="dev"))
        store.add_scope("doc-1", _scope(kind="swym_community", ref="abc"))

        store.remove_scope("doc-1", "swym_community", "abc")

        scopes = store.list_scopes_for_document("doc-1")
        assert [(s.kind, s.ref) for s in scopes] == [("personal", "dev")]

    def test_remove_scope_is_idempotent(self, store: CatalogStore):
        _seed_document(store)
        # Removing a link that was never created must not raise.
        store.remove_scope("doc-1", "swym_community", "never")

    def test_remove_scope_drops_reverse_index_entry(self, store: CatalogStore):
        _seed_document(store, document_id="d1")
        _seed_document(store, document_id="d2")
        store.add_scope("d1", _scope(kind="project", ref="p1"))
        store.add_scope("d2", _scope(kind="project", ref="p1"))
        store.remove_scope("d1", "project", "p1")

        page, _ = store.list_documents_in_scope("project", "p1", cursor=None, limit=10)
        assert {d.id for d in page} == {"d2"}
