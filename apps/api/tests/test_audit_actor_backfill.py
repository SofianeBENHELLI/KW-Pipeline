"""Tests for the ``actor`` field on user-initiated audit events (#91, ADR-019).

The 2026-05-14 progress plan called out ``actor.id`` audit-event
backfill as the residual #91 sub-item once the route-scope predicate
sweep had landed. Audit events that come from a human caller should
carry ``actor`` in their ``extra=`` payload so the admin viewer's
actor filter (and any downstream forensics) can attribute the action.

This file pins:

- ``document.uploaded`` — single + batch upload routes (PR #460).
- ``document.status_changed`` — validate / reject / demote flows
  threaded through ``DocumentService._record_review`` and
  ``mark_demoted_to_review`` (PR #462).
- ``extraction.started`` / ``extraction.succeeded`` /
  ``extraction.failed`` / ``extraction.retried`` — the four
  ``extraction.*`` events, threaded via the inline path AND the
  async ``ExtractionRequest`` → worker → ``ExtractionJobService``
  chain.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

PLAIN = "text/plain"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _records(caplog: pytest.LogCaptureFixture, event_name: str):
    return [r for r in caplog.records if r.msg == event_name]


def _extra(record: logging.LogRecord) -> dict:
    """Pull ``extra=`` back out of a record by subtracting the reserved
    ``LogRecord`` attribute set. Mirrors what ``AuditLogHandler`` does
    when projecting a record into the audit payload."""
    reserved = set(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {
        "message",
        "asctime",
    }
    return {k: v for k, v in vars(record).items() if k not in reserved}


# ─── document.uploaded carries actor ───────────────────────────────────


def test_single_upload_route_emits_actor_on_document_uploaded(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The single ``POST /documents/upload`` route threads
    ``current_user.id`` into ``DocumentService.upload_stream(actor=…)``
    which surfaces as ``extra['actor']`` on ``document.uploaded``."""
    caplog.set_level(logging.INFO)

    response = client.post(
        "/documents/upload",
        files={"file": ("memo.txt", b"actor-bearing upload", PLAIN)},
    )
    assert response.status_code == 200, response.text

    matches = _records(caplog, "document.uploaded")
    assert len(matches) == 1
    extra = _extra(matches[0])
    # Default ``KW_AUTH_MODE=dev`` returns ``current_user.id="dev"``.
    assert extra.get("actor") == "dev"


def test_batch_upload_route_emits_actor_on_every_document_uploaded(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The bulk ``POST /documents/upload-batch`` route threads the
    same ``current_user.id`` through every per-file upload so each
    of the per-file ``document.uploaded`` audit events carries an
    actor (#82 + #91 backfill)."""
    caplog.set_level(logging.INFO)

    response = client.post(
        "/documents/upload/batch",
        files=[
            ("files", ("a.txt", b"first batch file", PLAIN)),
            ("files", ("b.txt", b"second batch file", PLAIN)),
        ],
    )
    assert response.status_code == 200, response.text

    matches = _records(caplog, "document.uploaded")
    assert len(matches) == 2
    for record in matches:
        extra = _extra(record)
        assert extra.get("actor") == "dev"


# ─── Defensive: actor=None keeps the key out of the payload ───────────


def test_document_service_upload_omits_actor_key_when_none() -> None:
    """A direct ``DocumentService.upload(...)`` call without an actor
    leaves the ``actor`` key out of the payload entirely — system
    callers (worker, demo loader, scripts) don't pollute the audit
    table with ``actor: null`` rows. The :func:`event_actor` projection
    in the audit store reads only ``str`` values, so ``None`` would
    already be ignored — but omitting the key keeps the audit JSON
    cleaner for direct grep / jq workflows."""
    from app.dependencies import build_services

    services = build_services()
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture(level=logging.INFO)
    logging.getLogger().addHandler(handler)
    try:
        services.documents.upload(
            filename="system.txt",
            content_type=PLAIN,
            content=b"system upload",
        )
    finally:
        logging.getLogger().removeHandler(handler)

    matches = [r for r in captured if r.msg == "document.uploaded"]
    assert matches, "document.uploaded should fire on a successful upload"
    # ``actor`` is absent from the extras when the call didn't pass one.
    assert not hasattr(matches[0], "actor")


# ─── document.status_changed carries actor on validate / reject / demote ──


def _land_version_in_needs_review(services) -> tuple[str, str]:
    """Drive a fresh upload through extract + semantic so the version
    sits at NEEDS_REVIEW — the precondition for validate / reject. Reused
    pattern from ``test_review_service.py``."""
    version = services.documents.upload(
        filename="policy.txt",
        content_type=PLAIN,
        content=b"Hello world. This is a tiny test fixture.",
    )
    services.extraction_jobs.extract(document_id=version.document_id, version_id=version.id)
    services.semantic_outputs.generate(document_id=version.document_id, version_id=version.id)
    return version.document_id, version.id


def test_validate_route_emits_actor_on_document_status_changed(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``POST /documents/{id}/versions/{vid}/validate`` flows through
    ``ReviewService.handle_validation → mark_validated → _record_review
    → _log_status_changed(actor=…)``. The actor lands on the
    ``document.status_changed`` event for the NEEDS_REVIEW → VALIDATED
    transition."""
    from app.dependencies import build_services
    from app.main import create_app

    services = build_services()
    document_id, version_id = _land_version_in_needs_review(services)
    fresh_client = TestClient(create_app(services=services))

    caplog.clear()
    caplog.set_level(logging.INFO)
    response = fresh_client.post(
        f"/documents/{document_id}/versions/{version_id}/validate",
        json={"reviewer_note": "all good"},
    )
    assert response.status_code == 200, response.text

    matches = _records(caplog, "document.status_changed")
    # One status_changed event for the NEEDS_REVIEW → VALIDATED transition.
    transition = next(
        (m for m in matches if _extra(m).get("to") == "VALIDATED"),
        None,
    )
    assert transition is not None, "expected a NEEDS_REVIEW → VALIDATED transition"
    extra = _extra(transition)
    assert extra.get("actor") == "dev"
    # The review.validated companion event keeps its actor too.
    review_events = _records(caplog, "review.validated")
    assert review_events and _extra(review_events[-1]).get("actor") == "dev"


def test_reject_route_emits_actor_on_document_status_changed(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Same shape as the validate test, on the rejection path."""
    from app.dependencies import build_services
    from app.main import create_app

    services = build_services()
    document_id, version_id = _land_version_in_needs_review(services)
    fresh_client = TestClient(create_app(services=services))

    caplog.clear()
    caplog.set_level(logging.INFO)
    response = fresh_client.post(
        f"/documents/{document_id}/versions/{version_id}/reject",
        json={"reviewer_note": "wrong document"},
    )
    assert response.status_code == 200, response.text

    matches = _records(caplog, "document.status_changed")
    transition = next(
        (m for m in matches if _extra(m).get("to") == "REJECTED"),
        None,
    )
    assert transition is not None, "expected a NEEDS_REVIEW → REJECTED transition"
    assert _extra(transition).get("actor") == "dev"


def test_demote_route_emits_actor_on_document_status_changed(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Demote route (``POST /demote-to-review``) — VALIDATED → NEEDS_REVIEW
    transition's ``document.status_changed`` event carries the actor."""
    from app.dependencies import build_services
    from app.main import create_app

    services = build_services()
    document_id, version_id = _land_version_in_needs_review(services)
    # Drive to VALIDATED first so the demote precondition is met.
    services.review.handle_validation(
        document_id=document_id,
        version_id=version_id,
        reviewer_note="seed",
        actor="dev",
    )
    fresh_client = TestClient(create_app(services=services))

    caplog.clear()
    caplog.set_level(logging.INFO)
    response = fresh_client.post(
        f"/documents/{document_id}/versions/{version_id}/demote-to-review",
        json={"reviewer_note": "second look"},
    )
    assert response.status_code == 200, response.text

    matches = _records(caplog, "document.status_changed")
    transition = next(
        (m for m in matches if _extra(m).get("to") == "NEEDS_REVIEW"),
        None,
    )
    assert transition is not None, "expected a VALIDATED → NEEDS_REVIEW transition"
    assert _extra(transition).get("actor") == "dev"


# ─── extraction.* events carry actor (inline + async paths) ───────────


def test_inline_extract_route_emits_actor_on_extraction_started_and_succeeded(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``POST /documents/{id}/versions/{vid}/extract`` (inline mode)
    threads ``current_user.id`` through ``_run_inline_extract`` →
    ``ExtractionJobService.extract(actor=…)`` → the four
    ``extraction.*`` event emissions. Default
    ``KW_EXTRACTION_INLINE=true`` means this exercise covers the
    synchronous request path."""
    # Upload first to land the version at STORED.
    upload = client.post(
        "/documents/upload",
        files={"file": ("doc.txt", b"hello extract", PLAIN)},
    )
    assert upload.status_code == 200, upload.text
    version = upload.json()

    caplog.clear()
    caplog.set_level(logging.INFO)
    response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract",
    )
    assert response.status_code == 200, response.text

    started = _records(caplog, "extraction.started")
    assert started and _extra(started[-1]).get("actor") == "dev"
    succeeded = _records(caplog, "extraction.succeeded")
    assert succeeded and _extra(succeeded[-1]).get("actor") == "dev"


def test_inline_retry_route_emits_actor_on_extraction_retried(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drive a version to FAILED via a missing-parser path, then hit
    the retry route. The ``extraction.retried`` event carries the
    actor; the inner ``extraction.started`` of the retry attempt does
    too."""
    from app.dependencies import build_services
    from app.main import create_app

    # Use a fresh services + client so we can swap an "unknown" content
    # type without re-uploading through the gate.
    services = build_services()
    fresh_client = TestClient(create_app(services=services))
    # Land a STORED version then force it to FAILED by writing the
    # status directly — this skips the unsupported-type detection
    # which would also do.
    from app.models.document import DocumentVersionStatus

    version = services.documents.upload(
        filename="retryable.txt",
        content_type=PLAIN,
        content=b"retry me",
    )
    services.documents.mark_failed(
        version.document_id, version.id, "first attempt failed (test setup)"
    )

    caplog.clear()
    caplog.set_level(logging.INFO)
    response = fresh_client.post(
        f"/documents/{version.document_id}/versions/{version.id}/retry-extraction",
    )
    assert response.status_code == 200, response.text

    retried = _records(caplog, "extraction.retried")
    assert retried and _extra(retried[-1]).get("actor") == "dev"
    # The inner extract() call re-fires ``extraction.started`` with
    # the same actor — confirms the actor flows through the chain.
    started = _records(caplog, "extraction.started")
    assert started and _extra(started[-1]).get("actor") == "dev"
    # And the second attempt succeeded.
    final = services.documents.get_version(document_id=version.document_id, version_id=version.id)
    assert final.status == DocumentVersionStatus.EXTRACTED


def test_extraction_request_carries_actor_through_to_worker() -> None:
    """``ExtractionRequest`` is the seam between route enqueue and
    worker dequeue. Pin that the dataclass round-trips ``actor`` and
    that the worker reads it back when it dispatches into the
    ``ExtractionJobService``. (We don't spin up a worker here — the
    contract is the dataclass field; ``ExtractionWorker._handle_one``
    is exercised by the existing async-route test.)"""
    from app.services.extraction_worker import ExtractionRequest

    req = ExtractionRequest(document_id="doc-1", version_id="ver-1", actor="ada")
    assert req.actor == "ada"
    # Default keeps None so legacy callers stay correct.
    req_legacy = ExtractionRequest(document_id="doc-2", version_id="ver-2")
    assert req_legacy.actor is None
