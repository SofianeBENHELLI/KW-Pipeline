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

    def test_returned_documents_carry_their_scope_links(self, store: CatalogStore):
        """#258 — every document returned by ``list_documents_in_scope``
        must carry its active scope links on ``Document.scopes`` so
        the route layer can surface them without a follow-up
        ``list_scopes_for_document`` call.
        """
        _seed_document(store, document_id="d1")
        _seed_document(store, document_id="d2")
        store.add_scope("d1", _scope(kind="swym_community", ref="abc"))
        store.add_scope("d1", _scope(kind="project", ref="p1"))
        store.add_scope("d2", _scope(kind="swym_community", ref="abc"))

        page, _ = store.list_documents_in_scope("swym_community", "abc", cursor=None, limit=10)
        scopes_by_id = {d.id: d.scopes for d in page}
        assert {(s.kind, s.ref) for s in scopes_by_id["d1"]} == {
            ("swym_community", "abc"),
            ("project", "p1"),
        }
        assert {(s.kind, s.ref) for s in scopes_by_id["d2"]} == {
            ("swym_community", "abc"),
        }

    def test_returned_documents_filter_soft_removed_scopes(self, store: CatalogStore):
        """Soft-removed (``removed_at IS NOT NULL``, per #262) links
        must not surface on the populated ``Document.scopes`` field —
        the no-delete policy keeps the row but every read path hides
        it."""
        _seed_document(store, document_id="d1")
        store.add_scope("d1", _scope(kind="swym_community", ref="abc"))
        store.add_scope("d1", _scope(kind="project", ref="p1"))
        store.remove_scope("d1", "project", "p1")

        page, _ = store.list_documents_in_scope("swym_community", "abc", cursor=None, limit=10)
        assert len(page) == 1
        assert {(s.kind, s.ref) for s in page[0].scopes} == {
            ("swym_community", "abc"),
        }


class TestDocumentScopesPopulation:
    """#258 — every read path that returns a Document populates
    ``Document.scopes`` with the active links so the frontend can
    render its scope chip without a follow-up call.
    """

    def test_get_document_populates_scopes(self, store: CatalogStore):
        _seed_document(store, document_id="d1")
        store.add_scope("d1", _scope(kind="personal", ref="dev"))
        store.add_scope("d1", _scope(kind="project", ref="p1"))

        document = store.get_document("d1")
        assert document is not None
        assert {(s.kind, s.ref) for s in document.scopes} == {
            ("personal", "dev"),
            ("project", "p1"),
        }

    def test_get_document_filters_soft_removed_scopes(self, store: CatalogStore):
        _seed_document(store, document_id="d1")
        store.add_scope("d1", _scope(kind="personal", ref="dev"))
        store.add_scope("d1", _scope(kind="project", ref="p1"))
        store.remove_scope("d1", "project", "p1")

        document = store.get_document("d1")
        assert document is not None
        assert [(s.kind, s.ref) for s in document.scopes] == [("personal", "dev")]

    def test_list_documents_populates_scopes(self, store: CatalogStore):
        _seed_document(store, document_id="d1")
        _seed_document(store, document_id="d2")
        store.add_scope("d1", _scope(kind="personal", ref="dev"))
        store.add_scope("d2", _scope(kind="swym_community", ref="abc"))

        documents = store.list_documents()
        scopes_by_id = {d.id: d.scopes for d in documents}
        assert {(s.kind, s.ref) for s in scopes_by_id["d1"]} == {("personal", "dev")}
        assert {(s.kind, s.ref) for s in scopes_by_id["d2"]} == {
            ("swym_community", "abc"),
        }

    def test_list_documents_empty_scopes_for_unlinked_doc(self, store: CatalogStore):
        _seed_document(store, document_id="d1")
        documents = store.list_documents()
        assert documents[0].scopes == []

    def test_list_documents_filters_soft_removed_scopes(self, store: CatalogStore):
        _seed_document(store, document_id="d1")
        store.add_scope("d1", _scope(kind="personal", ref="dev"))
        store.add_scope("d1", _scope(kind="project", ref="p1"))
        store.remove_scope("d1", "project", "p1")

        documents = store.list_documents()
        assert [(s.kind, s.ref) for s in documents[0].scopes] == [("personal", "dev")]


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


class TestSoftRemoveAndReactivate:
    """No-delete policy (2026-05-05): ``remove_scope`` flags the row as
    ``removed_at`` instead of physically deleting it; ``add_scope`` for
    the same triple reactivates a flagged row with the new caller's
    identity. Source data is never lost — the future Archive/Purge
    Admin tool is the only path to physical deletion.
    """

    def test_remove_scope_does_not_delete_row_physically_in_sqlite(self, tmp_path: Path) -> None:
        """Direct SQL inspection: a soft-removed row stays in the table
        with a non-null ``removed_at``. Purely SQLite-only — the
        in-memory store is exercised via the public Protocol elsewhere."""
        import sqlite3

        store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")
        _seed_document(store)
        store.add_scope("doc-1", _scope(kind="swym_community", ref="abc"))
        store.remove_scope("doc-1", "swym_community", "abc")

        with sqlite3.connect(tmp_path / "catalog.sqlite3") as conn:
            row = conn.execute(
                "SELECT scope_kind, scope_ref, removed_at FROM document_scopes "
                "WHERE document_id = ?",
                ("doc-1",),
            ).fetchone()
        assert row is not None, "row must NOT be physically deleted"
        assert row[0] == "swym_community"
        assert row[1] == "abc"
        assert row[2] is not None, "removed_at must be stamped"

    def test_add_after_remove_reactivates_with_new_actor(self, store: CatalogStore):
        """Re-linking a soft-removed scope reactivates the row with the
        new caller's ``added_at`` / ``added_by`` (a re-link is a fresh
        audit event). The link reappears on read paths."""
        _seed_document(store)
        original = _scope(
            kind="swym_community",
            ref="abc-123",
            added_by="alice",
            added_at=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
        )
        store.add_scope("doc-1", original)
        store.remove_scope("doc-1", "swym_community", "abc-123")

        # After remove, the read paths hide the link.
        assert store.list_scopes_for_document("doc-1") == []
        page, _ = store.list_documents_in_scope("swym_community", "abc-123", cursor=None, limit=10)
        assert page == []

        # Reactivate with a different actor + later timestamp.
        relink = _scope(
            kind="swym_community",
            ref="abc-123",
            added_by="bob",
            added_at=datetime(2026, 5, 5, 9, 0, tzinfo=UTC),
        )
        store.add_scope("doc-1", relink)

        scopes = store.list_scopes_for_document("doc-1")
        assert len(scopes) == 1
        assert scopes[0].kind == "swym_community"
        assert scopes[0].ref == "abc-123"
        assert scopes[0].added_by == "bob", (
            "reactivation overwrites added_by with the re-link caller"
        )
        assert scopes[0].added_at == datetime(2026, 5, 5, 9, 0, tzinfo=UTC)
        assert scopes[0].removed_at is None

        # The reverse index also picks the reactivation up.
        page, _ = store.list_documents_in_scope("swym_community", "abc-123", cursor=None, limit=10)
        assert {d.id for d in page} == {"doc-1"}

    def test_remove_scope_is_invisible_to_list_scopes(self, store: CatalogStore):
        _seed_document(store)
        store.add_scope("doc-1", _scope(kind="personal", ref="dev"))
        store.add_scope("doc-1", _scope(kind="swym_community", ref="abc"))
        store.remove_scope("doc-1", "swym_community", "abc")

        scopes = store.list_scopes_for_document("doc-1")
        assert [(s.kind, s.ref) for s in scopes] == [("personal", "dev")], (
            "removed scope must not appear in list_scopes_for_document"
        )

    def test_double_remove_preserves_original_removed_at(self, tmp_path: Path) -> None:
        """Removing an already-removed link is a no-op — the original
        ``removed_at`` timestamp is preserved (audit-faithful: the
        first removal is the canonical event)."""
        import sqlite3
        import time

        store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")
        _seed_document(store)
        store.add_scope("doc-1", _scope(kind="project", ref="p1"))
        store.remove_scope("doc-1", "project", "p1")

        with sqlite3.connect(tmp_path / "catalog.sqlite3") as conn:
            first_removed_at = conn.execute(
                "SELECT removed_at FROM document_scopes WHERE document_id = ?",
                ("doc-1",),
            ).fetchone()[0]

        # Wait long enough that a second timestamp would differ if it
        # were stamped.
        time.sleep(0.01)
        store.remove_scope("doc-1", "project", "p1")

        with sqlite3.connect(tmp_path / "catalog.sqlite3") as conn:
            second_removed_at = conn.execute(
                "SELECT removed_at FROM document_scopes WHERE document_id = ?",
                ("doc-1",),
            ).fetchone()[0]

        assert first_removed_at == second_removed_at
