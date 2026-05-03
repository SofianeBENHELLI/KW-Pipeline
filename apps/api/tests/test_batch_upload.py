"""Tests for the bulk-upload route (#82).

Covers the per-file outcome shapes the issue's acceptance criteria
call out (uploaded / duplicate / rejected_content_type / too_large /
empty / failed) plus the summary counters and idempotency replay
semantics. Single bad file in a mixed batch must not hide
successful files.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

PLAIN = "text/plain"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _post_batch(client: TestClient, files: list[tuple[str, bytes, str]]) -> dict:
    """Multipart POST with one ``files`` part per (name, body, mime) tuple."""
    response = client.post(
        "/documents/upload/batch",
        files=[("files", (n, b, m)) for n, b, m in files],
    )
    return response


# ─── Happy path ───────────────────────────────────────────────────────


def test_batch_uploads_two_distinct_files_and_reports_both(client: TestClient) -> None:
    response = _post_batch(
        client,
        [("a.txt", b"alpha", PLAIN), ("b.txt", b"beta", PLAIN)],
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["summary"] == {
        "total": 2,
        "uploaded": 2,
        "duplicate": 0,
        "rejected_content_type": 0,
        "too_large": 0,
        "empty": 0,
        "failed": 0,
    }
    statuses = sorted(o["status"] for o in body["results"])
    assert statuses == ["uploaded", "uploaded"]
    for outcome in body["results"]:
        assert outcome["document_id"]
        assert outcome["version_id"]
        assert outcome["sha256"]
        assert outcome["error_code"] is None
        assert outcome["error_message"] is None


def test_batch_marks_second_identical_file_as_duplicate(client: TestClient) -> None:
    """Two files with identical bytes — the second is deduped."""
    response = _post_batch(
        client,
        [("a.txt", b"shared", PLAIN), ("b.txt", b"shared", PLAIN)],
    )
    assert response.status_code == 200, response.text
    body = response.json()

    statuses = [o["status"] for o in body["results"]]
    assert statuses[0] == "uploaded"
    assert statuses[1] == "duplicate"
    assert body["summary"]["uploaded"] == 1
    assert body["summary"]["duplicate"] == 1
    # Both rows still carry document_id/version_id/sha256 — duplicates
    # belong to the same hash but get their own version row.
    assert body["results"][0]["sha256"] == body["results"][1]["sha256"]


# ─── Validation outcomes ──────────────────────────────────────────────


def test_batch_rejects_unsupported_content_type_per_file(client: TestClient) -> None:
    response = _post_batch(
        client,
        [
            ("ok.txt", b"hello", PLAIN),
            ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png"),
        ],
    )
    assert response.status_code == 200
    body = response.json()
    statuses = [o["status"] for o in body["results"]]
    assert statuses == ["uploaded", "rejected_content_type"]
    rejected = body["results"][1]
    assert rejected["error_code"] == "KW_UPLOAD_UNSUPPORTED_TYPE"
    assert "image/png" in rejected["error_message"]
    assert rejected["document_id"] is None and rejected["version_id"] is None


def test_batch_marks_oversize_file_too_large(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tiny 32-byte cap so we don't have to ship a megabyte fixture.
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "32")
    fresh = TestClient(create_app())

    response = _post_batch(
        fresh,
        [
            ("small.txt", b"hello", PLAIN),
            ("big.txt", b"x" * 200, PLAIN),
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    statuses = [o["status"] for o in body["results"]]
    assert statuses == ["uploaded", "too_large"]
    big = body["results"][1]
    assert big["error_code"] == "KW_UPLOAD_TOO_LARGE"
    assert big["bytes"] > 32  # we read past the cap before bailing


def test_batch_flags_empty_files_without_aborting_the_batch(client: TestClient) -> None:
    response = _post_batch(
        client,
        [
            ("ok.txt", b"hello", PLAIN),
            ("empty.txt", b"", PLAIN),
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    statuses = [o["status"] for o in body["results"]]
    assert statuses == ["uploaded", "empty"]
    empty = body["results"][1]
    assert empty["error_code"] == "KW_UPLOAD_EMPTY"
    assert empty["bytes"] == 0


def test_batch_mixed_outcomes_summary_decomposes_total(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #82 acceptance: 'one bad file must not hide successful files.'"""
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "16")
    monkeypatch.setenv("ALLOWED_CONTENT_TYPES", PLAIN)
    fresh = TestClient(create_app())

    response = _post_batch(
        fresh,
        [
            ("good-1.txt", b"hello", PLAIN),
            ("good-2.txt", b"world", PLAIN),
            ("dup.txt", b"hello", PLAIN),  # duplicate of good-1
            ("photo.png", b"x", "image/png"),  # rejected
            ("big.txt", b"x" * 200, PLAIN),  # too_large
            ("empty.txt", b"", PLAIN),  # empty
        ],
    )
    assert response.status_code == 200
    body = response.json()
    summary = body["summary"]
    assert summary["total"] == 6
    assert summary["uploaded"] == 2
    assert summary["duplicate"] == 1
    assert summary["rejected_content_type"] == 1
    assert summary["too_large"] == 1
    assert summary["empty"] == 1
    assert summary["failed"] == 0
    # Total decomposes into the buckets.
    assert (
        sum(
            summary[k]
            for k in (
                "uploaded",
                "duplicate",
                "rejected_content_type",
                "too_large",
                "empty",
                "failed",
            )
        )
        == summary["total"]
    )


# ─── Edge cases ───────────────────────────────────────────────────────


def test_batch_zero_files_returns_400(client: TestClient) -> None:
    """An empty multipart envelope is a request-shape error, not a
    per-file outcome — the route surfaces it with the public error
    contract."""
    # FastAPI rejects truly-empty multipart at parse time; sending a
    # file with no parts via the normal upload form is the closest we
    # can get without forging a raw request. Use a deliberately-bad
    # body to trigger 400/422.
    response = client.post("/documents/upload/batch")
    # FastAPI's missing-required-field path returns 422; we accept
    # either 4xx as long as it isn't a 5xx (the route explicitly
    # rejects empty file lists with our own 400 too).
    assert 400 <= response.status_code < 500


def test_batch_idempotency_replay_returns_same_report(client: TestClient) -> None:
    files = [("a.txt", b"alpha-once", PLAIN), ("b.txt", b"beta-once", PLAIN)]
    headers = {"Idempotency-Key": "batch-1"}

    first = client.post(
        "/documents/upload/batch",
        files=[("files", (n, b, m)) for n, b, m in files],
        headers=headers,
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["summary"]["uploaded"] == 2

    # Replay with the same key. Without idempotency, the second call
    # would mark both as duplicates (same bytes) — proving the cache
    # returned the original report unchanged.
    replay = client.post(
        "/documents/upload/batch",
        files=[("files", (n, b, m)) for n, b, m in files],
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json() == first_body
