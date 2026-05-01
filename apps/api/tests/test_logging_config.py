"""Tests for the structured-logging configuration (issue #42).

Two surfaces are exercised:

* :func:`app.logging_config.configure_logging` — does it install one
  root handler whose formatter matches the requested shape, and is it
  idempotent across multiple calls?
* The audit-trail emissions throughout the request path — uploading a
  document, extracting it, generating a semantic doc, and validating
  it produces a known sequence of named events with the documented
  ``extra`` keys.

The audit test inspects ``caplog.records`` (pytest's built-in fixture)
rather than re-parsing stdout: the structured ``extra`` dict is what
on-call reviewers will grep, and ``caplog`` keeps it untouched on the
record itself.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.logging_config import (
    _HARVESTER_HANDLER_FLAG,
    JsonFormatter,
    configure_logging,
)
from app.main import create_app
from app.settings import Settings


def _harvester_handlers() -> list[logging.Handler]:
    """Return the handlers ``configure_logging`` is responsible for."""
    return [h for h in logging.getLogger().handlers if getattr(h, _HARVESTER_HANDLER_FLAG, False)]


@pytest.fixture(autouse=True)
def _reset_harvester_handlers():
    """Detach Harvester handlers between tests so they don't leak across.

    Pytest's own caplog handler is left alone — stripping it would
    break the ``caplog`` fixture in subsequent tests.
    """
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HARVESTER_HANDLER_FLAG, False):
            root.removeHandler(handler)
    root.setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def test_json_format_installs_json_formatter(self):
        configure_logging(Settings(log_format="json", log_level="INFO"))

        installed = _harvester_handlers()
        assert len(installed) == 1
        assert isinstance(installed[0].formatter, JsonFormatter)
        assert logging.getLogger().level == logging.INFO

    def test_text_format_does_not_crash(self):
        # The contract is just "doesn't blow up"; the formatter shape
        # is stdlib's, which we don't pin exactly.
        configure_logging(Settings(log_format="text", log_level="INFO"))

        installed = _harvester_handlers()
        assert len(installed) == 1
        # Smoke: emitting a record with the installed formatter works.
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        rendered = installed[0].formatter.format(record)
        assert "hello" in rendered

    def test_idempotent_across_repeat_calls(self):
        configure_logging(Settings(log_format="json"))
        configure_logging(Settings(log_format="json"))

        # Two calls in a row don't stack — only one Harvester handler
        # remains attached to the root.
        assert len(_harvester_handlers()) == 1

    def test_does_not_strip_foreign_handlers(self):
        root = logging.getLogger()
        sentinel = logging.NullHandler()
        root.addHandler(sentinel)
        try:
            configure_logging(Settings(log_format="json"))
            # The foreign handler is preserved; the Harvester handler
            # is added alongside it. Stripping foreign handlers would
            # break pytest's caplog and any operator-attached sinks.
            assert sentinel in root.handlers
            assert len(_harvester_handlers()) == 1
        finally:
            root.removeHandler(sentinel)

    def test_unknown_level_falls_back_to_info(self):
        configure_logging(Settings(log_format="json", log_level="NOT_A_LEVEL"))
        assert logging.getLogger().level == logging.INFO

    def test_lowercase_level_name_is_accepted(self):
        configure_logging(Settings(log_format="json", log_level="debug"))
        assert logging.getLogger().level == logging.DEBUG


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


class TestJsonFormatter:
    def _make_record(self, **extra) -> logging.LogRecord:
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="some.event",
            args=(),
            exc_info=None,
        )
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_emits_one_json_object_per_record(self):
        record = self._make_record(document_id="doc-1", version_id="v-1")

        rendered = JsonFormatter().format(record)
        parsed = json.loads(rendered)

        assert parsed["event"] == "some.event"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "app.test"
        assert parsed["document_id"] == "doc-1"
        assert parsed["version_id"] == "v-1"
        assert parsed["timestamp"].endswith("Z")

    def test_coerces_non_jsonable_values_to_string(self):
        class Opaque:
            def __repr__(self) -> str:
                return "<opaque>"

        record = self._make_record(weird=Opaque(), nested=[1, {"a": Opaque()}])
        parsed = json.loads(JsonFormatter().format(record))

        assert parsed["weird"] == "<opaque>"
        assert parsed["nested"] == [1, {"a": "<opaque>"}]

    def test_includes_exc_info_when_set(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="app.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="oops",
                args=(),
                exc_info=sys.exc_info(),
            )

        parsed = json.loads(JsonFormatter().format(record))
        assert "ValueError: boom" in parsed["exc_info"]


# ---------------------------------------------------------------------------
# Audit-trail emissions across an upload-extract-semantic-validate cycle
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    return TestClient(create_app())


def _drive_full_cycle(client: TestClient) -> dict:
    upload = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"text body", "text/plain")},
    ).json()
    client.post(f"/documents/{upload['document_id']}/versions/{upload['id']}/extract")
    client.post(f"/documents/{upload['document_id']}/versions/{upload['id']}/semantic")
    client.post(
        f"/documents/{upload['document_id']}/versions/{upload['id']}/validate",
        json={"reviewer_note": "ok"},
    )
    return upload


class TestAuditTrail:
    def test_full_cycle_emits_expected_event_sequence(self, caplog):
        # Build the app first so ``configure_logging`` runs with the
        # default ``text`` shape; pytest's caplog handler is preserved
        # by ``configure_logging`` (only the previous Harvester handler
        # is removed) so structured records are captured here.
        client = _client()
        caplog.set_level(logging.INFO)

        version = _drive_full_cycle(client)

        events = [
            (record.name, record.getMessage())
            for record in caplog.records
            if record.getMessage().startswith(("document.", "extraction.", "semantic.", "review."))
        ]
        names = [event for _, event in events]

        # Upload → STORED → EXTRACTING → EXTRACTED → NEEDS_REVIEW → VALIDATED.
        assert "document.uploaded" in names
        assert "extraction.started" in names
        assert "extraction.succeeded" in names
        assert "semantic.generated" in names
        assert "review.validated" in names

        # The status_changed event fires on every FSM move.
        status_records = [r for r in caplog.records if r.getMessage() == "document.status_changed"]
        assert len(status_records) >= 4
        for r in status_records:
            assert r.document_id == version["document_id"]
            assert r.version_id == version["id"]
            # ``from`` and ``to`` are reserved Python keywords, so the
            # only way to read them off the record is via ``getattr``.
            assert hasattr(r, "from")
            assert hasattr(r, "to")

    def test_uploaded_event_carries_documented_keys(self, caplog):
        client = _client()
        caplog.set_level(logging.INFO)

        client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"text body", "text/plain")},
        )

        record = next(r for r in caplog.records if r.getMessage() == "document.uploaded")
        for key in (
            "document_id",
            "version_id",
            "version_number",
            "sha256",
            "bytes",
            "content_type",
            "filename",
            "is_duplicate",
        ):
            assert hasattr(record, key), f"missing key: {key}"
        assert record.is_duplicate is False
        assert record.filename == "policy.txt"

    def test_idempotency_replay_logs_event(self, caplog):
        client = _client()
        caplog.set_level(logging.INFO)
        headers = {"Idempotency-Key": "stable-key"}

        client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"text body", "text/plain")},
            headers=headers,
        )
        client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"text body", "text/plain")},
            headers=headers,
        )

        replay = [r for r in caplog.records if r.getMessage() == "idempotency.replayed"]
        assert len(replay) == 1
        assert replay[0].route == "/documents/upload"
        assert replay[0].idempotency_key == "stable-key"

    def test_extraction_failure_logs_failed_event(self, caplog):
        client = _client()
        caplog.set_level(logging.INFO)
        # Empty .txt produces "No extractable content".
        upload = client.post(
            "/documents/upload",
            files={"file": ("empty.txt", b" ", "text/plain")},
        ).json()
        client.post(f"/documents/{upload['document_id']}/versions/{upload['id']}/extract")

        failed = [r for r in caplog.records if r.getMessage() == "extraction.failed"]
        assert len(failed) == 1
        assert failed[0].failure_reason
        assert failed[0].document_id == upload["document_id"]

    def test_semantic_cached_event_on_repeat_call(self, caplog):
        client = _client()
        upload = client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"text body", "text/plain")},
        ).json()
        client.post(f"/documents/{upload['document_id']}/versions/{upload['id']}/extract")
        client.post(f"/documents/{upload['document_id']}/versions/{upload['id']}/semantic")

        caplog.clear()
        caplog.set_level(logging.INFO)
        # Second call returns the cached payload.
        client.post(f"/documents/{upload['document_id']}/versions/{upload['id']}/semantic")
        cached = [r for r in caplog.records if r.getMessage() == "semantic.cached"]
        assert len(cached) == 1
        assert cached[0].document_id == upload["document_id"]

    def test_review_rejected_event(self, caplog):
        client = _client()
        caplog.set_level(logging.INFO)
        upload = client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"text body", "text/plain")},
        ).json()
        client.post(f"/documents/{upload['document_id']}/versions/{upload['id']}/extract")
        client.post(f"/documents/{upload['document_id']}/versions/{upload['id']}/semantic")
        client.post(
            f"/documents/{upload['document_id']}/versions/{upload['id']}/reject",
            json={"reviewer_note": "not enough citations"},
        )

        rejected = [r for r in caplog.records if r.getMessage() == "review.rejected"]
        assert len(rejected) == 1
        assert rejected[0].reviewer_note == "not enough citations"
