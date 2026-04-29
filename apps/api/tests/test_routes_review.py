"""HTTP-level coverage for the validate / reject review endpoints."""

from fastapi.testclient import TestClient

from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def _drive_to_needs_review(client: TestClient) -> dict:
    """Upload, extract, and generate semantic — the only legal precondition
    for /validate and /reject."""
    version = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"text body", "text/plain")},
    ).json()
    client.post(f"/documents/{version['document_id']}/versions/{version['id']}/extract")
    client.post(f"/documents/{version['document_id']}/versions/{version['id']}/semantic")
    return version


class TestValidateEndpoint:
    def test_validate_with_note_updates_status_and_persists_note(self):
        client = _client()
        v = _drive_to_needs_review(client)

        response = client.post(
            f"/documents/{v['document_id']}/versions/{v['id']}/validate",
            json={"reviewer_note": "lineage checked, ship it"},
        )

        assert response.status_code == 200
        assert response.json()["validation_status"] == "validated"

        version = client.get(f"/documents/{v['document_id']}").json()["versions"][0]
        assert version["status"] == "VALIDATED"
        assert version["reviewer_note"] == "lineage checked, ship it"
        assert version["reviewed_at"] is not None

    def test_validate_without_body_uses_default_empty_request(self):
        client = _client()
        v = _drive_to_needs_review(client)

        response = client.post(f"/documents/{v['document_id']}/versions/{v['id']}/validate")

        assert response.status_code == 200
        version = client.get(f"/documents/{v['document_id']}").json()["versions"][0]
        assert version["status"] == "VALIDATED"
        assert version["reviewer_note"] is None

    def test_validate_returns_404_for_unknown_version(self):
        client = _client()

        response = client.post(
            "/documents/missing-doc/versions/missing-version/validate",
            json={},
        )

        assert response.status_code == 404


class TestRejectEndpoint:
    def test_reject_flips_status_and_validation_state(self):
        client = _client()
        v = _drive_to_needs_review(client)

        response = client.post(
            f"/documents/{v['document_id']}/versions/{v['id']}/reject",
            json={"reviewer_note": "missing lineage on key claim"},
        )

        assert response.status_code == 200
        assert response.json()["validation_status"] == "rejected"

        version = client.get(f"/documents/{v['document_id']}").json()["versions"][0]
        assert version["status"] == "REJECTED"
        assert version["reviewer_note"] == "missing lineage on key claim"


class TestReviewWrongState:
    def test_validate_refuses_when_status_is_not_needs_review(self):
        """A version that hasn't reached NEEDS_REVIEW yet (e.g. just STORED)
        cannot be validated."""
        client = _client()
        version = client.post(
            "/documents/upload",
            files={"file": ("p.txt", b"x", "text/plain")},
        ).json()

        response = client.post(
            f"/documents/{version['document_id']}/versions/{version['id']}/validate",
            json={},
        )

        assert response.status_code == 409
        assert "NEEDS_REVIEW" in response.json()["detail"]

    def test_reject_refuses_after_already_validated(self):
        client = _client()
        v = _drive_to_needs_review(client)
        client.post(
            f"/documents/{v['document_id']}/versions/{v['id']}/validate",
            json={},
        )

        # Second decision is rejected — the version is no longer in NEEDS_REVIEW.
        response = client.post(
            f"/documents/{v['document_id']}/versions/{v['id']}/reject",
            json={},
        )

        assert response.status_code == 409
