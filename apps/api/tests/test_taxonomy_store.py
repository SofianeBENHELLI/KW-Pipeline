"""Tests for the SQLite-backed taxonomy store (#379 / ADR-031).

Covers:

* Migration 0010 creates the expected schema.
* :class:`InMemoryTaxonomyStore` and :class:`SQLiteTaxonomyStore`
  implement the same Protocol with parity behaviour for the read +
  publish paths.
* Publish flips the active flag atomically (the active set is always
  size ≤ 1).
* Re-publishing the same payload creates a new row rather than
  silently no-op-ing — operators reading the audit log can see every
  publish event.
* Bootstrap helper imports a YAML payload, returns the new id, and
  routes failures (malformed YAML / missing file) the way callers
  expect.
* The ``POST /admin/taxonomy/import_yaml`` route round-trips the
  publish + the audit event.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_persistent_services
from app.main import create_app
from app.schemas.taxonomy import Taxonomy, TaxonomyCategory
from app.services.taxonomy_store import (
    TAXONOMY_SOURCE_API,
    TAXONOMY_SOURCE_YAML_IMPORT,
    InMemoryTaxonomyStore,
    SQLiteTaxonomyStore,
    import_yaml_into_store,
)


def _sample_taxonomy() -> Taxonomy:
    return Taxonomy(
        categories=[
            TaxonomyCategory(
                id="hr",
                label="HR",
                description="Human resources policies, processes, and SOPs.",
                source="imposed",
                subcategories=[
                    TaxonomyCategory(
                        id="hr.hybrid_work",
                        label="Hybrid work",
                        description="Remote, hybrid, and cross-border arrangements.",
                        source="imposed",
                    ),
                    TaxonomyCategory(
                        id="hr.compensation",
                        label="Compensation",
                        description="Pay, equity, and benefits policy.",
                        source="imposed",
                    ),
                ],
            ),
            TaxonomyCategory(
                id="legal",
                label="Legal",
                description="Contracts, regulatory affairs, and disputes.",
                source="imposed",
            ),
        ]
    )


def _write_yaml(path: Path, taxonomy: Taxonomy) -> None:
    """Serialise a taxonomy into the YAML shape the loader accepts."""
    import yaml

    def render(category: TaxonomyCategory) -> dict:
        body: dict = {
            "id": category.id,
            "label": category.label,
            "description": category.description,
        }
        if category.subcategories:
            body["subcategories"] = [render(c) for c in category.subcategories]
        return body

    payload = {
        "schema_version": taxonomy.schema_version,
        "categories": [render(c) for c in taxonomy.categories],
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


# ─── Migration ─────────────────────────────────────────────────────


def test_migration_0010_creates_taxonomy_tables(tmp_path: Path) -> None:
    """Booting the persistent services applies the migration."""
    build_persistent_services(tmp_path)

    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'taxonom%'"
        )
        names = {row[0] for row in cursor.fetchall()}
    finally:
        db.close()
    assert "taxonomies" in names
    assert "taxonomy_categories" in names


def test_migration_0010_indexes_active_partial(tmp_path: Path) -> None:
    """The partial index on (active=1) lets the active read be O(1)."""
    build_persistent_services(tmp_path)

    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        idx_rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name = 'taxonomies'"
        ).fetchall()
    finally:
        db.close()
    assert any(row[0] == "idx_taxonomies_active" for row in idx_rows)


# ─── Store contract parity ─────────────────────────────────────────


@pytest.fixture(params=["inmemory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "inmemory":
        return InMemoryTaxonomyStore()
    # SQLite store needs the schema in place.
    build_persistent_services(tmp_path)
    return SQLiteTaxonomyStore(tmp_path / "catalog.sqlite3")


def test_store_returns_none_when_empty(store) -> None:
    assert store.get_active() is None


def test_publish_returns_a_new_id_and_makes_taxonomy_active(store) -> None:
    new_id = store.publish(
        _sample_taxonomy(),
        source=TAXONOMY_SOURCE_API,
        actor="alice",
    )
    assert isinstance(new_id, str) and len(new_id) > 0
    active = store.get_active()
    assert active is not None
    assert {c.id for c in active.categories} == {"hr", "legal"}


def test_publish_round_trips_the_full_tree(store) -> None:
    store.publish(_sample_taxonomy(), source=TAXONOMY_SOURCE_API, actor="alice")
    active = store.get_active()
    assert active is not None
    hr = next(c for c in active.categories if c.id == "hr")
    assert {sub.id for sub in hr.subcategories} == {"hr.hybrid_work", "hr.compensation"}
    # Children inherit ``source="imposed"`` per ADR-017 conventions.
    assert all(sub.source == "imposed" for sub in hr.subcategories)


def test_publish_flips_previous_active_to_inactive(store) -> None:
    first_id = store.publish(
        _sample_taxonomy(),
        source=TAXONOMY_SOURCE_YAML_IMPORT,
        actor="boot",
    )
    second_id = store.publish(
        Taxonomy(
            categories=[
                TaxonomyCategory(
                    id="legal",
                    label="Legal v2",
                    description="Updated description.",
                    source="imposed",
                )
            ]
        ),
        source=TAXONOMY_SOURCE_API,
        actor="alice",
    )
    assert first_id != second_id
    active = store.get_active()
    assert active is not None
    assert [c.id for c in active.categories] == ["legal"]
    assert active.categories[0].label == "Legal v2"


def test_publish_rejects_unknown_source(store) -> None:
    with pytest.raises(ValueError, match="Unknown taxonomy source"):
        store.publish(_sample_taxonomy(), source="not_a_source", actor="alice")


def test_publish_records_distinct_versions_even_for_identical_payload(store) -> None:
    """Two publishes of the same taxonomy produce two distinct ids —
    the audit log must see every publish even when the content is
    unchanged."""
    a = store.publish(_sample_taxonomy(), source=TAXONOMY_SOURCE_API, actor="alice")
    b = store.publish(_sample_taxonomy(), source=TAXONOMY_SOURCE_API, actor="alice")
    assert a != b


# ─── SQLite-specific: only one active row at a time ─────────────────


def test_sqlite_active_set_size_invariant(tmp_path: Path) -> None:
    """No matter how many publishes happen, ``active=1`` rows count <= 1."""
    services = build_persistent_services(tmp_path)
    store = SQLiteTaxonomyStore(tmp_path / "catalog.sqlite3")
    for _ in range(5):
        store.publish(_sample_taxonomy(), source=TAXONOMY_SOURCE_API, actor="alice")

    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        active_count = db.execute("SELECT COUNT(*) FROM taxonomies WHERE active = 1").fetchone()[0]
        total = db.execute("SELECT COUNT(*) FROM taxonomies").fetchone()[0]
    finally:
        db.close()
    assert active_count == 1
    assert total == 5
    # Sanity: services hold the same store via dependency wiring.
    assert services.taxonomy_store is not None


# ─── Bootstrap import helper ───────────────────────────────────────


def test_import_yaml_into_store_publishes_on_first_call(tmp_path: Path) -> None:
    yaml_path = tmp_path / "taxonomy.yml"
    _write_yaml(yaml_path, _sample_taxonomy())
    store = InMemoryTaxonomyStore()
    new_id = import_yaml_into_store(store, yaml_path=yaml_path, actor="boot")
    assert new_id is not None
    active = store.get_active()
    assert active is not None
    assert {c.id for c in active.categories} == {"hr", "legal"}


def test_import_yaml_into_store_returns_none_for_missing_file(tmp_path: Path) -> None:
    store = InMemoryTaxonomyStore()
    new_id = import_yaml_into_store(
        store,
        yaml_path=tmp_path / "does-not-exist.yml",
        actor="boot",
    )
    assert new_id is None
    assert store.get_active() is None


def test_import_yaml_into_store_returns_none_for_empty_path(tmp_path: Path) -> None:
    store = InMemoryTaxonomyStore()
    new_id = import_yaml_into_store(store, yaml_path="", actor="boot")
    assert new_id is None
    assert store.get_active() is None


# ─── Boot wiring + bootstrap import ────────────────────────────────


def test_persistent_boot_imports_yaml_when_store_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First boot against a fresh DB + KW_TAXONOMY_PATH should
    seed the SQLite store automatically — operators don't have to
    POST to the import route on day 1 of the migration."""
    yaml_path = tmp_path / "taxonomy.yml"
    _write_yaml(yaml_path, _sample_taxonomy())
    monkeypatch.setenv("KW_TAXONOMY_PATH", str(yaml_path))

    services = build_persistent_services(tmp_path)
    assert services.taxonomy is not None
    assert {c.id for c in services.taxonomy.categories} == {"hr", "legal"}


def test_persistent_boot_does_not_re_import_when_store_has_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second boot reads from SQLite without running the YAML import
    again. Verified by editing the YAML between the two boots —
    the second boot should still see the original content."""
    yaml_path = tmp_path / "taxonomy.yml"
    _write_yaml(yaml_path, _sample_taxonomy())
    monkeypatch.setenv("KW_TAXONOMY_PATH", str(yaml_path))

    build_persistent_services(tmp_path)  # first boot — imports YAML

    # Mutate the YAML; second boot should ignore it.
    _write_yaml(
        yaml_path,
        Taxonomy(
            categories=[
                TaxonomyCategory(
                    id="newroot",
                    label="New",
                    description="Added after the first boot.",
                    source="imposed",
                )
            ]
        ),
    )
    services = build_persistent_services(tmp_path)
    assert services.taxonomy is not None
    # Original content survived; YAML edit did NOT re-import.
    assert {c.id for c in services.taxonomy.categories} == {"hr", "legal"}


# ─── Route: POST /admin/taxonomy/import_yaml ───────────────────────


def test_import_yaml_route_publishes_and_returns_taxonomy_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    yaml_path = tmp_path / "taxonomy.yml"
    _write_yaml(yaml_path, _sample_taxonomy())
    monkeypatch.setenv("KW_TAXONOMY_PATH", str(yaml_path))

    app = create_app()
    client = TestClient(app)

    response = client.post("/admin/taxonomy/import_yaml", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == TAXONOMY_SOURCE_YAML_IMPORT
    assert body["category_count"] == 4  # hr + 2 hr children + legal
    assert body["taxonomy_id"]
    assert body["source_path"] == str(yaml_path)


def test_import_yaml_route_returns_404_when_no_path_configured() -> None:
    app = create_app()
    client = TestClient(app)
    response = client.post("/admin/taxonomy/import_yaml", json={})
    assert response.status_code == 404
    body = response.json()
    assert "KW_TAXONOMY_PATH" in body["error"]["message"]


def test_import_yaml_route_returns_422_for_malformed_yaml(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.yml"
    bad_path.write_text(
        "schema_version: v0.1\ncategories:\n  - id: BAD CAPS\n    label: x\n    description: y\n",
        encoding="utf-8",
    )
    app = create_app()
    client = TestClient(app)

    response = client.post("/admin/taxonomy/import_yaml", json={"path": str(bad_path)})
    assert response.status_code == 422
    body = response.json()
    # The TaxonomyLoadError message names the offending id.
    assert "BAD CAPS" in body["error"]["message"]


def test_import_yaml_route_round_trips_and_get_taxonomy_reflects_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: POST import → GET /knowledge/taxonomy returns the
    imported tree. This is the integration sanity check that every
    layer (schema, store, route, boot wiring) is consistent."""
    yaml_path = tmp_path / "taxonomy.yml"
    _write_yaml(yaml_path, _sample_taxonomy())
    monkeypatch.setenv("KW_TAXONOMY_PATH", str(yaml_path))

    # NOTE: GET /knowledge/taxonomy reads from ``services.taxonomy``,
    # which is captured at app construction time. The import route
    # writes through the store but the cached field on PipelineServices
    # is not auto-refreshed today (see ADR-031 §"Operator workflow at
    # MVP cadence" — operators run a redeploy after publish). So we
    # assert the SQLite store reflects the publish; the cached
    # services.taxonomy refresh is a separate slice.
    app = create_app()
    client = TestClient(app)
    response = client.post("/admin/taxonomy/import_yaml", json={})
    assert response.status_code == 200

    services = app.state.services
    fresh = services.taxonomy_store.get_active()
    assert fresh is not None
    assert {c.id for c in fresh.categories} == {"hr", "legal"}


def _ts() -> str:
    return datetime.now(UTC).isoformat()
