"""HTTP-level coverage for ``GET /documents/{id}/lineage`` (EPIC-C C.3).

The route returns the version history of a document family in the
shape the lineage modal renders, with two derived fields:

- ``is_latest`` — flagged on the highest-version-numbered version.
- ``superseded_by_version_id`` — points at the next-higher version
  when the row is ``SUPERSEDED``; reconstructed from
  ``(version_number, status)`` ordering per ADR-025.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.models.document import DocumentVersionStatus


@pytest.fixture(autouse=True)
def _disable_scope_filter(monkeypatch):
    """Bypass the D.5 scope filter for this module.

    These tests upload via the in-process ``services.documents.upload``
    helper which doesn't write a ``personal:dev`` scope link (the
    scope row is written by the upload route, not the service). The
    legacy ``KW_AUTH_MODE=disabled`` mode skips the scope predicate
    entirely so the tests keep exercising lineage semantics without
    threading a per-call scope link.
    """
    monkeypatch.setenv("KW_AUTH_MODE", "disabled")


def _client_with_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _land_in_needs_review(services, *, document_id: str | None = None, content: bytes):
    version = services.documents.upload(
        filename="policy.txt",
        content_type="text/plain",
        content=content,
        document_id=document_id,
    )
    services.extraction_jobs.extract(document_id=version.document_id, version_id=version.id)
    services.semantic_outputs.generate(document_id=version.document_id, version_id=version.id)
    return version.document_id, version.id


def test_lineage_404_for_unknown_document():
    client, _ = _client_with_services()

    response = client.get("/documents/does-not-exist/lineage")

    assert response.status_code == 404


def test_lineage_returns_versions_sorted_ascending_by_version_number():
    client, services = _client_with_services()
    document_id, v1_id = _land_in_needs_review(services, content=b"first body of family")
    services.review.handle_validation(document_id=document_id, version_id=v1_id, actor="alice")
    _, v2_id = _land_in_needs_review(
        services, document_id=document_id, content=b"second body of family"
    )
    services.review.handle_validation(document_id=document_id, version_id=v2_id, actor="alice")

    response = client.get(f"/documents/{document_id}/lineage")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == document_id
    version_numbers = [v["version_number"] for v in body["versions"]]
    assert version_numbers == sorted(version_numbers)


def test_lineage_marks_only_highest_version_as_latest():
    client, services = _client_with_services()
    document_id, v1_id = _land_in_needs_review(services, content=b"first body of family")
    services.review.handle_validation(document_id=document_id, version_id=v1_id, actor="alice")
    _, v2_id = _land_in_needs_review(
        services, document_id=document_id, content=b"second body of family"
    )
    services.review.handle_validation(document_id=document_id, version_id=v2_id, actor="alice")

    response = client.get(f"/documents/{document_id}/lineage")

    body = response.json()
    by_id = {v["id"]: v for v in body["versions"]}
    assert by_id[v1_id]["is_latest"] is False
    assert by_id[v2_id]["is_latest"] is True


def test_lineage_v1_superseded_by_points_at_v2_id():
    client, services = _client_with_services()
    document_id, v1_id = _land_in_needs_review(services, content=b"first body of family")
    services.review.handle_validation(document_id=document_id, version_id=v1_id, actor="alice")
    _, v2_id = _land_in_needs_review(
        services, document_id=document_id, content=b"second body of family"
    )
    services.review.handle_validation(document_id=document_id, version_id=v2_id, actor="alice")

    response = client.get(f"/documents/{document_id}/lineage")

    body = response.json()
    by_id = {v["id"]: v for v in body["versions"]}
    # v1 was auto-superseded when v2 validated (ADR-025 §C.1).
    assert by_id[v1_id]["status"] == DocumentVersionStatus.SUPERSEDED.value
    assert by_id[v1_id]["superseded_by_version_id"] == v2_id
    # v2 itself is the active head; nothing supersedes it.
    assert by_id[v2_id]["superseded_by_version_id"] is None


def test_lineage_family_filename_is_latest_versions_filename():
    client, services = _client_with_services()
    # First version uses one filename, second a different one — the
    # response label tracks the latest.
    v1 = services.documents.upload(
        filename="policy-v1.txt",
        content_type="text/plain",
        content=b"first body of family",
    )
    services.extraction_jobs.extract(document_id=v1.document_id, version_id=v1.id)
    services.semantic_outputs.generate(document_id=v1.document_id, version_id=v1.id)
    services.review.handle_validation(document_id=v1.document_id, version_id=v1.id, actor="alice")

    v2 = services.documents.upload(
        filename="policy-v2.txt",
        content_type="text/plain",
        content=b"second body of family",
        document_id=v1.document_id,
    )
    services.extraction_jobs.extract(document_id=v2.document_id, version_id=v2.id)
    services.semantic_outputs.generate(document_id=v2.document_id, version_id=v2.id)
    services.review.handle_validation(document_id=v2.document_id, version_id=v2.id, actor="alice")

    response = client.get(f"/documents/{v1.document_id}/lineage")

    body = response.json()
    assert body["family_filename"] == "policy-v2.txt"


def test_lineage_single_version_family_has_no_superseded_chain():
    client, services = _client_with_services()
    v1 = services.documents.upload(
        filename="policy.txt",
        content_type="text/plain",
        content=b"only version of family",
    )

    response = client.get(f"/documents/{v1.document_id}/lineage")

    assert response.status_code == 200
    body = response.json()
    assert len(body["versions"]) == 1
    assert body["versions"][0]["is_latest"] is True
    assert body["versions"][0]["superseded_by_version_id"] is None


def test_build_lineage_response_with_no_versions_returns_empty_versions_list():
    """Defensive branch in ``_build_lineage_response``: an empty
    ``Document.versions`` list (only possible mid-archive race) yields
    a lineage response with ``versions=[]`` rather than crashing."""
    from datetime import UTC, datetime

    from app.routes.lifecycle import _build_lineage_response
    from app.schemas.document import Document

    doc = Document(
        id="empty-doc",
        original_filename="empty.txt",
        latest_version_id="ignored",
        created_at=datetime.now(UTC),
        versions=[],
    )

    response = _build_lineage_response(doc)
    assert response.document_id == "empty-doc"
    assert response.family_filename == "empty.txt"
    assert response.versions == []


def test_get_version_including_archived_raises_when_version_id_missing():
    """``_get_version_including_archived`` resolves a version even on
    archived documents, but raises ``KeyError`` when the version_id
    isn't on the document. Pinned with a direct unit test rather than
    going through HTTP so the helper's contract stays explicit."""
    import pytest

    from app.dependencies import build_services
    from app.routes.lifecycle import _get_version_including_archived

    services = build_services()
    version = services.documents.upload(
        filename="seed.txt",
        content_type="text/plain",
        content=b"unit test seed",
    )

    # Wrong version_id on a real document → KeyError on the version, not
    # the document. Catches the line-829 fall-through after the for
    # loop completes without finding a match.
    with pytest.raises(KeyError, match="Document version not found"):
        _get_version_including_archived(
            services=services,
            document_id=version.document_id,
            version_id="nonexistent-version-id",
        )

    # Wrong document_id → the earlier KeyError at line 825.
    with pytest.raises(KeyError, match="Document not found"):
        _get_version_including_archived(
            services=services,
            document_id="nonexistent-doc-id",
            version_id="anything",
        )
