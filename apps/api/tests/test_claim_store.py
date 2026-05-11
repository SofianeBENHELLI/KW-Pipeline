"""Tests for the atomic Claim/Fact data model + store + read API
(#368, ADR-031).

Covers:

* Migration ``0012_claims`` creates the table + indexes.
* :class:`InMemoryClaimStore` and :class:`SQLiteClaimStore` implement
  the same Protocol with parity behaviour for save / list / delete
  (parametrized fixture).
* Wire schema validation: ``object_value`` XOR ``object_entity_id``
  is required; both set or neither set raises ``ValidationError``.
* ``GET /knowledge/claims`` returns the list shape and 422 when the
  required ``subject_entity_id`` query param is missing.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.dependencies import build_persistent_services
from app.main import create_app
from app.schemas.claim import CLAIM_SCHEMA_VERSION, Claim, ClaimsListResponse
from app.services.claim_store import (
    DEFAULT_CLAIMS_PAGE_LIMIT,
    InMemoryClaimStore,
    SQLiteClaimStore,
)
from app.services.migrations import _run_migrations

# Set of version ids the test fixtures use. Seeded into the
# ``document_versions`` parent table so the SQLite store's FK on
# ``version_id`` is satisfied without dragging the full catalog
# wiring into a unit-level test.
_SEEDED_VERSION_IDS = ("ver-1", "ver-2")


def _seed_sqlite_schema(db_path: Path) -> None:
    """Run migrations + seed the parent rows the FK depends on.

    Mirrors the pattern in
    ``tests/services/test_validation_metadata_store.py`` —
    ``claims.version_id`` is an FK into ``document_versions(id)``,
    so a unit test wanting to insert a claim needs the parent row
    in place. We seed the smallest set the fixtures touch.
    """
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        _run_migrations(conn)
        conn.execute(
            "INSERT INTO documents (id, original_filename, latest_version_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("doc-1", "fixture.txt", "ver-1", "2026-05-05T12:00:00+00:00"),
        )
        for vid in _SEEDED_VERSION_IDS:
            conn.execute(
                "INSERT INTO document_versions ("
                "  id, document_id, version_number, filename, content_type, file_size,"
                "  sha256, storage_uri, status, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    vid,
                    "doc-1",
                    1,
                    "fixture.txt",
                    "text/plain",
                    10,
                    "0" * 64,
                    "memory://0",
                    "STORED",
                    "2026-05-05T12:00:00+00:00",
                ),
            )
    finally:
        conn.close()


def _placeholder_extracted_at() -> datetime:
    """Pre-store ``extracted_at`` placeholder.

    The store stamps every save with ``datetime.now(UTC)``, so the
    value the test passes here is overwritten — but the field is
    required on :class:`Claim` so we still need a real datetime to
    pass schema validation.
    """
    return datetime(2026, 5, 11, tzinfo=UTC)


def _make_claim(
    *,
    claim_id: str = "claim-1",
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    subject_entity_id: str = "entity-aaa111",
    predicate: str = "is_a",
    object_value: str | None = "policy",
    object_entity_id: str | None = None,
    confidence: float = 0.85,
    provenance_chunk_ids: list[str] | None = None,
) -> Claim:
    return Claim(
        id=claim_id,
        document_id=document_id,
        version_id=version_id,
        subject_entity_id=subject_entity_id,
        predicate=predicate,
        object_value=object_value,
        object_entity_id=object_entity_id,
        confidence=confidence,
        extracted_at=_placeholder_extracted_at(),
        provenance_chunk_ids=provenance_chunk_ids or ["chunk-1"],
    )


# ─── Migration ─────────────────────────────────────────────────────


def test_migration_0012_creates_claims_table(tmp_path: Path) -> None:
    """Booting the persistent services applies the migration."""
    build_persistent_services(tmp_path)

    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = 'claims'")
        names = {row[0] for row in cursor.fetchall()}
    finally:
        db.close()
    assert "claims" in names


def test_migration_0012_creates_claims_indexes(tmp_path: Path) -> None:
    """The three read-pattern indexes land alongside the table."""
    build_persistent_services(tmp_path)

    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name = 'claims'"
        ).fetchall()
    finally:
        db.close()
    names = {row[0] for row in rows}
    assert "idx_claims_subject_entity_id" in names
    assert "idx_claims_version_id" in names
    assert "idx_claims_predicate" in names


def test_migration_0012_records_in_schema_migrations(tmp_path: Path) -> None:
    """The migration id is stamped in ``schema_migrations`` so a re-run
    is a no-op and the bootstrap path doesn't re-apply it."""
    build_persistent_services(tmp_path)
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        applied = {row[0] for row in db.execute("SELECT id FROM schema_migrations").fetchall()}
    finally:
        db.close()
    assert "0012_claims" in applied


# ─── Schema validation ────────────────────────────────────────────


def test_claim_requires_one_of_object_value_or_object_entity_id() -> None:
    """Neither ``object_value`` nor ``object_entity_id`` set → reject."""
    with pytest.raises(ValidationError, match="object is missing"):
        Claim(
            id="claim-x",
            document_id="doc-1",
            version_id="ver-1",
            subject_entity_id="entity-aaa111",
            predicate="is_a",
            object_value=None,
            object_entity_id=None,
            confidence=0.5,
            extracted_at=_placeholder_extracted_at(),
            provenance_chunk_ids=["chunk-1"],
        )


def test_claim_rejects_both_object_value_and_object_entity_id() -> None:
    """Both ``object_value`` and ``object_entity_id`` set → reject."""
    with pytest.raises(ValidationError, match="object is ambiguous"):
        Claim(
            id="claim-x",
            document_id="doc-1",
            version_id="ver-1",
            subject_entity_id="entity-aaa111",
            predicate="is_a",
            object_value="policy",
            object_entity_id="entity-bbb222",
            confidence=0.5,
            extracted_at=_placeholder_extracted_at(),
            provenance_chunk_ids=["chunk-1"],
        )


def test_claim_accepts_only_object_value() -> None:
    """Literal-object claim — ``object_value`` set, entity-ref unset."""
    claim = _make_claim(object_value="2025", object_entity_id=None)
    assert claim.object_value == "2025"
    assert claim.object_entity_id is None
    assert claim.schema_version == CLAIM_SCHEMA_VERSION


def test_claim_accepts_only_object_entity_id() -> None:
    """Entity-ref claim — ``object_entity_id`` set, value unset."""
    claim = _make_claim(object_value=None, object_entity_id="entity-bbb222")
    assert claim.object_value is None
    assert claim.object_entity_id == "entity-bbb222"


def test_claim_requires_at_least_one_provenance_chunk_id() -> None:
    """Empty provenance list → reject (claim must be verifiable)."""
    with pytest.raises(ValidationError):
        Claim(
            id="claim-x",
            document_id="doc-1",
            version_id="ver-1",
            subject_entity_id="entity-aaa111",
            predicate="is_a",
            object_value="policy",
            object_entity_id=None,
            confidence=0.5,
            extracted_at=_placeholder_extracted_at(),
            provenance_chunk_ids=[],
        )


def test_claim_confidence_must_be_in_unit_interval() -> None:
    with pytest.raises(ValidationError):
        _make_claim(confidence=1.5)
    with pytest.raises(ValidationError):
        _make_claim(confidence=-0.1)


# ─── Store contract parity ────────────────────────────────────────


@pytest.fixture(params=["inmemory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    if request.param == "inmemory":
        return InMemoryClaimStore()
    # SQLite store needs the schema in place AND the FK parents
    # seeded so the version_id FK is satisfied.
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    return SQLiteClaimStore(db_path)


def test_store_starts_empty(store: Any) -> None:
    items, next_cursor = store.list_for_subject("entity-anything")
    assert items == []
    assert next_cursor is None


def test_store_save_then_list_round_trips_claim(store: Any) -> None:
    store.save_claims([_make_claim()])
    items, next_cursor = store.list_for_subject("entity-aaa111")
    assert len(items) == 1
    got = items[0]
    assert got.id == "claim-1"
    assert got.subject_entity_id == "entity-aaa111"
    assert got.predicate == "is_a"
    assert got.object_value == "policy"
    assert got.object_entity_id is None
    assert got.confidence == pytest.approx(0.85)
    assert got.schema_version == CLAIM_SCHEMA_VERSION
    # The store stamps ``extracted_at`` with now(UTC) — the placeholder
    # the test passed in is overwritten.
    assert got.extracted_at.tzinfo is not None
    assert got.provenance_chunk_ids == ["chunk-1"]
    assert next_cursor is None


def test_store_save_handles_empty_batch(store: Any) -> None:
    """Empty input is a no-op — extractor passes that emit zero
    claims must not raise."""
    store.save_claims([])
    items, _ = store.list_for_subject("entity-aaa111")
    assert items == []


def test_store_round_trips_object_entity_id_claim(store: Any) -> None:
    """Entity-ref claims survive the round trip too — ``object_value``
    stays None and the entity ref comes back populated."""
    store.save_claims(
        [
            _make_claim(
                claim_id="claim-rel",
                object_value=None,
                object_entity_id="entity-bbb222",
            )
        ]
    )
    items, _ = store.list_for_subject("entity-aaa111")
    assert len(items) == 1
    assert items[0].object_value is None
    assert items[0].object_entity_id == "entity-bbb222"


def test_store_filters_by_subject(store: Any) -> None:
    store.save_claims(
        [
            _make_claim(claim_id="c-1", subject_entity_id="entity-aaa111"),
            _make_claim(claim_id="c-2", subject_entity_id="entity-bbb222"),
        ]
    )
    items, _ = store.list_for_subject("entity-aaa111")
    assert {c.id for c in items} == {"c-1"}
    items, _ = store.list_for_subject("entity-bbb222")
    assert {c.id for c in items} == {"c-2"}


def test_store_round_trips_long_provenance_list(store: Any) -> None:
    store.save_claims([_make_claim(provenance_chunk_ids=[f"chunk-{i}" for i in range(10)])])
    items, _ = store.list_for_subject("entity-aaa111")
    assert items[0].provenance_chunk_ids == [f"chunk-{i}" for i in range(10)]


def test_store_pagination_walks_all_pages(store: Any) -> None:
    """Pagination returns each row exactly once across consecutive
    pages, and ``next_cursor`` is ``None`` on the final page."""
    # Save 5 claims in two batches so ``extracted_at`` differs across
    # the batches and the in-memory + SQLite stores agree on order.
    store.save_claims([_make_claim(claim_id=f"c-{i}") for i in range(3)])
    store.save_claims([_make_claim(claim_id=f"c-{i}") for i in range(3, 5)])

    seen_ids: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        items, cursor = store.list_for_subject("entity-aaa111", cursor=cursor, limit=2)
        seen_ids.extend(c.id for c in items)
        pages += 1
        if cursor is None:
            break
        # Safety bound — a bug that produces an infinite cursor loop
        # blows up here rather than hanging.
        assert pages <= 10
    assert sorted(seen_ids) == sorted([f"c-{i}" for i in range(5)])


def test_store_pagination_invalid_cursor_raises(store: Any) -> None:
    from app.services.claim_store import InvalidCursor

    with pytest.raises(InvalidCursor):
        store.list_for_subject("entity-aaa111", cursor="not-a-real-cursor")


def test_store_delete_for_version_removes_only_that_version(store: Any) -> None:
    store.save_claims(
        [
            _make_claim(claim_id="c-v1-1", version_id="ver-1"),
            _make_claim(claim_id="c-v1-2", version_id="ver-1"),
            _make_claim(claim_id="c-v2-1", version_id="ver-2"),
        ]
    )
    removed = store.delete_for_version("ver-1")
    assert removed == 2
    items, _ = store.list_for_subject("entity-aaa111")
    assert {c.id for c in items} == {"c-v2-1"}


def test_store_delete_for_version_idempotent(store: Any) -> None:
    """Calling delete twice is safe — second call removes 0 rows."""
    store.save_claims([_make_claim(version_id="ver-1")])
    assert store.delete_for_version("ver-1") == 1
    assert store.delete_for_version("ver-1") == 0


def test_store_default_page_limit_constant_is_sane() -> None:
    """The default page limit is in (0, 200]; clients depending on the
    constant get a stable ceiling."""
    assert 0 < DEFAULT_CLAIMS_PAGE_LIMIT <= 200


# ─── FK contract: cascade on parent delete + reject orphan inserts ─


def test_sqlite_store_rejects_claim_with_unknown_version_id(tmp_path: Path) -> None:
    """The migration declares ``FOREIGN KEY (version_id) REFERENCES
    document_versions(id) ON DELETE CASCADE`` and ``_connect`` enables
    ``PRAGMA foreign_keys = ON`` — inserting a claim against a
    version_id that doesn't exist must raise an integrity error
    rather than silently writing an orphan row."""
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    store = SQLiteClaimStore(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.save_claims([_make_claim(version_id="ver-does-not-exist")])


def test_sqlite_store_cascade_deletes_claims_when_parent_version_deleted(
    tmp_path: Path,
) -> None:
    """Deleting a ``document_versions`` row must cascade to the
    ``claims`` table per the migration's documented contract — claims
    don't outlive their version."""
    db_path = tmp_path / "catalog.sqlite3"
    _seed_sqlite_schema(db_path)
    store = SQLiteClaimStore(db_path)
    store.save_claims(
        [
            _make_claim(claim_id="c-1", version_id="ver-1"),
            _make_claim(claim_id="c-2", version_id="ver-2"),
        ]
    )

    # Delete one parent version directly — bypasses the catalog API
    # so we exercise the FK behaviour, not the application code.
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM document_versions WHERE id = ?", ("ver-1",))
    finally:
        conn.close()

    # Claims for ver-1 vanished; ver-2 still has its claim.
    items_v1, _ = store.list_for_subject(subject_entity_id="entity-aaa111")
    surviving_versions = {c.version_id for c in items_v1}
    assert "ver-1" not in surviving_versions
    assert "ver-2" in surviving_versions


# ─── Route: GET /knowledge/claims ─────────────────────────────────


def test_route_returns_empty_list_when_no_claims_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No claims for the subject yet → 200 with empty items, schema
    version pinned. Mirrors the rest of the knowledge-layer read
    surface (no 404 for "nothing yet")."""
    monkeypatch.setenv("KW_AUTH_MODE", "dev")
    app = create_app()
    client = TestClient(app)
    response = client.get("/knowledge/claims", params={"subject_entity_id": "entity-zzz999"})
    assert response.status_code == 200, response.text
    body = response.json()
    parsed = ClaimsListResponse.model_validate(body)
    assert parsed.items == []
    assert parsed.next_cursor is None
    assert parsed.schema_version == CLAIM_SCHEMA_VERSION


def test_route_returns_seeded_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed the in-memory store via ``app.state.services`` and verify
    the route returns the same payload."""
    monkeypatch.setenv("KW_AUTH_MODE", "dev")
    app = create_app()
    services = app.state.services
    services.claim_store.save_claims(
        [
            _make_claim(claim_id="c-1", subject_entity_id="entity-aaa111"),
            _make_claim(claim_id="c-2", subject_entity_id="entity-aaa111"),
            _make_claim(claim_id="c-other", subject_entity_id="entity-bbb222"),
        ]
    )
    client = TestClient(app)
    response = client.get("/knowledge/claims", params={"subject_entity_id": "entity-aaa111"})
    assert response.status_code == 200, response.text
    parsed = ClaimsListResponse.model_validate(response.json())
    assert {c.id for c in parsed.items} == {"c-1", "c-2"}


def test_route_returns_422_when_subject_entity_id_missing() -> None:
    """The ``subject_entity_id`` query param is required — FastAPI
    returns 422 with the parameter name in the error envelope."""
    app = create_app()
    client = TestClient(app)
    response = client.get("/knowledge/claims")
    assert response.status_code == 422
    body = response.json()
    # FastAPI's standard validation error envelope contains the
    # missing field name; we check loosely so a future error-shape
    # change doesn't break this test.
    assert "subject_entity_id" in response.text
    # Also assert envelope shape didn't drift to a non-dict.
    assert isinstance(body, dict)


def test_route_400s_on_invalid_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed cursor maps to HTTP 400 (not 500)."""
    monkeypatch.setenv("KW_AUTH_MODE", "dev")
    app = create_app()
    client = TestClient(app)
    response = client.get(
        "/knowledge/claims",
        params={"subject_entity_id": "entity-aaa111", "cursor": "not-base64!!"},
    )
    assert response.status_code == 400
    body = response.json()
    # API error envelope wraps the detail under ``error.message``.
    assert "Invalid cursor" in body.get("error", {}).get("message", "")


def test_route_rejects_limit_above_max() -> None:
    """``limit > 200`` is rejected by FastAPI's Query bound (422)."""
    app = create_app()
    client = TestClient(app)
    response = client.get(
        "/knowledge/claims",
        params={"subject_entity_id": "entity-aaa111", "limit": 9001},
    )
    assert response.status_code == 422


def test_route_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    """A small ``limit`` returns a populated ``next_cursor`` that
    advances the stream when followed."""
    monkeypatch.setenv("KW_AUTH_MODE", "dev")
    app = create_app()
    services = app.state.services
    services.claim_store.save_claims([_make_claim(claim_id=f"c-{i}") for i in range(3)])
    services.claim_store.save_claims([_make_claim(claim_id=f"c-{i}") for i in range(3, 5)])
    client = TestClient(app)

    response = client.get(
        "/knowledge/claims",
        params={"subject_entity_id": "entity-aaa111", "limit": 2},
    )
    assert response.status_code == 200, response.text
    page = ClaimsListResponse.model_validate(response.json())
    assert len(page.items) == 2
    assert page.next_cursor is not None

    response = client.get(
        "/knowledge/claims",
        params={
            "subject_entity_id": "entity-aaa111",
            "limit": 10,
            "cursor": page.next_cursor,
        },
    )
    assert response.status_code == 200
    rest = ClaimsListResponse.model_validate(response.json())
    seen = {c.id for c in page.items} | {c.id for c in rest.items}
    assert seen == {f"c-{i}" for i in range(5)}
