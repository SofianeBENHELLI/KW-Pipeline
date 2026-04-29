from fastapi.testclient import TestClient

from app.main import create_app


def test_health_endpoint_returns_ok():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_catalog_detail_extract_and_semantic_flow():
    client = TestClient(create_app())

    upload_response = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"Policy title\nReview required", "text/plain")},
    )
    assert upload_response.status_code == 200
    version = upload_response.json()
    assert version["filename"] == "policy.txt"
    assert version["status"] == "STORED"
    assert len(version["sha256"]) == 64

    catalog_response = client.get("/documents")
    assert catalog_response.status_code == 200
    assert catalog_response.json()[0]["id"] == version["document_id"]

    detail_response = client.get(f"/documents/{version['document_id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["versions"][0]["id"] == version["id"]

    extraction_response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract"
    )
    assert extraction_response.status_code == 200
    extraction = extraction_response.json()
    assert extraction["parser_name"] == "plain_text"
    assert len(extraction["source_references"]) == 2

    semantic_response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/semantic"
    )
    assert semantic_response.status_code == 200
    semantic = semantic_response.json()
    assert semantic["validation_status"] == "needs_review"
    assert "# Policy" in semantic["markdown"]
    assert "Policy title" in semantic["markdown"]
    assert "## Source Lineage" in semantic["markdown"]


def test_upload_rejects_empty_file():
    client = TestClient(create_app())

    response = client.post(
        "/documents/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded file is empty."


def test_duplicate_upload_conflicts_when_extracting_duplicate_version():
    client = TestClient(create_app())

    first = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"same bytes", "text/plain")},
    ).json()
    duplicate_response = client.post(
        "/documents/upload",
        files={"file": ("renamed.txt", b"same bytes", "text/plain")},
    )
    duplicate = duplicate_response.json()

    assert duplicate["status"] == "DUPLICATE_DETECTED"
    assert duplicate["duplicate_of_version_id"] == first["id"]

    extraction_response = client.post(
        f"/documents/{duplicate['document_id']}/versions/{duplicate['id']}/extract"
    )

    assert extraction_response.status_code == 409
    assert "Duplicate versions are not extracted independently." in extraction_response.json()["detail"]
