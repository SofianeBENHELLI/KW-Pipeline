"""End-to-end presenter-path smoke test (closes #132).

Walks the full happy path (upload → catalog → extract → semantic →
validate) and a single rejection path (upload → extract → semantic →
reject) using FastAPI's ``TestClient``. The test is in-process and runs
in the default ``pytest`` invocation — there is no ``-m integration``
marker, the coverage target stays intact, and no network hop is made.

The fixture is intentionally tiny (~140 bytes of plain text) and
deterministic so a failure points unambiguously at the demo step that
broke. Every assertion carries a step label so the failure message
names the broken step, not just the failing assertion.
"""

from fastapi.testclient import TestClient

from app.main import create_app

# Keep the fixture under 200 bytes so the test stays fast and the
# diagnostic output on failure stays readable. The text is structured
# enough that PlainTextParser produces multiple sections, which gives
# the semantic generator real input.
_DEMO_TEXT = (
    b"Supplier Quality Policy\n"
    b"\n"
    b"1. Inbound lots are sampled per AQL 1.5.\n"
    b"2. Failures are quarantined within 8 hours.\n"
    b"3. Records are retained for ten years.\n"
)


def _client() -> TestClient:
    return TestClient(create_app())


def _upload_demo_fixture(client: TestClient) -> dict:
    response = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", _DEMO_TEXT, "text/plain")},
    )
    assert response.status_code == 200, (
        f"Step 1 (upload) failed: HTTP {response.status_code}: {response.text}"
    )
    return response.json()


def test_presenter_demo_path() -> None:
    """Happy path: upload → catalog → extract → semantic → validate."""
    client = _client()

    # Step 1 — upload
    version = _upload_demo_fixture(client)
    assert version["status"] == "STORED", (
        f"Step 1 (upload) returned unexpected status: {version['status']!r}"
    )
    document_id = version["document_id"]
    version_id = version["id"]

    # Step 2 — catalog includes the new document
    listing = client.get("/documents")
    assert listing.status_code == 200, f"Step 2 (catalog list) failed: HTTP {listing.status_code}"
    document_ids = {item["id"] for item in listing.json()["items"]}
    assert document_id in document_ids, (
        f"Step 2 (catalog list) did not include document {document_id}; saw {document_ids}"
    )

    # Step 3 — extract produces a raw extraction
    extract = client.post(
        f"/documents/{document_id}/versions/{version_id}/extract",
    )
    assert extract.status_code == 200, (
        f"Step 3 (extract) failed: HTTP {extract.status_code}: {extract.text}"
    )
    raw = extract.json()
    assert raw.get("sections"), (
        "Step 3 (extract) produced no sections; PlainTextParser should emit "
        f"one section per non-empty line. Got: {raw}"
    )

    # Step 3b — raw extraction is retrievable
    raw_get = client.get(f"/documents/{document_id}/versions/{version_id}/extraction")
    assert raw_get.status_code == 200, (
        f"Step 3b (get extraction) failed: HTTP {raw_get.status_code}"
    )

    # Step 4 — semantic generation lands the version in NEEDS_REVIEW
    semantic = client.post(
        f"/documents/{document_id}/versions/{version_id}/semantic",
    )
    assert semantic.status_code == 200, (
        f"Step 4 (semantic) failed: HTTP {semantic.status_code}: {semantic.text}"
    )

    # Step 4b — markdown is available for the review UI
    markdown = client.get(f"/documents/{document_id}/versions/{version_id}/markdown")
    assert markdown.status_code == 200, f"Step 4b (markdown) failed: HTTP {markdown.status_code}"
    assert markdown.text.strip(), "Step 4b (markdown) returned an empty body."

    # Sanity: document is now in NEEDS_REVIEW before validation.
    pre_validate = client.get(f"/documents/{document_id}").json()
    assert pre_validate["versions"][0]["status"] == "NEEDS_REVIEW", (
        "Step 4 sanity check failed: version should be NEEDS_REVIEW after "
        f"semantic generation, got {pre_validate['versions'][0]['status']!r}"
    )

    # Step 5 — validate transitions the version to VALIDATED
    validate = client.post(
        f"/documents/{document_id}/versions/{version_id}/validate",
        json={"reviewer_note": "smoke test: lineage looks fine"},
    )
    assert validate.status_code == 200, (
        f"Step 5 (validate) failed: HTTP {validate.status_code}: {validate.text}"
    )
    assert validate.json()["validation_status"] == "validated", (
        "Step 5 (validate) did not flip validation_status to 'validated'."
    )

    final = client.get(f"/documents/{document_id}").json()
    final_version = final["versions"][0]
    assert final_version["status"] == "VALIDATED", (
        f"Step 5 (validate) left version in {final_version['status']!r} instead of VALIDATED."
    )
    assert final_version["reviewer_note"] == "smoke test: lineage looks fine", (
        "Step 5 (validate) lost the reviewer note."
    )


def test_presenter_reject_path() -> None:
    """Rejection path: upload → extract → semantic → reject → REJECTED."""
    client = _client()

    version = _upload_demo_fixture(client)
    document_id = version["document_id"]
    version_id = version["id"]

    extract = client.post(f"/documents/{document_id}/versions/{version_id}/extract")
    assert extract.status_code == 200, (
        f"Reject path step 2 (extract) failed: HTTP {extract.status_code}"
    )

    semantic = client.post(f"/documents/{document_id}/versions/{version_id}/semantic")
    assert semantic.status_code == 200, (
        f"Reject path step 3 (semantic) failed: HTTP {semantic.status_code}"
    )

    reject = client.post(
        f"/documents/{document_id}/versions/{version_id}/reject",
        json={"reviewer_note": "smoke test: missing source citation"},
    )
    assert reject.status_code == 200, (
        f"Reject path step 4 (reject) failed: HTTP {reject.status_code}: {reject.text}"
    )
    assert reject.json()["validation_status"] == "rejected", (
        "Reject path step 4 did not flip validation_status to 'rejected'."
    )

    final = client.get(f"/documents/{document_id}").json()["versions"][0]
    assert final["status"] == "REJECTED", (
        f"Reject path step 4 left version in {final['status']!r} instead of REJECTED."
    )
    assert final["reviewer_note"] == "smoke test: missing source citation", (
        "Reject path step 4 lost the reviewer note."
    )
