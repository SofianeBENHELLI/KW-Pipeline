"""Tests for the catalog search / filter / sort surface (#86).

Covers the first slice of #86: ``status`` repeatable query param and
``q`` filename substring match on ``GET /documents``, plus the
underlying ``CatalogStore.list_documents`` filter args. Sort order
stays ``(created_at ASC, id ASC)`` — the ``sort=`` query param lands
in a follow-up.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.services.catalog_store import InMemoryCatalogStore
from app.services.document_service import DocumentService
from app.services.storage_service import InMemoryStorageService

PLAIN = "text/plain"


# ─── In-process service tests ─────────────────────────────────────────


def _seed(documents: DocumentService, *names: str) -> dict[str, str]:
    """Upload one tiny version per name; return {name → document_id}."""
    out: dict[str, str] = {}
    for name in names:
        version = documents.upload(name, PLAIN, name.encode("utf-8"))
        out[name] = version.document_id
    return out


def test_status_filter_keeps_only_documents_whose_latest_version_matches() -> None:
    documents = DocumentService(storage=InMemoryStorageService())
    ids = _seed(documents, "ok.txt", "needs_review.txt", "failed.txt")

    # Drive each version into a distinct state.
    catalog = documents.catalog
    assert isinstance(catalog, InMemoryCatalogStore)
    catalog.versions[
        catalog.documents[ids["needs_review.txt"]].latest_version_id
    ].status = DocumentVersionStatus.NEEDS_REVIEW
    catalog.versions[
        catalog.documents[ids["failed.txt"]].latest_version_id
    ].status = DocumentVersionStatus.FAILED

    needs_review_only = catalog.list_documents(
        status_filter=frozenset({DocumentVersionStatus.NEEDS_REVIEW})
    )
    assert {d.original_filename for d in needs_review_only} == {"needs_review.txt"}

    pair = catalog.list_documents(
        status_filter=frozenset({DocumentVersionStatus.NEEDS_REVIEW, DocumentVersionStatus.FAILED})
    )
    assert {d.original_filename for d in pair} == {"needs_review.txt", "failed.txt"}


def test_filename_query_is_case_insensitive_substring_match() -> None:
    documents = DocumentService(storage=InMemoryStorageService())
    _seed(documents, "Procurement-Policy.txt", "supplier.docx", "Procurement_Annex.pdf")

    page = documents.catalog.list_documents(filename_query="procurement")
    assert {d.original_filename for d in page} == {
        "Procurement-Policy.txt",
        "Procurement_Annex.pdf",
    }


def test_filters_compose_with_each_other() -> None:
    documents = DocumentService(storage=InMemoryStorageService())
    ids = _seed(
        documents,
        "Procurement-Policy.txt",
        "Procurement_Annex.pdf",
        "supplier.docx",
    )
    catalog = documents.catalog
    assert isinstance(catalog, InMemoryCatalogStore)
    # Park "Annex" in NEEDS_REVIEW and the others in STORED.
    catalog.versions[
        catalog.documents[ids["Procurement_Annex.pdf"]].latest_version_id
    ].status = DocumentVersionStatus.NEEDS_REVIEW

    page = catalog.list_documents(
        filename_query="procurement",
        status_filter=frozenset({DocumentVersionStatus.NEEDS_REVIEW}),
    )
    assert [d.original_filename for d in page] == ["Procurement_Annex.pdf"]


def test_filters_compose_with_cursor_pagination() -> None:
    documents = DocumentService(storage=InMemoryStorageService())
    # Six matching docs to walk through across two pages.
    for i in range(6):
        documents.upload(f"contract-{i:02d}.txt", PLAIN, b"x")
    documents.upload("supplier.docx", PLAIN, b"y")  # Filter target — excluded.

    page1, cursor = documents.list_documents_page(
        limit=4,
        filename_query="contract",
    )
    assert [d.original_filename for d in page1] == [
        "contract-00.txt",
        "contract-01.txt",
        "contract-02.txt",
        "contract-03.txt",
    ]
    assert cursor is not None

    page2, next_cursor = documents.list_documents_page(
        limit=4,
        cursor=cursor,
        filename_query="contract",
    )
    assert [d.original_filename for d in page2] == ["contract-04.txt", "contract-05.txt"]
    assert next_cursor is None


# ─── Route-level tests ────────────────────────────────────────────────


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _upload(client: TestClient, name: str, body: bytes = b"x") -> dict:
    r = client.post(
        "/documents/upload",
        files={"file": (name, body, PLAIN)},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_route_status_filter_returns_only_matching(client: TestClient) -> None:
    a = _upload(client, "alpha.txt", b"alpha")
    b = _upload(client, "beta.txt", b"beta")
    # Drive `alpha` to NEEDS_REVIEW via extract → semantic.
    client.post(f"/documents/{a['document_id']}/versions/{a['id']}/extract")
    client.post(f"/documents/{a['document_id']}/versions/{a['id']}/semantic")

    response = client.get("/documents", params={"status": "NEEDS_REVIEW"})
    assert response.status_code == 200, response.text
    body = response.json()
    names = [d["original_filename"] for d in body["items"]]
    assert names == ["alpha.txt"]
    assert b["id"] not in names  # paranoid sanity


def test_route_status_filter_repeatable(client: TestClient) -> None:
    a = _upload(client, "a.txt", body=b"alpha-bytes")
    _upload(client, "b.txt", body=b"beta-bytes")
    # `a` to NEEDS_REVIEW; `b` stays STORED. Distinct bodies dodge the
    # SHA-256 dedup that would otherwise park `b` in DUPLICATE_DETECTED.
    client.post(f"/documents/{a['document_id']}/versions/{a['id']}/extract")
    client.post(f"/documents/{a['document_id']}/versions/{a['id']}/semantic")

    response = client.get(
        "/documents",
        params=[("status", "NEEDS_REVIEW"), ("status", "STORED")],
    )
    assert response.status_code == 200
    names = sorted(d["original_filename"] for d in response.json()["items"])
    assert names == ["a.txt", "b.txt"]


def test_route_unknown_status_returns_400(client: TestClient) -> None:
    response = client.get("/documents", params={"status": "GARBAGE"})
    assert response.status_code == 400
    body = response.json()
    detail = body.get("detail") or body.get("error", {}).get("message", "")
    assert "GARBAGE" in detail
    assert "VALIDATED" in detail  # allowed-set message lists known values


def test_route_filename_query_substring_case_insensitive(client: TestClient) -> None:
    _upload(client, "Procurement-Policy.txt")
    _upload(client, "Procurement_Annex.txt")
    _upload(client, "supplier.txt")

    response = client.get("/documents", params={"q": "procurement"})
    assert response.status_code == 200
    names = sorted(d["original_filename"] for d in response.json()["items"])
    assert names == ["Procurement-Policy.txt", "Procurement_Annex.txt"]


def test_route_filename_query_empty_string_acts_as_no_filter(client: TestClient) -> None:
    _upload(client, "alpha.txt")
    _upload(client, "beta.txt")

    response = client.get("/documents", params={"q": "   "})
    assert response.status_code == 200
    names = sorted(d["original_filename"] for d in response.json()["items"])
    assert names == ["alpha.txt", "beta.txt"]


def test_route_filters_compose_at_http_layer(client: TestClient) -> None:
    a = _upload(client, "Procurement-Policy.txt")
    b = _upload(client, "Procurement_Annex.txt")
    _upload(client, "supplier.txt")

    # Park `a` in NEEDS_REVIEW, leave the rest in STORED.
    client.post(f"/documents/{a['document_id']}/versions/{a['id']}/extract")
    client.post(f"/documents/{a['document_id']}/versions/{a['id']}/semantic")

    response = client.get(
        "/documents",
        params={"q": "procurement", "status": "NEEDS_REVIEW"},
    )
    assert response.status_code == 200
    names = [d["original_filename"] for d in response.json()["items"]]
    assert names == ["Procurement-Policy.txt"]
    assert b["id"] not in names
