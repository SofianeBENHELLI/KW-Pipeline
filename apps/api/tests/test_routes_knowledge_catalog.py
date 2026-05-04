"""HTTP-level coverage for ``GET /knowledge/catalog`` (EPIC-C C.3, ADR-025).

Pins the catalog-view contract:

- ``SUPERSEDED`` versions are filtered out of the "latest_status"
  computation by default; documents whose only versions are
  ``SUPERSEDED`` are hidden entirely.
- The default visibility set is ``{VALIDATED, NEEDS_REVIEW}``;
  ``REJECTED`` / ``FAILED`` / ``SUPERSEDED`` are hidden unless the
  explicit ``status=`` filter requests them.
- Cursor-based pagination round-trips correctly under the post-store
  visibility filter.
- Scope links populate the ``scopes`` field on each item.
- Scope params are accepted but not yet enforced (D.5 lands the
  predicate; for this PR they are silent no-ops).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.schemas.scope import Scope


def _client_with_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _land_in_needs_review(services, *, document_id=None, content):
    version = services.documents.upload(
        filename="policy.txt",
        content_type="text/plain",
        content=content,
        document_id=document_id,
    )
    services.extraction_jobs.extract(document_id=version.document_id, version_id=version.id)
    services.semantic_outputs.generate(document_id=version.document_id, version_id=version.id)
    return version.document_id, version.id


def _seed_validated_family(services, *, content=b"first body of family") -> str:
    document_id, v1_id = _land_in_needs_review(services, content=content)
    services.review.handle_validation(document_id=document_id, version_id=v1_id, actor="alice")
    return document_id


def _seed_superseded_family(
    services,
    *,
    content_v1=b"first body of family",
    content_v2=b"second body of family",
) -> tuple[str, str, str]:
    """Build a v1=SUPERSEDED + v2=VALIDATED family. Returns ``(doc_id, v1, v2)``."""
    document_id, v1_id = _land_in_needs_review(services, content=content_v1)
    services.review.handle_validation(document_id=document_id, version_id=v1_id, actor="alice")
    _, v2_id = _land_in_needs_review(services, document_id=document_id, content=content_v2)
    services.review.handle_validation(document_id=document_id, version_id=v2_id, actor="alice")
    return document_id, v1_id, v2_id


def test_catalog_default_visibility_hides_superseded_only_families():
    client, services = _client_with_services()
    visible_id = _seed_validated_family(services)
    # A second family whose only version is SUPERSEDED-equivalent: we
    # supersede v1 by validating v2; v1 should not surface in the
    # catalog because the EPIC-C view filters it out.
    doc_id, _v1, _v2 = _seed_superseded_family(
        services,
        content_v1=b"second-family v1 distinct bytes",
        content_v2=b"second-family v2 distinct bytes",
    )

    response = client.get("/knowledge/catalog")

    assert response.status_code == 200
    body = response.json()
    surfaced = {row["document_id"] for row in body["items"]}
    assert visible_id in surfaced
    # The second family's latest non-superseded version is v2 VALIDATED;
    # the family itself remains visible (the spec drops only families
    # whose every version is SUPERSEDED).
    assert doc_id in surfaced
    # No SUPERSEDED row is returned by default.
    statuses = {row["latest_status"] for row in body["items"]}
    assert DocumentVersionStatus.SUPERSEDED.value not in statuses


def test_catalog_hides_family_when_only_versions_are_superseded():
    """Construct a synthetic family whose every version is in
    ``SUPERSEDED`` (a state that is not currently reachable through
    the normal pipeline but is allowed by the data model and must be
    handled defensively by the route)."""
    client, services = _client_with_services()
    doc_id, v1_id, _v2 = _seed_superseded_family(
        services,
        content_v1=b"first family v1 distinct bytes",
        content_v2=b"first family v2 distinct bytes",
    )
    # Manually flip v2 to SUPERSEDED so every version of the family
    # is SUPERSEDED. Reach into the in-memory store to bypass the
    # FSM (we're testing the route's defensive filter, not the FSM).
    family = services.documents.catalog.get_document(doc_id)
    for version in family.versions:
        version.status = DocumentVersionStatus.SUPERSEDED

    response = client.get("/knowledge/catalog")

    assert response.status_code == 200
    body = response.json()
    surfaced = {row["document_id"] for row in body["items"]}
    assert doc_id not in surfaced


def test_catalog_status_filter_overrides_default_visibility():
    """When the caller explicitly passes ``status=SUPERSEDED``, a
    family whose latest version is ``SUPERSEDED`` (audit/admin
    inspection of stale rows) surfaces despite being hidden by
    default. The default-visibility surface uses the
    "non-superseded latest" rule; the audit surface uses the raw
    highest-numbered version."""
    client, services = _client_with_services()
    doc_id, _v1, _v2 = _seed_superseded_family(services)
    # Manually flip every version to SUPERSEDED so the family's
    # latest (highest-numbered) version is SUPERSEDED — the audit
    # case the explicit filter is designed to expose.
    family = services.documents.catalog.get_document(doc_id)
    for version in family.versions:
        version.status = DocumentVersionStatus.SUPERSEDED

    response = client.get(
        "/knowledge/catalog",
        params={"status": "SUPERSEDED"},
    )

    assert response.status_code == 200
    body = response.json()
    rows = [r for r in body["items"] if r["document_id"] == doc_id]
    assert rows, "expected the SUPERSEDED-only family to surface under explicit status filter"
    assert rows[0]["latest_status"] == DocumentVersionStatus.SUPERSEDED.value


def test_catalog_status_filter_unknown_status_returns_400():
    client, _ = _client_with_services()

    response = client.get("/knowledge/catalog", params={"status": "BANANAS"})

    assert response.status_code == 400
    body = response.json()
    assert "BANANAS" in body["detail"]


def test_catalog_pagination_cursor_round_trip():
    """Three visible families, ``limit=1`` → first page returns row 1
    and a cursor; second page returns row 2; third page returns row 3
    and has no further cursor."""
    client, services = _client_with_services()
    ids = [
        _seed_validated_family(services, content=f"family {i} unique body".encode())
        for i in range(3)
    ]

    page_one = client.get("/knowledge/catalog", params={"limit": 1}).json()
    assert len(page_one["items"]) == 1
    assert page_one["next_cursor"] is not None

    page_two = client.get(
        "/knowledge/catalog", params={"limit": 1, "cursor": page_one["next_cursor"]}
    ).json()
    assert len(page_two["items"]) == 1

    page_three = client.get(
        "/knowledge/catalog", params={"limit": 1, "cursor": page_two["next_cursor"]}
    ).json()
    assert len(page_three["items"]) == 1
    assert page_three["next_cursor"] is None

    walked_ids = [
        page_one["items"][0]["document_id"],
        page_two["items"][0]["document_id"],
        page_three["items"][0]["document_id"],
    ]
    # Every seeded id is visited exactly once across the three pages.
    assert sorted(walked_ids) == sorted(ids)


def test_catalog_invalid_cursor_returns_400():
    client, _ = _client_with_services()

    response = client.get("/knowledge/catalog", params={"cursor": "not-base64!!!"})

    assert response.status_code == 400


def test_catalog_scopes_field_is_populated():
    client, services = _client_with_services()
    doc_id = _seed_validated_family(services)
    services.documents.catalog.add_scope(
        document_id=doc_id,
        scope=Scope(
            kind="swym_community",
            ref="community-42",
            added_at=datetime.now(UTC),
            added_by="alice",
        ),
    )

    response = client.get("/knowledge/catalog")

    body = response.json()
    rows = [row for row in body["items"] if row["document_id"] == doc_id]
    assert len(rows) == 1
    scopes = rows[0]["scopes"]
    assert any(s["kind"] == "swym_community" and s["ref"] == "community-42" for s in scopes)


def test_catalog_silently_accepts_scope_params_without_filtering():
    """D.5 will wire scope filtering. For this PR the params are
    accepted but ignored — the response is identical with or without
    them."""
    client, services = _client_with_services()
    doc_id = _seed_validated_family(services)

    without = client.get("/knowledge/catalog").json()
    with_scope = client.get(
        "/knowledge/catalog",
        params={"scope_kind": "swym_community", "scope_ref": "anything"},
    ).json()

    # Same set of document ids surfaced — the scope params don't filter
    # anything yet.
    surfaced_without = {row["document_id"] for row in without["items"]}
    surfaced_with = {row["document_id"] for row in with_scope["items"]}
    assert surfaced_without == surfaced_with
    assert doc_id in surfaced_with


def test_catalog_filename_query_filters_results():
    client, services = _client_with_services()
    # Two families with distinct filenames.
    v1 = services.documents.upload(
        filename="alpha.txt",
        content_type="text/plain",
        content=b"alpha body distinct",
    )
    services.extraction_jobs.extract(document_id=v1.document_id, version_id=v1.id)
    services.semantic_outputs.generate(document_id=v1.document_id, version_id=v1.id)
    services.review.handle_validation(document_id=v1.document_id, version_id=v1.id, actor="alice")
    v2 = services.documents.upload(
        filename="beta.txt",
        content_type="text/plain",
        content=b"beta body distinct",
    )
    services.extraction_jobs.extract(document_id=v2.document_id, version_id=v2.id)
    services.semantic_outputs.generate(document_id=v2.document_id, version_id=v2.id)
    services.review.handle_validation(document_id=v2.document_id, version_id=v2.id, actor="alice")

    response = client.get("/knowledge/catalog", params={"q": "alpha"})

    assert response.status_code == 200
    surfaced = {row["family_filename"] for row in response.json()["items"]}
    assert surfaced == {"alpha.txt"}


def test_catalog_item_carries_version_count_and_latest_metadata():
    client, services = _client_with_services()
    doc_id, _v1, _v2 = _seed_superseded_family(services)

    response = client.get("/knowledge/catalog")

    body = response.json()
    rows = [row for row in body["items"] if row["document_id"] == doc_id]
    assert len(rows) == 1
    row = rows[0]
    assert row["version_count"] == 2
    assert row["latest_version_number"] == 2
    assert row["latest_status"] == DocumentVersionStatus.VALIDATED.value
