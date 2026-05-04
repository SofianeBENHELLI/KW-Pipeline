"""Auto-supersede on validation (ADR-025, EPIC-C C.1).

Pins three contracts:

1. When v2 of a family transitions to VALIDATED, the prior VALIDATED
   v1 auto-transitions to SUPERSEDED. The audit trail records a
   ``version.superseded`` row attributing the transition to the
   actor that did the validation.
2. First-validation idempotency: validating v1 of a fresh family
   when no prior VALIDATED exists is a no-op (no error, no spurious
   audit row).
3. The supersede only fires when there is a *prior* ``VALIDATED``
   sibling. A ``REJECTED`` sibling is not eligible — only
   ``VALIDATED → SUPERSEDED`` is a legal FSM edge.
"""

from __future__ import annotations

from app.dependencies import build_services
from app.main import create_app
from app.models.document import DocumentVersionStatus


def _land_version_in_needs_review(
    services,
    *,
    document_id: str | None = None,
    filename: str = "policy.txt",
    content: bytes = b"Hello world. This is a tiny test fixture.",
) -> tuple[str, str]:
    """Drive a fresh upload through the pipeline so the returned
    ``(document_id, version_id)`` is in NEEDS_REVIEW.

    When ``document_id`` is set, the upload is appended to that
    family as a new version (the supersede flow needs a v2 to
    validate against an existing v1).
    """
    version = services.documents.upload(
        filename=filename,
        content_type="text/plain",
        content=content,
        document_id=document_id,
    )
    services.extraction_jobs.extract(document_id=version.document_id, version_id=version.id)
    services.semantic_outputs.generate(document_id=version.document_id, version_id=version.id)
    refreshed = services.documents.get_version(
        document_id=version.document_id, version_id=version.id
    )
    assert refreshed.status == DocumentVersionStatus.NEEDS_REVIEW
    return version.document_id, version.id


def test_validating_v2_supersedes_prior_validated_v1():
    services = build_services()
    create_app(services=services)

    # v1 — upload, extract, validate.
    document_id, v1_id = _land_version_in_needs_review(services)
    services.review.handle_validation(document_id=document_id, version_id=v1_id, actor="alice")

    # v2 — append to the same family, then validate.
    _, v2_id = _land_version_in_needs_review(
        services,
        document_id=document_id,
        filename="policy-v2.txt",
        content=b"Hello world. This is a different test fixture body.",
    )
    services.review.handle_validation(document_id=document_id, version_id=v2_id, actor="alice")

    v1_final = services.documents.get_version(document_id=document_id, version_id=v1_id)
    v2_final = services.documents.get_version(document_id=document_id, version_id=v2_id)
    assert v1_final.status == DocumentVersionStatus.SUPERSEDED
    assert v2_final.status == DocumentVersionStatus.VALIDATED

    # Audit attribution: a ``version.superseded`` row carrying the
    # actor that did the v2 validation.
    rows = services.audit_events.query(event_name="version.superseded")
    assert any(
        row.payload.get("version_id") == v1_id
        and row.payload.get("superseded_by_version_id") == v2_id
        and row.payload.get("actor") == "alice"
        for row in rows
    ), (
        "Expected a version.superseded audit row attributing the v1 "
        "supersede to actor='alice' with superseded_by_version_id=v2."
    )


def test_validating_first_version_does_not_emit_supersede():
    """First validation of a family — no prior VALIDATED sibling, so
    the supersede path must no-op without raising and without
    emitting an audit row."""
    services = build_services()
    create_app(services=services)

    document_id, v1_id = _land_version_in_needs_review(services)
    services.review.handle_validation(document_id=document_id, version_id=v1_id, actor="alice")

    v1_final = services.documents.get_version(document_id=document_id, version_id=v1_id)
    assert v1_final.status == DocumentVersionStatus.VALIDATED

    rows = services.audit_events.query(event_name="version.superseded")
    assert not rows, (
        "Expected no version.superseded audit rows on a first-validation "
        f"of a fresh family; got {len(rows)} rows."
    )


def test_rejected_v1_is_not_superseded_when_v2_validates():
    """Only VALIDATED → SUPERSEDED is a legal edge. A REJECTED v1
    must stay REJECTED when v2 is validated; the supersede path
    selects only the most recent prior ``VALIDATED`` sibling."""
    services = build_services()
    create_app(services=services)

    # v1 — reject.
    document_id, v1_id = _land_version_in_needs_review(services)
    services.review.handle_rejection(document_id=document_id, version_id=v1_id, actor="alice")

    # v2 — validate.
    _, v2_id = _land_version_in_needs_review(
        services,
        document_id=document_id,
        filename="policy-v2.txt",
        content=b"Hello world. This is a different test fixture body.",
    )
    services.review.handle_validation(document_id=document_id, version_id=v2_id, actor="alice")

    v1_final = services.documents.get_version(document_id=document_id, version_id=v1_id)
    v2_final = services.documents.get_version(document_id=document_id, version_id=v2_id)
    assert v1_final.status == DocumentVersionStatus.REJECTED
    assert v2_final.status == DocumentVersionStatus.VALIDATED

    rows = services.audit_events.query(event_name="version.superseded")
    assert not rows, (
        "Expected no version.superseded audit rows when the only prior "
        f"sibling is REJECTED; got {len(rows)} rows."
    )


def test_supersede_chain_across_three_versions():
    """v1 → SUPERSEDED when v2 validates; v2 → SUPERSEDED when v3
    validates. v1 stays SUPERSEDED through the v3 step."""
    services = build_services()
    create_app(services=services)

    document_id, v1_id = _land_version_in_needs_review(services)
    services.review.handle_validation(document_id=document_id, version_id=v1_id, actor="alice")

    _, v2_id = _land_version_in_needs_review(
        services,
        document_id=document_id,
        filename="policy-v2.txt",
        content=b"Hello world. This is the second body of the family.",
    )
    services.review.handle_validation(document_id=document_id, version_id=v2_id, actor="alice")

    _, v3_id = _land_version_in_needs_review(
        services,
        document_id=document_id,
        filename="policy-v3.txt",
        content=b"Hello world. This is the third and latest body.",
    )
    services.review.handle_validation(document_id=document_id, version_id=v3_id, actor="bob")

    v1_final = services.documents.get_version(document_id=document_id, version_id=v1_id)
    v2_final = services.documents.get_version(document_id=document_id, version_id=v2_id)
    v3_final = services.documents.get_version(document_id=document_id, version_id=v3_id)
    assert v1_final.status == DocumentVersionStatus.SUPERSEDED
    assert v2_final.status == DocumentVersionStatus.SUPERSEDED
    assert v3_final.status == DocumentVersionStatus.VALIDATED

    rows = services.audit_events.query(event_name="version.superseded")
    superseded_pairs = {
        (row.payload.get("version_id"), row.payload.get("superseded_by_version_id")) for row in rows
    }
    assert (v1_id, v2_id) in superseded_pairs
    assert (v2_id, v3_id) in superseded_pairs
