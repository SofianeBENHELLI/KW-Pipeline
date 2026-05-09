from fastapi.testclient import TestClient

from app.main import create_app


def test_health_endpoint_returns_ok():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_endpoint_reports_catalog_ok_and_neo4j_disabled_by_default():
    client = TestClient(create_app())

    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["catalog"]["status"] == "ok"
    # Knowledge layer is off by default → neo4j check reports disabled.
    assert body["checks"]["neo4j"]["status"] == "disabled"


def test_ready_endpoint_reports_503_when_catalog_probe_fails(monkeypatch):
    app = create_app()
    services = app.state.services

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated catalog outage")

    monkeypatch.setattr(services.documents.catalog, "list_documents", _boom)
    client = TestClient(app)

    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"]["catalog"]["status"] == "error"
    assert "simulated catalog outage" in body["checks"]["catalog"]["detail"]


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
    catalog_body = catalog_response.json()
    assert catalog_body["items"][0]["id"] == version["document_id"]
    assert catalog_body["next_cursor"] is None

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

    get_extraction_response = client.get(
        f"/documents/{version['document_id']}/versions/{version['id']}/extraction"
    )
    assert get_extraction_response.status_code == 200
    assert get_extraction_response.json()["id"] == extraction["id"]

    semantic_response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/semantic"
    )
    assert semantic_response.status_code == 200
    semantic = semantic_response.json()
    assert semantic["validation_status"] == "needs_review"
    assert "# Policy" in semantic["markdown"]
    assert "Policy title" in semantic["markdown"]
    assert "## Source Lineage" in semantic["markdown"]

    semantic_detail_response = client.get(f"/documents/{version['document_id']}")
    assert semantic_detail_response.status_code == 200
    assert semantic_detail_response.json()["versions"][0]["status"] == "NEEDS_REVIEW"

    get_semantic_response = client.get(
        f"/documents/{version['document_id']}/versions/{version['id']}/semantic"
    )
    assert get_semantic_response.status_code == 200
    assert get_semantic_response.json()["id"] == semantic["id"]

    get_markdown_response = client.get(
        f"/documents/{version['document_id']}/versions/{version['id']}/markdown"
    )
    assert get_markdown_response.status_code == 200
    assert get_markdown_response.headers["content-type"].startswith("text/markdown")
    assert get_markdown_response.text == semantic["markdown"]


def test_get_raw_file_returns_uploaded_bytes():
    client = TestClient(create_app())
    payload = b"Policy title\nReview required"
    upload_response = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", payload, "text/plain")},
    )
    version = upload_response.json()

    response = client.get(f"/documents/{version['document_id']}/versions/{version['id']}/raw")

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"].startswith("text/plain")
    disposition = response.headers["content-disposition"]
    assert "inline" in disposition
    assert "policy.txt" in disposition


def test_get_raw_file_returns_404_for_missing_version():
    client = TestClient(create_app())

    response = client.get("/documents/missing/versions/missing/raw")

    assert response.status_code == 404


def test_get_extraction_returns_404_before_extraction():
    client = TestClient(create_app())
    version = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"Policy title", "text/plain")},
    ).json()

    response = client.get(
        f"/documents/{version['document_id']}/versions/{version['id']}/extraction"
    )

    assert response.status_code == 404
    assert "Raw extraction not found." in response.json()["detail"]


def test_get_semantic_and_markdown_return_404_before_generation():
    client = TestClient(create_app())
    version = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"Policy title", "text/plain")},
    ).json()
    client.post(f"/documents/{version['document_id']}/versions/{version['id']}/extract")

    semantic_response = client.get(
        f"/documents/{version['document_id']}/versions/{version['id']}/semantic"
    )
    markdown_response = client.get(
        f"/documents/{version['document_id']}/versions/{version['id']}/markdown"
    )

    assert semantic_response.status_code == 404
    assert "Semantic output not found." in semantic_response.json()["detail"]
    assert markdown_response.status_code == 404
    assert "Semantic output not found." in markdown_response.json()["detail"]


def test_semantic_generation_returns_404_before_extraction():
    client = TestClient(create_app())
    version = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"Policy title", "text/plain")},
    ).json()

    response = client.post(f"/documents/{version['document_id']}/versions/{version['id']}/semantic")

    assert response.status_code == 404
    assert "Raw extraction not found." in response.json()["detail"]


def test_retrieval_endpoints_return_404_for_missing_version():
    client = TestClient(create_app())

    extraction_response = client.get("/documents/missing/versions/missing/extraction")
    semantic_response = client.get("/documents/missing/versions/missing/semantic")
    markdown_response = client.get("/documents/missing/versions/missing/markdown")

    assert extraction_response.status_code == 404
    assert semantic_response.status_code == 404
    assert markdown_response.status_code == 404


def test_semantic_generation_is_cached_for_repeat_requests():
    client = TestClient(create_app())
    version = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"Policy title", "text/plain")},
    ).json()
    client.post(f"/documents/{version['document_id']}/versions/{version['id']}/extract")

    first = client.post(f"/documents/{version['document_id']}/versions/{version['id']}/semantic")
    second = client.post(f"/documents/{version['document_id']}/versions/{version['id']}/semantic")
    fetched = client.get(f"/documents/{version['document_id']}/versions/{version['id']}/semantic")

    assert first.status_code == 200
    assert second.status_code == 200
    assert fetched.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert fetched.json()["id"] == first.json()["id"]


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
    assert (
        "Duplicate versions are not extracted independently."
        in extraction_response.json()["detail"]
    )


def test_persistent_app_keeps_catalog_between_app_instances(tmp_path):
    first_client = TestClient(create_app(persistent=True, data_dir=str(tmp_path)))
    upload_response = first_client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"Persistent API policy", "text/plain")},
    )
    assert upload_response.status_code == 200
    uploaded = upload_response.json()

    second_client = TestClient(create_app(persistent=True, data_dir=str(tmp_path)))
    catalog_response = second_client.get("/documents")

    assert catalog_response.status_code == 200
    body = catalog_response.json()
    assert body["items"][0]["id"] == uploaded["document_id"]
    assert body["items"][0]["versions"][0]["id"] == uploaded["id"]
    assert body["next_cursor"] is None
