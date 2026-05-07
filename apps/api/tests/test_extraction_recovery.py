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
