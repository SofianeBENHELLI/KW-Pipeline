"""Tests for the boot-time stuck-extraction recovery (ADR-006 §5).

The helper runs once on app boot via the lifespan hook in
:mod:`app.main`. The integration coverage of the lifespan wiring lives
in ``test_extraction_worker_lifespan.py``; this module drives the
recovery helper directly with a hand-built ``PipelineServices`` so the
control-flow branches are exercised in isolation.
"""

from __future__ import annotations

from app.dependencies import build_services
from app.models.document import DocumentVersionStatus
from app.services.extraction_recovery import recover_stuck_extractions


def _build(*, extraction_inline: bool):
    """Construct a fresh in-memory ``PipelineServices`` and override the
    extraction_inline flag on its settings model. Returns the services
    plus their document service for fixture setup."""
    services = build_services()
    # ``Settings`` is a frozen-ish Pydantic model in practice but this
    # repo uses regular pydantic-settings (mutable). Override directly.
    object.__setattr__(services.settings, "extraction_inline", extraction_inline)
    return services


def _make_extracting_version(services, *, filename: str = "stuck.txt") -> tuple[str, str]:
    """Upload + push the version into ``EXTRACTING`` so recovery has
    something to act on. Returns ``(document_id, version_id)``.

    Body is derived from the filename so multiple calls in a single
    test produce distinct sha256s — otherwise the second upload trips
    duplicate detection and lands in DUPLICATE_DETECTED instead of
    STORED.
    """
    documents = services.documents
    body = f"content for {filename}".encode()
    version = documents.upload(filename, "text/plain", body)
    documents.update_status(version.document_id, version.id, DocumentVersionStatus.EXTRACTING)
    return version.document_id, version.id


def test_inline_mode_skips_recovery_entirely() -> None:
    services = _build(extraction_inline=True)
    document_id, version_id = _make_extracting_version(services)

    # Inline mode short-circuits — the version stays EXTRACTING and the
    # helper returns 0. (In real life nothing would have left a version
    # in EXTRACTING under inline mode anyway; we set it up artificially
    # to prove the helper doesn't touch it.)
    recovered = recover_stuck_extractions(services)
    assert recovered == 0
    assert (
        services.documents.get_version(document_id, version_id).status
        == DocumentVersionStatus.EXTRACTING
    )


def test_recovers_one_stuck_version() -> None:
    services = _build(extraction_inline=False)
    document_id, version_id = _make_extracting_version(services)

    recovered = recover_stuck_extractions(services)
    assert recovered == 1

    failed = services.documents.get_version(document_id, version_id)
    assert failed.status == DocumentVersionStatus.FAILED
    assert failed.failure_reason is not None
    assert "process restart" in failed.failure_reason.lower()


def test_recovers_multiple_versions_across_documents() -> None:
    services = _build(extraction_inline=False)
    targets = [_make_extracting_version(services, filename=f"stuck-{i}.txt") for i in range(3)]

    recovered = recover_stuck_extractions(services)
    assert recovered == 3
    for document_id, version_id in targets:
        version = services.documents.get_version(document_id, version_id)
        assert version.status == DocumentVersionStatus.FAILED


def test_returns_zero_when_no_versions_are_stuck() -> None:
    services = _build(extraction_inline=False)
    # One STORED version (not EXTRACTING) → recovery shouldn't touch it.
    version = services.documents.upload("clean.txt", "text/plain", b"clean")

    recovered = recover_stuck_extractions(services)
    assert recovered == 0
    assert (
        services.documents.get_version(version.document_id, version.id).status
        == DocumentVersionStatus.STORED
    )


def test_recover_swallows_list_documents_failure(monkeypatch):
    """Defensive branch: a transient catalog failure during the scan
    is logged and swallowed; the helper returns 0 instead of raising
    so the periodic loop keeps going."""
    from app.dependencies import build_services
    from app.services.extraction_recovery import recover_stuck_extractions

    services = build_services()
    object.__setattr__(services.settings, "extraction_inline", False)

    def _boom(*args, **kwargs):
        raise RuntimeError("catalog unreachable")

    monkeypatch.setattr(services.documents.catalog, "list_documents", _boom)

    assert recover_stuck_extractions(services) == 0


def test_recover_continues_when_per_version_mark_failed_raises(monkeypatch):
    """Defensive branch: a per-version ``mark_failed`` failure is
    logged and the loop continues so one bad row doesn't deny the
    rest of the queue a clean recovery. ``recovered`` reflects only
    the rows that actually transitioned."""
    from app.dependencies import build_services
    from app.models.document import DocumentVersionStatus
    from app.services.extraction_recovery import recover_stuck_extractions

    services = build_services()
    object.__setattr__(services.settings, "extraction_inline", False)

    # Land two versions in EXTRACTING.
    v1 = services.documents.upload(
        filename="a.txt",
        content_type="text/plain",
        content=b"contents A",
    )
    services.documents.update_status(v1.document_id, v1.id, DocumentVersionStatus.EXTRACTING)
    v2 = services.documents.upload(
        filename="b.txt",
        content_type="text/plain",
        content=b"contents B",
    )
    services.documents.update_status(v2.document_id, v2.id, DocumentVersionStatus.EXTRACTING)

    # Make ``mark_failed`` blow up on v1 only; v2 must still recover.
    original_mark_failed = services.documents.mark_failed
    call_count = {"n": 0}

    def _flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated FSM write failure")
        return original_mark_failed(*args, **kwargs)

    monkeypatch.setattr(services.documents, "mark_failed", _flaky)

    recovered = recover_stuck_extractions(services)
    # Exactly one version recovered (the second call); the failed row
    # stays EXTRACTING since the mark call raised.
    assert recovered == 1


def test_recover_skips_versions_already_in_terminal_state():
    """Defensive branch: a document family with mixed-state versions
    (one stuck, one already FAILED / VALIDATED) only acts on the
    stuck one. Pins the per-version status filter inside the helper."""
    from app.dependencies import build_services
    from app.models.document import DocumentVersionStatus
    from app.services.extraction_recovery import recover_stuck_extractions

    services = build_services()
    object.__setattr__(services.settings, "extraction_inline", False)

    # Upload v1 and drive it to FAILED (not stuck).
    v1 = services.documents.upload(
        filename="mixed.txt",
        content_type="text/plain",
        content=b"first content",
    )
    services.documents.mark_failed(v1.document_id, v1.id, "intentional fail")

    # Append v2 to the same family + park it in EXTRACTING (stuck).
    v2 = services.documents.upload(
        filename="mixed.txt",
        content_type="text/plain",
        content=b"second content",
        document_id=v1.document_id,
    )
    services.documents.update_status(
        v1.document_id,
        v2.id,
        DocumentVersionStatus.EXTRACTING,
    )

    recovered = recover_stuck_extractions(services)
    assert recovered == 1  # only v2 transitioned

    v1_after = services.documents.get_version(document_id=v1.document_id, version_id=v1.id)
    v2_after = services.documents.get_version(document_id=v1.document_id, version_id=v2.id)
    assert v1_after.status == DocumentVersionStatus.FAILED  # untouched
    assert v2_after.status == DocumentVersionStatus.FAILED  # transitioned by recovery


def test_extraction_worker_name_property_returns_constructed_name():
    """Trivial pin on the public ``name`` property — exists because
    the lifespan logs ``worker.name`` and a future rename of the
    private attribute would silently shift the log shape."""
    from unittest.mock import MagicMock

    from app.services.extraction_worker import ExtractionWorker, InMemoryExtractionQueue

    worker = ExtractionWorker(
        queue=InMemoryExtractionQueue(maxsize=1),
        jobs=MagicMock(),
        name="custom-worker-id",
    )
    assert worker.name == "custom-worker-id"
