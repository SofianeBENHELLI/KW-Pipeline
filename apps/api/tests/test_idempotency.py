"""Tests for Idempotency-Key header support on the three POST endpoints.

Covers:
- /documents/upload: same key + same bytes → replay; same key + different bytes → 422;
  no key → normal (no caching).
- /documents/{id}/versions/{vid}/extract: same tests.
- /documents/{id}/versions/{vid}/semantic: same tests.
- Direct unit tests for IdempotencyStore: get/put/expiry.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.services.idempotency_store import (
    InMemoryIdempotencyStore,
    SQLiteIdempotencyStore,
    hash_bytes,
    hash_json_body,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(services=None):
    if services is None:
        services = build_services()
    return TestClient(create_app(services=services))


def _upload(client, content: bytes = b"Policy title\nReview required", key: str | None = None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(
        "/documents/upload",
        files={"file": ("policy.txt", content, "text/plain")},
        headers=headers,
    )


def _do_full_pipeline(
    client,
    content: bytes = b"Policy title\nReview required",
    key: str | None = None,
):
    """Upload → extract → semantic, returning all three responses."""
    upload_resp = _upload(client, content=content, key=key)
    assert upload_resp.status_code == 200
    version = upload_resp.json()

    extract_headers = {"Idempotency-Key": key} if key else {}
    extract_resp = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract",
        headers=extract_headers,
    )

    semantic_headers = {"Idempotency-Key": key} if key else {}
    semantic_resp = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/semantic",
        headers=semantic_headers,
    )
    return upload_resp, extract_resp, semantic_resp, version


# ---------------------------------------------------------------------------
# Unit tests: InMemoryIdempotencyStore
# ---------------------------------------------------------------------------


class TestInMemoryIdempotencyStore:
    def test_get_returns_none_when_empty(self):
        store = InMemoryIdempotencyStore()
        assert store.get("key-1", "/route") is None

    def test_put_then_get_returns_stored_response(self):
        store = InMemoryIdempotencyStore()
        store.put("key-1", "/route", "hash-abc", 200, '{"id": "x"}')

        stored = store.get("key-1", "/route")
        assert stored is not None
        assert stored.key == "key-1"
        assert stored.route == "/route"
        assert stored.request_hash == "hash-abc"
        assert stored.response_status == 200
        assert stored.response_json == '{"id": "x"}'

    def test_same_key_different_routes_are_independent(self):
        store = InMemoryIdempotencyStore()
        store.put("key-1", "/route-a", "ha", 200, '"a"')
        store.put("key-1", "/route-b", "hb", 200, '"b"')

        assert store.get("key-1", "/route-a").response_json == '"a"'
        assert store.get("key-1", "/route-b").response_json == '"b"'
        assert store.get("key-1", "/route-c") is None

    def test_overwrite_updates_existing_entry(self):
        store = InMemoryIdempotencyStore()
        store.put("key-1", "/route", "h1", 200, '"first"')
        store.put("key-1", "/route", "h2", 201, '"second"')

        stored = store.get("key-1", "/route")
        assert stored.response_json == '"second"'
        assert stored.request_hash == "h2"

    def test_purge_expired_removes_old_entries(self):
        store = InMemoryIdempotencyStore()
        store.put("key-old", "/r", "h", 200, '{}')
        store.put("key-new", "/r", "h", 200, '{}')

        # Back-date the old entry by injecting directly.
        old_stored = store._entries[("key-old", "/r")]
        from dataclasses import replace as dc_replace

        store._entries[("key-old", "/r")] = dc_replace(
            old_stored,
            created_at=datetime.now(tz=UTC) - timedelta(hours=48),
        )

        removed = store.purge_expired(ttl_hours=24)
        assert removed == 1
        assert store.get("key-old", "/r") is None
        assert store.get("key-new", "/r") is not None

    def test_purge_expired_zero_when_nothing_old(self):
        store = InMemoryIdempotencyStore()
        store.put("key-1", "/r", "h", 200, '{}')

        removed = store.purge_expired(ttl_hours=24)
        assert removed == 0


# ---------------------------------------------------------------------------
# Unit tests: SQLiteIdempotencyStore
# ---------------------------------------------------------------------------


class TestSQLiteIdempotencyStore:
    def test_get_returns_none_when_empty(self, tmp_path):
        store = SQLiteIdempotencyStore(tmp_path / "idem.sqlite3")
        assert store.get("key-1", "/route") is None

    def test_put_then_get_round_trips(self, tmp_path):
        store = SQLiteIdempotencyStore(tmp_path / "idem.sqlite3")
        store.put("key-1", "/route", "hash-abc", 200, '{"id": "x"}')

        stored = store.get("key-1", "/route")
        assert stored is not None
        assert stored.request_hash == "hash-abc"
        assert stored.response_json == '{"id": "x"}'

    def test_persists_across_instances(self, tmp_path):
        db = tmp_path / "idem.sqlite3"
        SQLiteIdempotencyStore(db).put("key-1", "/r", "h", 200, '"v"')

        stored = SQLiteIdempotencyStore(db).get("key-1", "/r")
        assert stored is not None
        assert stored.response_json == '"v"'

    def test_purge_expired_removes_old_entries(self, tmp_path):
        import sqlite3

        db = tmp_path / "idem.sqlite3"
        store = SQLiteIdempotencyStore(db)
        store.put("key-old", "/r", "h", 200, '{}')
        store.put("key-new", "/r", "h", 200, '{}')

        # Back-date the old row directly in SQLite.
        cutoff_ts = (datetime.now(tz=UTC) - timedelta(hours=48)).isoformat()
        conn = sqlite3.connect(db)
        conn.execute(
            "UPDATE idempotency_keys SET created_at = ? WHERE key = ?",
            (cutoff_ts, "key-old"),
        )
        conn.commit()
        conn.close()

        removed = store.purge_expired(ttl_hours=24)
        assert removed == 1
        assert store.get("key-old", "/r") is None
        assert store.get("key-new", "/r") is not None


# ---------------------------------------------------------------------------
# Unit tests: hash helpers
# ---------------------------------------------------------------------------


class TestHashHelpers:
    def test_hash_bytes_is_deterministic(self):
        assert hash_bytes(b"hello") == hash_bytes(b"hello")

    def test_hash_bytes_differs_for_different_input(self):
        assert hash_bytes(b"hello") != hash_bytes(b"world")

    def test_hash_json_body_is_order_independent(self):
        h1 = hash_json_body({"b": 1, "a": 2})
        h2 = hash_json_body({"a": 2, "b": 1})
        assert h1 == h2

    def test_hash_json_body_includes_path_params(self):
        h_with = hash_json_body(None, path_params={"doc": "x", "ver": "y"})
        h_without = hash_json_body(None)
        assert h_with != h_without

    def test_hash_json_body_different_path_params(self):
        h1 = hash_json_body(None, path_params={"doc": "a", "ver": "1"})
        h2 = hash_json_body(None, path_params={"doc": "b", "ver": "1"})
        assert h1 != h2


# ---------------------------------------------------------------------------
# Integration tests: /documents/upload
# ---------------------------------------------------------------------------


class TestIdempotencyOnUpload:
    def test_same_key_same_bytes_replays_first_response(self):
        services = build_services()
        client = _client(services)

        first = _upload(client, content=b"same content", key="upload-key-1")
        second = _upload(client, content=b"same content", key="upload-key-1")

        assert first.status_code == 200
        assert second.status_code == 200
        # Must return the same document version id.
        assert second.json()["id"] == first.json()["id"]

    def test_same_key_same_bytes_does_not_create_extra_catalog_row(self):
        services = build_services()
        client = _client(services)

        _upload(client, content=b"unique content abc", key="upload-key-2")
        _upload(client, content=b"unique content abc", key="upload-key-2")

        catalog = client.get("/documents").json()
        # Exactly one document in the catalog, not two.
        assert len(catalog["items"]) == 1

    def test_same_key_different_bytes_returns_422(self):
        services = build_services()
        client = _client(services)

        first = _upload(client, content=b"first bytes", key="upload-key-3")
        assert first.status_code == 200

        second = _upload(client, content=b"different bytes", key="upload-key-3")
        assert second.status_code == 422
        assert "Idempotency-Key reused with different request body" in second.json()["detail"]

    def test_no_key_behaves_as_normal_no_caching(self):
        services = build_services()
        client = _client(services)

        first = _upload(client, content=b"no key content")
        second = _upload(client, content=b"no key content")

        assert first.status_code == 200
        assert second.status_code == 200
        # Without a key the second call is treated as a new upload; both
        # succeed but the second one is flagged as DUPLICATE_DETECTED since
        # the document service deduplicates by hash.
        assert second.json()["status"] == "DUPLICATE_DETECTED"

    def test_response_is_byte_identical_on_replay(self):
        services = build_services()
        client = _client(services)

        first = _upload(client, content=b"idempotent bytes", key="upload-key-4")
        second = _upload(client, content=b"idempotent bytes", key="upload-key-4")

        assert first.json() == second.json()


# ---------------------------------------------------------------------------
# Integration tests: /extract
# ---------------------------------------------------------------------------


class TestIdempotencyOnExtract:
    def _setup(self, key: str | None = None):
        services = build_services()
        client = _client(services)
        upload = _upload(client, content=b"Extractable content\nLine 2").json()
        return client, upload

    def test_same_key_same_params_replays_first_response(self):
        services = build_services()
        client = _client(services)
        upload = _upload(client, content=b"Extract idempotent test").json()
        doc_id, ver_id = upload["document_id"], upload["id"]

        first = client.post(
            f"/documents/{doc_id}/versions/{ver_id}/extract",
            headers={"Idempotency-Key": "extract-key-1"},
        )
        second = client.post(
            f"/documents/{doc_id}/versions/{ver_id}/extract",
            headers={"Idempotency-Key": "extract-key-1"},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]

    def test_same_key_different_params_returns_422(self):
        """Reusing the same key for a different document/version pair must 422."""
        services = build_services()
        client = _client(services)

        upload_a = _upload(client, content=b"Doc A content").json()
        upload_b = _upload(client, content=b"Doc B content").json()

        first = client.post(
            f"/documents/{upload_a['document_id']}/versions/{upload_a['id']}/extract",
            headers={"Idempotency-Key": "extract-key-2"},
        )
        assert first.status_code == 200

        second = client.post(
            f"/documents/{upload_b['document_id']}/versions/{upload_b['id']}/extract",
            headers={"Idempotency-Key": "extract-key-2"},
        )
        assert second.status_code == 422
        assert "Idempotency-Key reused with different request body" in second.json()["detail"]

    def test_no_key_behaves_as_normal(self):
        services = build_services()
        client = _client(services)
        upload = _upload(client, content=b"Normal extract no key").json()
        doc_id, ver_id = upload["document_id"], upload["id"]

        first = client.post(f"/documents/{doc_id}/versions/{ver_id}/extract")
        # Without a key, the second call goes through the lifecycle FSM and
        # rejects re-extraction with 409 Conflict, exactly as before this PR.
        second = client.post(f"/documents/{doc_id}/versions/{ver_id}/extract")

        assert first.status_code == 200
        assert second.status_code == 409


# ---------------------------------------------------------------------------
# Integration tests: /semantic
# ---------------------------------------------------------------------------


class TestIdempotencyOnSemantic:
    def test_same_key_same_params_replays_first_response(self):
        services = build_services()
        client = _client(services)
        upload = _upload(client, content=b"Semantic idempotent test").json()
        doc_id, ver_id = upload["document_id"], upload["id"]
        # Must extract first.
        client.post(f"/documents/{doc_id}/versions/{ver_id}/extract")

        first = client.post(
            f"/documents/{doc_id}/versions/{ver_id}/semantic",
            headers={"Idempotency-Key": "semantic-key-1"},
        )
        second = client.post(
            f"/documents/{doc_id}/versions/{ver_id}/semantic",
            headers={"Idempotency-Key": "semantic-key-1"},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]

    def test_same_key_different_params_returns_422(self):
        services = build_services()
        client = _client(services)

        upload_a = _upload(client, content=b"Semantic Doc A").json()
        upload_b = _upload(client, content=b"Semantic Doc B").json()

        client.post(
            f"/documents/{upload_a['document_id']}/versions/{upload_a['id']}/extract"
        )
        client.post(
            f"/documents/{upload_b['document_id']}/versions/{upload_b['id']}/extract"
        )

        first = client.post(
            f"/documents/{upload_a['document_id']}/versions/{upload_a['id']}/semantic",
            headers={"Idempotency-Key": "semantic-key-2"},
        )
        assert first.status_code == 200

        second = client.post(
            f"/documents/{upload_b['document_id']}/versions/{upload_b['id']}/semantic",
            headers={"Idempotency-Key": "semantic-key-2"},
        )
        assert second.status_code == 422
        assert "Idempotency-Key reused with different request body" in second.json()["detail"]

    def test_no_key_behaves_as_normal(self):
        services = build_services()
        client = _client(services)
        upload = _upload(client, content=b"Semantic no key test").json()
        doc_id, ver_id = upload["document_id"], upload["id"]
        client.post(f"/documents/{doc_id}/versions/{ver_id}/extract")

        first = client.post(f"/documents/{doc_id}/versions/{ver_id}/semantic")
        second = client.post(f"/documents/{doc_id}/versions/{ver_id}/semantic")

        assert first.status_code == 200
        assert second.status_code == 200
        # The semantic service is idempotent: returns the same cached document.
        assert second.json()["id"] == first.json()["id"]

    def test_response_is_byte_identical_on_replay(self):
        services = build_services()
        client = _client(services)
        upload = _upload(client, content=b"Semantic byte-identical test").json()
        doc_id, ver_id = upload["document_id"], upload["id"]
        client.post(f"/documents/{doc_id}/versions/{ver_id}/extract")

        first = client.post(
            f"/documents/{doc_id}/versions/{ver_id}/semantic",
            headers={"Idempotency-Key": "semantic-key-3"},
        )
        second = client.post(
            f"/documents/{doc_id}/versions/{ver_id}/semantic",
            headers={"Idempotency-Key": "semantic-key-3"},
        )

        assert first.json() == second.json()


# ---------------------------------------------------------------------------
# Integration: keys are scoped per route, not globally
# ---------------------------------------------------------------------------


class TestKeyIsScopedPerRoute:
    def test_same_key_on_different_routes_is_independent(self):
        """Using the same Idempotency-Key on /upload and /extract should not
        cross-contaminate the cache entries."""
        services = build_services()
        client = _client(services)

        shared_key = "cross-route-key"

        upload = _upload(client, content=b"Cross route test", key=shared_key)
        assert upload.status_code == 200
        version = upload.json()

        # Same key on /extract must NOT return the upload response.
        extract = client.post(
            f"/documents/{version['document_id']}/versions/{version['id']}/extract",
            headers={"Idempotency-Key": shared_key},
        )
        assert extract.status_code == 200
        # The extract response has a "parser_name" field that the upload response doesn't.
        assert "parser_name" in extract.json()
