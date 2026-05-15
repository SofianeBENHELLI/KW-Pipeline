"""Tests for the ``actor`` field on user-initiated audit events (#91, ADR-019).

The 2026-05-14 progress plan called out ``actor.id`` audit-event
backfill as the residual #91 sub-item once the route-scope predicate
sweep had landed. Audit events that come from a human caller should
carry ``actor`` in their ``extra=`` payload so the admin viewer's
actor filter (and any downstream forensics) can attribute the action.

This file pins the ``document.uploaded`` path. Status-change emits
(``document.status_changed``) currently flow through both human-driven
routes (validate / reject) and worker-driven paths (extraction
worker, recovery, semantic output service). The helper now accepts
``actor`` but the threading from non-upload routes is queued as a
follow-up — this PR ships the upload path so the contract is
demonstrated end-to-end with a green test.
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
    reserved = set(
        vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    ) | {"message", "asctime"}
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
        "/documents/upload-batch",
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
