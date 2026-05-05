"""Cursor pagination on `GET /documents` (issue #38).

The route returns ``{"items": [...], "next_cursor": str | None}``. ``cursor``
is opaque base64 over ``(created_at_iso, id)`` of the last returned row, so
the next page is rows strictly greater than that tuple under the stable
``(created_at ASC, id ASC)`` ordering.

These tests exercise both the in-memory store (default for `create_app()`)
and the SQLite store (via `create_app(persistent=True, ...)`) so the SQL
tuple comparison stays in lockstep with the in-memory slice.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.document import Document, DocumentVersion
from app.services.catalog_store import (
    InMemoryCatalogStore,
    InvalidCursor,
    SQLiteCatalogStore,
    _decode_cursor,
    _encode_cursor,
)


def _upload(client: TestClient, body: bytes, filename: str = "p.txt") -> dict:
    return client.post(
        "/documents/upload",
        files={"file": (filename, body, "text/plain")},
    ).json()


# --------------------------- Cursor codec --------------------------- #


class TestCursorCodec:
    def test_round_trip_preserves_value(self):
        when = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
        token = _encode_cursor((when, "doc-id"))

        decoded_when, decoded_id = _decode_cursor(token)

        assert decoded_when == when
        assert decoded_id == "doc-id"

    def test_decode_rejects_non_base64(self):
        with pytest.raises(InvalidCursor, match="not valid base64"):
            _decode_cursor("not base64!!!")

    def test_decode_rejects_non_json_payload(self):
        # Valid base64 but the decoded bytes aren't JSON.
        import base64

        token = base64.urlsafe_b64encode(b"\xff\xfe").decode("ascii")
        with pytest.raises(InvalidCursor):
            _decode_cursor(token)

    def test_decode_rejects_wrong_shape(self):
        import base64
        import json

        token = base64.urlsafe_b64encode(json.dumps({"hello": "world"}).encode()).decode("ascii")
        with pytest.raises(InvalidCursor, match="must be a"):
            _decode_cursor(token)

    def test_decode_rejects_wrong_length_list(self):
        import base64
        import json

        token = base64.urlsafe_b64encode(json.dumps(["only one"]).encode()).decode("ascii")
        with pytest.raises(InvalidCursor, match="must be a"):
            _decode_cursor(token)

    def test_decode_rejects_non_string_fields(self):
        import base64
        import json

        token = base64.urlsafe_b64encode(json.dumps([1, 2]).encode()).decode("ascii")
        with pytest.raises(InvalidCursor, match="must be strings"):
            _decode_cursor(token)

    def test_decode_rejects_bad_iso_datetime(self):
        import base64
        import json

        token = base64.urlsafe_b64encode(json.dumps(["not a date", "doc-id"]).encode()).decode(
            "ascii"
        )
        with pytest.raises(InvalidCursor, match="ISO datetime"):
            _decode_cursor(token)


# --------------------------- HTTP route --------------------------- #


class TestListDocumentsRoute:
    def test_empty_catalog_returns_empty_page(self):
        client = TestClient(create_app())

        response = client.get("/documents")

        assert response.status_code == 200
        assert response.json() == {"items": [], "next_cursor": None}

    def test_short_page_has_null_next_cursor(self):
        client = TestClient(create_app())
        version = _upload(client, b"only-one")

        response = client.get("/documents?limit=50")

        body = response.json()
        assert response.status_code == 200
        assert len(body["items"]) == 1
        assert body["items"][0]["id"] == version["document_id"]
        assert body["next_cursor"] is None

    def test_multi_page_traversal_walks_every_id_in_order(self):
        client = TestClient(create_app())
        # Three uploads with distinct bytes so they don't dedupe.
        first = _upload(client, b"alpha")
        second = _upload(client, b"beta")
        third = _upload(client, b"gamma")
        expected_order = [first["document_id"], second["document_id"], third["document_id"]]

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(3):
            url = "/documents?limit=1"
            if cursor is not None:
                url += f"&cursor={cursor}"
            page = client.get(url).json()
            assert len(page["items"]) == 1
            seen.append(page["items"][0]["id"])
            cursor = page["next_cursor"]

        assert seen == expected_order
        # The third page reported a cursor (it was full); a fourth call must
        # return an empty page with no further cursor.
        assert cursor is not None
        empty_page = client.get(f"/documents?limit=1&cursor={cursor}").json()
        assert empty_page == {"items": [], "next_cursor": None}

    def test_invalid_cursor_returns_400(self):
        client = TestClient(create_app())

        response = client.get("/documents?cursor=not-valid-cursor!!!")

        assert response.status_code == 400
        assert "Invalid cursor" in response.json()["detail"]

    def test_limit_below_minimum_returns_400(self):
        client = TestClient(create_app())

        response = client.get("/documents?limit=0")

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "limit must be between" in detail
        assert "0" in detail

    def test_limit_above_maximum_returns_400(self):
        client = TestClient(create_app())

        response = client.get("/documents?limit=201")

        assert response.status_code == 400
        assert "limit must be between" in response.json()["detail"]

    def test_default_limit_is_50(self):
        """50 short uploads — page is exactly full but there's nothing
        further, so `next_cursor` must be null. (The store-level contract:
        `next_cursor` is null when the underlying query returned fewer
        than `limit` rows; here it returns exactly 50, so we need a fence
        to confirm there isn't a phantom page beyond.)"""
        client = TestClient(create_app())
        for i in range(50):
            _upload(client, f"body-{i}".encode(), filename=f"f-{i}.txt")

        page = client.get("/documents").json()

        assert len(page["items"]) == 50
        # Page is full, so a follow-up call with the cursor MUST produce an
        # empty page; this is the "no more rows" signal under the contract.
        assert page["next_cursor"] is not None
        follow_up = client.get(f"/documents?cursor={page['next_cursor']}").json()
        assert follow_up == {"items": [], "next_cursor": None}

    def test_response_documents_carry_scopes_field(self):
        """#258 — every Document on the response carries its active
        scope links so the frontend can render its scope chip without
        a follow-up ``list_scopes_for_document`` call. The default
        upload path lands a single ``personal:<user.id>`` link."""
        client = TestClient(create_app())
        upload = _upload(client, b"scoped-payload")

        page = client.get("/documents").json()

        assert len(page["items"]) == 1
        item = page["items"][0]
        assert item["id"] == upload["document_id"]
        # Always present (Pydantic default + serialisation-required
        # config), and the upload path seeds at least one link so the
        # list is never empty here.
        assert "scopes" in item
        assert isinstance(item["scopes"], list)
        kinds = {scope["kind"] for scope in item["scopes"]}
        assert kinds == {"personal"}


# --------------- Stable ordering: same-second uploads --------------- #


def _make_document_at(created_at: datetime, doc_id: str) -> Document:
    """Build a `Document` whose `created_at` we can pin for the test —
    the schema's default factory uses `datetime.now()`, which is the
    behaviour we're trying to bypass."""
    version = DocumentVersion(
        id=f"ver-{doc_id}",
        document_id=doc_id,
        version_number=1,
        filename="x.txt",
        content_type="text/plain",
        file_size=1,
        sha256="0" * 64,
        storage_uri=f"memory://{doc_id}/x.txt",
        status="STORED",
        created_at=created_at,
    )
    return Document(
        id=doc_id,
        original_filename="x.txt",
        latest_version_id=version.id,
        created_at=created_at,
        versions=[version],
    )


class TestStableOrderingSameSecond:
    """Two documents created at the exact same `created_at` must sort by
    `id` deterministically and pagination must not skip or duplicate
    either one."""

    def _seed(self, store, *doc_ids: str) -> datetime:
        same_moment = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
        for doc_id in doc_ids:
            doc = _make_document_at(same_moment, doc_id)
            store.save_document_with_version(doc, doc.versions[0])
        return same_moment

    def test_in_memory_store_sorts_by_id_when_created_at_ties(self):
        store = InMemoryCatalogStore()
        # Insert in reverse-id order so we'd notice if the sort were stable
        # on insertion order rather than on `id`.
        self._seed(store, "doc-b", "doc-a")

        page_one = store.list_documents(limit=1)
        assert [d.id for d in page_one] == ["doc-a"]

        cursor = _encode_cursor((page_one[0].created_at, page_one[0].id))
        page_two = store.list_documents(limit=1, cursor=cursor)
        assert [d.id for d in page_two] == ["doc-b"]

        cursor = _encode_cursor((page_two[0].created_at, page_two[0].id))
        page_three = store.list_documents(limit=1, cursor=cursor)
        assert page_three == []

    def test_sqlite_store_sorts_by_id_when_created_at_ties(self, tmp_path):
        store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")
        self._seed(store, "doc-b", "doc-a")

        page_one = store.list_documents(limit=1)
        assert [d.id for d in page_one] == ["doc-a"]

        cursor = _encode_cursor((page_one[0].created_at, page_one[0].id))
        page_two = store.list_documents(limit=1, cursor=cursor)
        assert [d.id for d in page_two] == ["doc-b"]

        cursor = _encode_cursor((page_two[0].created_at, page_two[0].id))
        page_three = store.list_documents(limit=1, cursor=cursor)
        assert page_three == []

    def test_sqlite_pagination_does_not_revisit_or_skip(self, tmp_path):
        """End-to-end on SQLite: three docs spanning a same-second pair
        plus one earlier doc, walked one at a time."""
        store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")
        earlier = datetime(2026, 4, 29, 11, 59, 59, tzinfo=UTC)
        same = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
        # Insert out of natural order.
        for created_at, doc_id in [
            (same, "doc-b"),
            (earlier, "doc-early"),
            (same, "doc-a"),
        ]:
            doc = _make_document_at(created_at, doc_id)
            store.save_document_with_version(doc, doc.versions[0])

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(3):
            page = store.list_documents(limit=1, cursor=cursor)
            assert len(page) == 1
            seen.append(page[0].id)
            cursor = _encode_cursor((page[0].created_at, page[0].id))

        # Earlier first, then same-second pair sorted by id.
        assert seen == ["doc-early", "doc-a", "doc-b"]
        # No fourth row.
        assert store.list_documents(limit=1, cursor=cursor) == []


# --- Catalog-store-level: cursor + limit slicing on the in-memory store --- #


class TestInMemoryStoreSlicing:
    """Direct tests of `InMemoryCatalogStore.list_documents` with cursor
    and limit. The HTTP route exercises the SQLite store only by way of
    the integration tests, so these guard the in-memory branch."""

    def _seed(self, count: int) -> InMemoryCatalogStore:
        store = InMemoryCatalogStore()
        base = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
        for i in range(count):
            # Make `created_at` strictly increasing so the tie-breaker
            # path isn't exercised here (covered by `TestStableOrdering...`).
            doc = _make_document_at(base + timedelta(seconds=i), f"doc-{i:02d}")
            store.save_document_with_version(doc, doc.versions[0])
        return store

    def test_cursor_after_last_returns_empty(self):
        store = self._seed(2)
        last = store.list_documents()[-1]
        cursor = _encode_cursor((last.created_at, last.id))

        assert store.list_documents(cursor=cursor, limit=10) == []

    def test_no_limit_returns_everything_after_cursor(self):
        store = self._seed(3)
        first = store.list_documents()[0]
        cursor = _encode_cursor((first.created_at, first.id))

        result = store.list_documents(cursor=cursor)

        assert [d.id for d in result] == ["doc-01", "doc-02"]


# ----------------------- SQLite isolation tests ----------------------- #


class TestSQLiteStoreSlicing:
    def _seed(self, store: SQLiteCatalogStore, count: int) -> None:
        base = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
        for i in range(count):
            doc = _make_document_at(base + timedelta(seconds=i), f"doc-{i:02d}")
            store.save_document_with_version(doc, doc.versions[0])

    def test_limit_caps_returned_rows(self, tmp_path):
        store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")
        self._seed(store, 5)

        result = store.list_documents(limit=2)

        assert [d.id for d in result] == ["doc-00", "doc-01"]

    def test_no_cursor_no_limit_returns_all(self, tmp_path):
        store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")
        self._seed(store, 3)

        result = store.list_documents()

        assert [d.id for d in result] == ["doc-00", "doc-01", "doc-02"]

    def test_cursor_walks_forward_in_sqlite(self, tmp_path):
        store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")
        self._seed(store, 4)
        first_two = store.list_documents(limit=2)
        cursor = _encode_cursor((first_two[-1].created_at, first_two[-1].id))

        result = store.list_documents(cursor=cursor, limit=2)

        assert [d.id for d in result] == ["doc-02", "doc-03"]


# --------------------- DocumentService page wrapper --------------------- #


class TestListDocumentsPageWrapper:
    """`DocumentService.list_documents_page` returns `(items, next_cursor)`.
    The route layer depends on this two-tuple shape."""

    def test_short_page_yields_null_cursor(self):
        from app.services.document_service import DocumentService
        from app.services.storage_service import InMemoryStorageService

        service = DocumentService(storage=InMemoryStorageService())
        service.upload("a.txt", "text/plain", b"a")

        items, next_cursor = service.list_documents_page(limit=5)

        assert len(items) == 1
        assert next_cursor is None

    def test_full_page_yields_resumable_cursor(self):
        from app.services.document_service import DocumentService
        from app.services.storage_service import InMemoryStorageService

        service = DocumentService(storage=InMemoryStorageService())
        service.upload("a.txt", "text/plain", b"a")
        service.upload("b.txt", "text/plain", b"b")

        items, next_cursor = service.list_documents_page(limit=1)

        assert len(items) == 1
        assert next_cursor is not None
        # Resuming from that cursor returns the second row.
        more, _ = service.list_documents_page(limit=1, cursor=next_cursor)
        assert len(more) == 1
        assert more[0].id != items[0].id
