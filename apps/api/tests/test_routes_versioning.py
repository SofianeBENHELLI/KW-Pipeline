"""HTTP-level coverage for the versioned upload path:
POST /documents/upload?document_id=<existing> appends a v2,
POST /documents/upload?document_id=<missing>  returns 404."""

from fastapi.testclient import TestClient

from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def _upload(client: TestClient, *, content: bytes, document_id: str | None = None) -> dict:
    params = {"document_id": document_id} if document_id else None
    response = client.post(
        "/documents/upload",
        params=params,
        files={"file": ("policy.txt", content, "text/plain")},
    )
    return response.status_code, response.json()


class TestVersionedUploadHttp:
    def test_uploading_with_document_id_query_appends_v2(self):
        client = _client()
        _, v1 = _upload(client, content=b"first")

        status, v2 = _upload(client, content=b"second", document_id=v1["document_id"])

        assert status == 200
        assert v2["document_id"] == v1["document_id"]
        assert v2["version_number"] == 2
        assert v2["status"] == "STORED"

        document = client.get(f"/documents/{v1['document_id']}").json()
        assert [v["id"] for v in document["versions"]] == [v1["id"], v2["id"]]
        assert document["latest_version_id"] == v2["id"]

    def test_versioned_upload_to_missing_document_returns_404(self):
        client = _client()

        status, body = _upload(client, content=b"orphan", document_id="not-a-real-document")

        assert status == 404
        assert "Document not found" in body["detail"]

    def test_uploading_without_document_id_still_creates_a_new_family(self):
        client = _client()

        status, v1 = _upload(client, content=b"first")
        _, v2 = _upload(client, content=b"second")  # no document_id → new family

        assert status == 200
        assert v1["document_id"] != v2["document_id"]
        assert v1["version_number"] == 1
        assert v2["version_number"] == 1
