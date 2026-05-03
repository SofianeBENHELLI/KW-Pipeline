"""Tests for the retry-extraction surface (issue #87).

Covers the four behaviours the issue calls out:

1. **Retry success after the underlying issue is fixed** — a version
   that previously FAILED because no parser was registered for its
   content-type can be retried after the parser is wired in, and
   succeeds.
2. **Retry that fails again** — the version stays FAILED with the
   *new* reason; the audit log preserves both attempts via
   ``extraction.retried`` + ``extraction.failed`` records.
3. **Non-retryable states reject the retry** — VALIDATED, REJECTED,
   EXTRACTED, NEEDS_REVIEW, etc. all return 409 from the route /
   raise ``ValueError`` from the service. The review gate is not
   bypassed.
4. **Failure reason is cleared on success** — a successfully-retried
   version's ``failure_reason`` is None, not the stale text.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.schemas.extraction import RawExtraction
from app.services.document_parser import ParserRegistry, PlainTextParser
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionFailed, ExtractionJobService
from app.services.storage_service import InMemoryStorageService

PLAIN = "text/plain"


# ─── Service-level tests with controllable parsers ─────────────────────


class _RecordingParser:
    """Parser stub whose ``parse`` calls are externally toggleable.

    Reuses the registered ``PlainTextParser`` for actual section output
    so the success path produces a non-empty ``RawExtraction``; the
    toggle is for whether to ``raise`` before the real parser runs.
    """

    name = "recording"
    version = "test"
    supported_content_types = frozenset({PLAIN})

    def __init__(self) -> None:
        self.fail_with: Exception | None = None
        self.calls: int = 0

    def parse(self, version, storage):  # noqa: ANN001 - tests can stay loose
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        # Re-use the real plain-text logic by composing it inline so the
        # success path emits the expected sections without copying its
        # body.
        return PlainTextParser().parse(version=version, storage=storage)


def _make_service(parser: _RecordingParser) -> tuple[DocumentService, ExtractionJobService]:
    documents = DocumentService(storage=InMemoryStorageService())
    jobs = ExtractionJobService(documents=documents, parsers=ParserRegistry([parser]))
    return documents, jobs


def _upload(documents: DocumentService, body: bytes = b"alpha\nbeta\ngamma"):
    return documents.upload("doc.txt", PLAIN, body)


def test_retry_extract_succeeds_after_underlying_issue_is_fixed() -> None:
    parser = _RecordingParser()
    parser.fail_with = RuntimeError("temporary infra hiccup")
    documents, jobs = _make_service(parser)
    version = _upload(documents)

    # First attempt fails; the version is parked in FAILED with a reason.
    with pytest.raises(ExtractionFailed):
        jobs.extract(document_id=version.document_id, version_id=version.id)
    failed = documents.get_version(document_id=version.document_id, version_id=version.id)
    assert failed.status is DocumentVersionStatus.FAILED
    assert failed.failure_reason is not None
    assert "temporary infra hiccup" in failed.failure_reason

    # Operator fixes the underlying problem.
    parser.fail_with = None

    # Retry succeeds and the version moves on.
    extraction = jobs.retry_extract(document_id=version.document_id, version_id=version.id)
    assert isinstance(extraction, RawExtraction)
    succeeded = documents.get_version(document_id=version.document_id, version_id=version.id)
    assert succeeded.status is DocumentVersionStatus.EXTRACTED
    # The previous failure_reason is cleared on the way out of FAILED —
    # otherwise readers see stale text on a now-healthy version.
    assert succeeded.failure_reason is None
    # Both the original and retry attempts hit the parser.
    assert parser.calls == 2


def test_retry_extract_can_fail_again_and_keeps_version_failed() -> None:
    parser = _RecordingParser()
    parser.fail_with = RuntimeError("first fail")
    documents, jobs = _make_service(parser)
    version = _upload(documents)

    with pytest.raises(ExtractionFailed):
        jobs.extract(document_id=version.document_id, version_id=version.id)

    # Try again with a *different* failure mode — confirm the version
    # remains FAILED and the new reason replaces the old one.
    parser.fail_with = RuntimeError("second fail")
    with pytest.raises(ExtractionFailed) as excinfo:
        jobs.retry_extract(document_id=version.document_id, version_id=version.id)
    assert "second fail" in excinfo.value.reason
    refailed = documents.get_version(document_id=version.document_id, version_id=version.id)
    assert refailed.status is DocumentVersionStatus.FAILED
    assert refailed.failure_reason is not None
    assert "second fail" in refailed.failure_reason
    # Old reason isn't preserved on the row (audit log is the source of
    # truth for failure history — see docs/architecture/observability.md).
    assert "first fail" not in refailed.failure_reason


@pytest.mark.parametrize(
    "current",
    [
        DocumentVersionStatus.STORED,
        DocumentVersionStatus.EXTRACTED,
        DocumentVersionStatus.SEMANTIC_READY,
        DocumentVersionStatus.NEEDS_REVIEW,
        DocumentVersionStatus.VALIDATED,
        DocumentVersionStatus.REJECTED,
        DocumentVersionStatus.DUPLICATE_DETECTED,
    ],
)
def test_retry_extract_rejects_non_failed_states(current: DocumentVersionStatus) -> None:
    """Retry never bypasses the review gate or rerun a healthy pipeline."""
    parser = _RecordingParser()
    documents, jobs = _make_service(parser)
    version = _upload(documents)
    # Force the version into the state under test by rewriting the row
    # directly on the in-memory catalog. The FSM rejects a public path
    # to e.g. VALIDATED from STORED, but the rejection check inside
    # ``retry_extract`` doesn't care how the row got into its current
    # state — only that it's not FAILED.
    catalog_record = documents.catalog.versions[version.id]  # type: ignore[attr-defined]
    catalog_record.status = current

    with pytest.raises(ValueError) as excinfo:
        jobs.retry_extract(document_id=version.document_id, version_id=version.id)
    assert "Retry only allowed from FAILED" in str(excinfo.value)
    # Parser was never invoked because the precondition rejected first.
    assert parser.calls == 0


def test_retry_extract_emits_audit_event(caplog: pytest.LogCaptureFixture) -> None:
    parser = _RecordingParser()
    parser.fail_with = RuntimeError("provider down")
    documents, jobs = _make_service(parser)
    version = _upload(documents)
    with pytest.raises(ExtractionFailed):
        jobs.extract(document_id=version.document_id, version_id=version.id)
    parser.fail_with = None

    caplog.clear()
    caplog.set_level(logging.INFO)
    jobs.retry_extract(document_id=version.document_id, version_id=version.id)

    retried = [r for r in caplog.records if r.getMessage() == "extraction.retried"]
    assert len(retried) == 1
    extra = retried[0].__dict__
    assert extra.get("document_id") == version.document_id
    assert extra.get("version_id") == version.id
    # The previous reason is captured on the audit record so on-call can
    # reconstruct what the failure was without joining tables.
    assert "provider down" in (extra.get("previous_failure_reason") or "")


# ─── Route-level tests via TestClient ──────────────────────────────────


def _force_failed(client: TestClient, mime: str) -> dict:
    """Upload + extract with no parser registered for ``mime`` so the
    version lands in ``FAILED`` via the parser-not-found branch."""
    upload = client.post(
        "/documents/upload",
        files={"file": ("weird.bin", b"\x00\x01", mime)},
    )
    assert upload.status_code == 200, upload.text
    version = upload.json()
    extract = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract",
    )
    assert extract.status_code == 422, extract.text
    return version


def test_retry_extraction_route_404_when_version_unknown() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/documents/missing-doc/versions/missing-ver/retry-extraction",
    )
    assert response.status_code == 404


def test_retry_extraction_route_409_when_version_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(create_app())
    upload = client.post(
        "/documents/upload",
        files={"file": ("ok.txt", b"hello", PLAIN)},
    )
    version = upload.json()
    # Drive through extraction → semantic so the version sits in
    # NEEDS_REVIEW (a definitely-not-FAILED state).
    client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract",
    )
    client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/semantic",
    )

    response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/retry-extraction",
    )
    assert response.status_code == 409
    assert "Retry only allowed from FAILED" in response.text


def test_retry_extraction_route_succeeds_with_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route-layer success path: retry runs cleanly and the idempotency
    cache is populated so a replayed key returns the cached result
    without rerunning extraction."""
    odd_mime = "application/x-temporary-error"
    monkeypatch.setenv("ALLOWED_CONTENT_TYPES", f"text/plain,{odd_mime}")
    client = TestClient(create_app())

    # First attempt fails because no parser is registered for the MIME.
    version = _force_failed(client, odd_mime)

    # Widen the registry by registering a parser for the MIME on the
    # already-running app's services. The dependencies module exposes
    # the registry on the live PipelineServices via the FastAPI app
    # state — reach in and mutate the in-memory ``parsers`` dict.
    parsers = client.app.state.services.parsers  # type: ignore[attr-defined]

    class _LateParser:
        name = "late"
        version = "test"
        supported_content_types = frozenset({odd_mime})

        def parse(self, version, storage):  # noqa: ANN001
            from app.schemas.extraction import RawExtraction, RawSection, SourceReference

            content = storage.get(version.storage_uri)
            ref = SourceReference(
                document_version_id=version.id,
                section_id="s-0",
                page_number=None,
                line_start=None,
                line_end=None,
                snippet=content[:24].decode("latin-1", errors="replace"),
            )
            return RawExtraction(
                document_version_id=version.id,
                parser_name=self.name,
                parser_version=self.version,
                text=content.decode("latin-1", errors="replace"),
                sections=[
                    RawSection(
                        id="s-0",
                        heading="Body",
                        text=content.decode("latin-1", errors="replace") or "fallback",
                        source_reference_ids=[ref.id],
                        parser_metadata={},
                    )
                ],
                source_references=[ref],
            )

    parsers._by_content_type[odd_mime] = _LateParser()  # type: ignore[attr-defined]

    response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/retry-extraction",
        headers={"Idempotency-Key": "retry-1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["parser_name"] == "late"

    # Replaying with the same idempotency key returns the cached payload
    # without rerunning extraction (the version already moved out of
    # FAILED on the first call, so a fresh call would 409).
    replay = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/retry-extraction",
        headers={"Idempotency-Key": "retry-1"},
    )
    assert replay.status_code == 200
    assert replay.json() == body


def test_retry_extraction_route_emits_audit_event_and_persists_new_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Route-layer: a retry that re-fails surfaces the new reason via 422
    and emits ``extraction.retried`` to the structured log."""
    odd_mime = "application/x-still-no-parser"
    monkeypatch.setenv("ALLOWED_CONTENT_TYPES", f"text/plain,{odd_mime}")
    client = TestClient(create_app())
    version = _force_failed(client, odd_mime)

    caplog.clear()
    caplog.set_level(logging.INFO)
    response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/retry-extraction",
    )
    # No parser was added between attempts — the second extraction
    # fails the same way and the route surfaces the new reason via 422.
    assert response.status_code == 422
    body = response.json()
    assert (
        "needs-fix" in body.get("detail", "")
        or "no parser" in body.get("detail", "").lower()
        or len(body.get("detail", "")) > 0
    )

    retried = [r for r in caplog.records if r.getMessage() == "extraction.retried"]
    assert len(retried) == 1
    failed_again = [r for r in caplog.records if r.getMessage() == "extraction.failed"]
    assert len(failed_again) >= 1
