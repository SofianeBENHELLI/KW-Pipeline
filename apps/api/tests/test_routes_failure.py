"""HTTP-level coverage for the parser-failure path:
POST /extract returns 422 with the persisted reason in `detail`,
and GET /documents{,/{id}} surfaces `failure_reason` for FAILED versions."""

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app


class FailingParser:
    """Parser stub that raises so the /extract endpoint can be observed
    persisting and surfacing the failure reason."""

    name = "failing"
    version = "test"

    def parse(self, version, storage):
        raise RuntimeError("simulated parser failure")


def _client_with_failing_parser() -> TestClient:
    """Build the standard pipeline, then swap the parser used by the extraction
    job service for a stub that raises. ExtractionJobService.parser is a regular
    attribute, so this monkey-patch is safe even though PipelineServices is
    a frozen dataclass."""
    services = build_services()
    services.extraction_jobs.parser = FailingParser()
    return TestClient(create_app(services=services))


def _upload(client: TestClient) -> dict:
    return client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"contents", "text/plain")},
    ).json()


class TestExtractEndpointFailurePath:
    def test_extract_returns_422_with_persisted_reason(self):
        client = _client_with_failing_parser()
        version = _upload(client)

        response = client.post(
            f"/documents/{version['document_id']}/versions/{version['id']}/extract"
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "FailingParser: simulated parser failure"

    def test_failure_reason_is_visible_via_get_document(self):
        client = _client_with_failing_parser()
        version = _upload(client)

        client.post(f"/documents/{version['document_id']}/versions/{version['id']}/extract")
        document = client.get(f"/documents/{version['document_id']}").json()

        failed_version = document["versions"][0]
        assert failed_version["status"] == "FAILED"
        assert failed_version["failure_reason"] == "FailingParser: simulated parser failure"

    def test_failure_reason_is_visible_in_catalog_listing(self):
        client = _client_with_failing_parser()
        version = _upload(client)

        client.post(f"/documents/{version['document_id']}/versions/{version['id']}/extract")
        catalog = client.get("/documents").json()

        assert catalog[0]["versions"][0]["status"] == "FAILED"
        assert (
            catalog[0]["versions"][0]["failure_reason"] == "FailingParser: simulated parser failure"
        )
