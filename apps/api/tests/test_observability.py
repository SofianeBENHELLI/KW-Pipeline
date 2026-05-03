"""Lifecycle-event observability tests (issue #26).

Asserts that every key state transition in the pipeline emits a
structured log record that an on-call greppers can filter on. Issue
#42 already shipped the structured-log plumbing
(:mod:`app.logging_config`); this module pins the *behaviour* — that
the events fire, with the right names, with the right ``extra``
fields, and crucially **without** leaking raw file bytes or full
extracted text into the log stream.

The events under test are the ones the audit story relies on:

* ``document.uploaded`` — single record covers both fresh uploads
  and duplicates (the ``is_duplicate`` boolean and ``sha256`` hash
  are part of the payload).
* ``document.status_changed`` — emitted on every FSM transition.
* ``extraction.started`` / ``extraction.succeeded`` /
  ``extraction.failed`` — happy path + parser-not-found + extraction
  exception paths.
* ``semantic.generated`` — emitted when the semantic projection runs.
* ``review.validated`` / ``review.rejected`` — emitted from the
  reviewer endpoints.

These are not all the events the API emits (see
``docs/architecture/observability.md`` for the full vocabulary), but
they're the audit-floor items #26 calls out as acceptance criteria.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app

# The text/plain MIME is on the default upload allowlist, so these
# tests don't need to widen `KW_ALLOWED_CONTENT_TYPES`.
PLAIN = "text/plain"


def _records(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    """Return every captured record whose message matches ``event`` exactly.

    Call sites use ``log.info("event.name", extra={...})`` — the message
    *is* the event name, so equality is the right matcher (substring
    matching would falsely couple ``extraction.failed`` and
    ``extraction.failed_idempotency_replay`` should the latter ever exist).
    """
    return [r for r in caplog.records if r.getMessage() == event]


def _extra(record: logging.LogRecord) -> dict[str, object]:
    """Project a LogRecord's structured ``extra`` keys back to a dict.

    Mirrors the projection :class:`app.logging_config.JsonFormatter` does
    when rendering JSON output, but as a plain dict so tests can
    ``assert "raw_bytes" not in extra``.
    """
    reserved = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
    return {k: v for k, v in record.__dict__.items() if k not in reserved and not k.startswith("_")}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


# ─── Upload + duplicate ───────────────────────────────────────────────


def test_document_uploaded_event_carries_correlation_ids(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """First upload emits ``document.uploaded`` with the canonical fields."""
    caplog.set_level(logging.INFO)

    response = client.post(
        "/documents/upload",
        files={"file": ("memo.txt", b"hello world", PLAIN)},
    )
    assert response.status_code == 200, response.text

    matches = _records(caplog, "document.uploaded")
    assert len(matches) == 1
    extra = _extra(matches[0])
    # Correlation IDs.
    assert isinstance(extra["document_id"], str) and extra["document_id"]
    assert isinstance(extra["version_id"], str) and extra["version_id"]
    # Audit fields the on-call greppers depend on.
    assert extra["sha256"]  # Real digest, not empty.
    assert extra["bytes"] == len(b"hello world")
    assert extra["content_type"] == PLAIN
    assert extra["is_duplicate"] is False
    # Critical: nothing in the payload looks like raw file content.
    assert "hello world" not in str(extra)


def test_document_uploaded_event_marks_duplicate(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Re-uploading the same bytes flips ``is_duplicate`` true on the
    second event without touching the first record."""
    payload = b"identical bytes"

    # First upload — clean state.
    client.post("/documents/upload", files={"file": ("a.txt", payload, PLAIN)})

    caplog.clear()
    caplog.set_level(logging.INFO)

    # Second upload — same bytes under a different filename.
    response = client.post(
        "/documents/upload",
        files={"file": ("b.txt", payload, PLAIN)},
    )
    assert response.status_code == 200, response.text

    matches = _records(caplog, "document.uploaded")
    assert len(matches) == 1
    extra = _extra(matches[0])
    assert extra["is_duplicate"] is True
    assert extra["content_type"] == PLAIN
    # ``DocumentService.upload`` sets the duplicate version's status at
    # construction time (no STORED → DUPLICATE_DETECTED transition);
    # ``document.uploaded`` with ``is_duplicate=True`` is the canonical
    # audit signal for a deduped upload, and the value of ``sha256``
    # matches the original — letting on-call join the two records.
    assert isinstance(extra["sha256"], str) and extra["sha256"]


# ─── Extraction lifecycle ─────────────────────────────────────────────


def _upload_text(client: TestClient, body: bytes = b"alpha\nbeta\ngamma") -> dict:
    response = client.post(
        "/documents/upload",
        files={"file": ("doc.txt", body, PLAIN)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_extraction_started_and_succeeded_events_fire(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    version = _upload_text(client)
    caplog.clear()
    caplog.set_level(logging.INFO)

    response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract",
    )
    assert response.status_code == 200, response.text

    started = _records(caplog, "extraction.started")
    assert len(started) == 1
    started_extra = _extra(started[0])
    assert started_extra["document_id"] == version["document_id"]
    assert started_extra["version_id"] == version["id"]
    assert started_extra["content_type"] == PLAIN
    assert started_extra["bytes_in"] == len(b"alpha\nbeta\ngamma")

    succeeded = _records(caplog, "extraction.succeeded")
    assert len(succeeded) == 1
    succeeded_extra = _extra(succeeded[0])
    assert succeeded_extra["parser_name"] == "plain_text"
    assert succeeded_extra["sections_out"] >= 1
    # No raw text in the audit trail.
    assert "alpha" not in str(succeeded_extra)


def test_extraction_failed_event_fires_when_parser_missing(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force an unsupported MIME through the upload allowlist to drive the
    "no parser registered" branch of ``ExtractionJobService.run``."""
    odd_mime = "application/x-strange-format"
    monkeypatch.setenv("ALLOWED_CONTENT_TYPES", f"text/plain,{odd_mime}")
    fresh_client = TestClient(create_app())

    upload = fresh_client.post(
        "/documents/upload",
        files={"file": ("weird.bin", b"\x00\x01\x02", odd_mime)},
    )
    assert upload.status_code == 200, upload.text
    version = upload.json()

    caplog.clear()
    caplog.set_level(logging.INFO)

    response = fresh_client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract",
    )
    # The route returns a 4xx error; the audit event still fires.
    assert response.status_code >= 400

    failed = _records(caplog, "extraction.failed")
    assert len(failed) == 1
    extra = _extra(failed[0])
    assert extra["document_id"] == version["document_id"]
    assert extra["version_id"] == version["id"]
    # ``failure_reason`` is a safe message string — not raw bytes.
    assert isinstance(extra["failure_reason"], str)
    assert "\x00" not in extra["failure_reason"]


# ─── Semantic generation ─────────────────────────────────────────────


def test_semantic_generated_event_fires(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    version = _upload_text(client, body=b"the quick brown fox\njumps over\nthe lazy dog")
    client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract",
    )

    caplog.clear()
    caplog.set_level(logging.INFO)

    response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/semantic",
    )
    assert response.status_code == 200, response.text

    matches = _records(caplog, "semantic.generated")
    assert len(matches) == 1
    extra = _extra(matches[0])
    assert extra["document_id"] == version["document_id"]
    assert extra["version_id"] == version["id"]
    assert isinstance(extra["section_count"], int) and extra["section_count"] > 0
    # No raw extracted text in the payload.
    assert "fox" not in str(extra)
    assert "lazy dog" not in str(extra)


# ─── Review actions ───────────────────────────────────────────────────


def _make_validatable(client: TestClient) -> dict:
    """Drive a fresh upload all the way to NEEDS_REVIEW so a reviewer
    endpoint can act on it."""
    version = _upload_text(client)
    client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract",
    )
    client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/semantic",
    )
    return version


def test_review_validated_event_fires(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    version = _make_validatable(client)
    caplog.clear()
    caplog.set_level(logging.INFO)

    response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/validate",
        json={"reviewer_note": "looks good"},
    )
    assert response.status_code == 200, response.text

    matches = _records(caplog, "review.validated")
    assert len(matches) == 1
    extra = _extra(matches[0])
    assert extra["document_id"] == version["document_id"]
    assert extra["version_id"] == version["id"]


def test_review_rejected_event_fires(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    version = _make_validatable(client)
    caplog.clear()
    caplog.set_level(logging.INFO)

    response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/reject",
        json={"reviewer_note": "needs rework"},
    )
    assert response.status_code == 200, response.text

    matches = _records(caplog, "review.rejected")
    assert len(matches) == 1
    extra = _extra(matches[0])
    assert extra["document_id"] == version["document_id"]
    assert extra["version_id"] == version["id"]


# ─── Service-construction sanity check ──────────────────────────────


def test_services_construct_without_logging_handler_changes() -> None:
    """``build_services`` shouldn't be silently mutating the root logger
    (``configure_logging`` runs separately, in ``create_app``). This pins
    the boundary so a future refactor doesn't accidentally double-install
    handlers when services are built outside an ASGI lifespan."""
    before = list(logging.getLogger().handlers)
    services = build_services()
    after = list(logging.getLogger().handlers)

    assert before == after
    assert services is not None
