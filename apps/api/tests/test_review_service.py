"""Direct unit tests for ``ReviewService`` (audit P0 #223).

These tests exercise the service in isolation — no FastAPI, no
TestClient, no HTTP. The collaborator wiring uses the same in-memory
adapters that the rest of the suite already trusts (the
``build_services`` factory plus a one-shot upload + extract +
semantic round-trip to land a version in NEEDS_REVIEW).

Three contracts are pinned:

1. ``handle_validation`` drives a NEEDS_REVIEW version to VALIDATED
   and returns the persisted ``SemanticDocument`` with
   ``validation_status="validated"``.
2. ``handle_rejection`` drives the same path to REJECTED with
   ``validation_status="rejected"``.
3. The service raises ``KeyError`` (missing entity) and ``ValueError``
   (FSM precondition failure) — never an HTTP exception. The route
   layer is responsible for translating to HTTP envelopes.

A separate test pins the fire-and-log discipline: a flaky knowledge
projector must NOT roll back the FSM transition.
"""

from __future__ import annotations

import pytest

from app.dependencies import build_services
from app.models.document import DocumentVersionStatus


def _land_version_in_needs_review(services) -> tuple[str, str]:
    """Drive a fresh upload through the pipeline so the returned
    ``(document_id, version_id)`` is in NEEDS_REVIEW.
    """
    version = services.documents.upload(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Hello world. This is a tiny test fixture.",
    )
    services.extraction_jobs.extract(document_id=version.document_id, version_id=version.id)
    services.semantic_outputs.generate(document_id=version.document_id, version_id=version.id)
    refreshed = services.documents.get_version(
        document_id=version.document_id, version_id=version.id
    )
    assert refreshed.status == DocumentVersionStatus.NEEDS_REVIEW
    return version.document_id, version.id


def test_handle_validation_drives_needs_review_to_validated():
    services = build_services()
    document_id, version_id = _land_version_in_needs_review(services)

    result = services.review.handle_validation(
        document_id=document_id,
        version_id=version_id,
        reviewer_note="Looks good.",
    )

    assert result.validation_status == "validated"
    final = services.documents.get_version(document_id=document_id, version_id=version_id)
    assert final.status == DocumentVersionStatus.VALIDATED
    assert final.reviewer_note == "Looks good."


def test_handle_rejection_drives_needs_review_to_rejected():
    services = build_services()
    document_id, version_id = _land_version_in_needs_review(services)

    result = services.review.handle_rejection(
        document_id=document_id,
        version_id=version_id,
        reviewer_note="Wrong document.",
    )

    assert result.validation_status == "rejected"
    final = services.documents.get_version(document_id=document_id, version_id=version_id)
    assert final.status == DocumentVersionStatus.REJECTED
    assert final.reviewer_note == "Wrong document."


def test_handle_validation_raises_key_error_for_missing_version():
    """Service contract: raise plain ``KeyError`` for missing entity.
    The route layer translates this to HTTP 404."""
    services = build_services()
    with pytest.raises(KeyError):
        services.review.handle_validation(
            document_id="ghost-doc",
            version_id="ghost-ver",
        )


def test_handle_validation_raises_value_error_when_not_in_needs_review():
    """Service contract: raise ``ValueError`` when the FSM precondition
    fails. The route layer translates this to HTTP 409 with the
    ``LIFECYCLE_CONFLICT`` envelope."""
    services = build_services()
    document_id, version_id = _land_version_in_needs_review(services)

    # First validation succeeds; the version is now VALIDATED, no
    # longer NEEDS_REVIEW.
    services.review.handle_validation(document_id=document_id, version_id=version_id)

    with pytest.raises(ValueError, match="not NEEDS_REVIEW"):
        services.review.handle_validation(document_id=document_id, version_id=version_id)


def test_handle_validation_records_actor_in_audit_event():
    """ADR-019 §4: every audit row for a write action carries an
    ``actor``. The review-decision path is the first one wired.

    Goes through ``create_app`` so the audit log handler is attached
    — the in-memory store on the services container then captures
    the structured ``review.validated`` event for the assertion.
    """
    from app.main import create_app

    services = build_services()
    create_app(services=services)
    document_id, version_id = _land_version_in_needs_review(services)

    services.review.handle_validation(
        document_id=document_id,
        version_id=version_id,
        actor="alice",
    )

    rows = services.audit_events.query(event_name="review.validated")
    assert any(row.payload.get("actor") == "alice" for row in rows), (
        "Expected a review.validated audit row attributing the decision to actor='alice'."
    )


def test_handle_rejection_records_actor_in_audit_event():
    from app.main import create_app

    services = build_services()
    create_app(services=services)
    document_id, version_id = _land_version_in_needs_review(services)

    services.review.handle_rejection(
        document_id=document_id,
        version_id=version_id,
        actor="bob",
    )

    rows = services.audit_events.query(event_name="review.rejected")
    assert any(row.payload.get("actor") == "bob" for row in rows), (
        "Expected a review.rejected audit row attributing the decision to actor='bob'."
    )


def test_handle_validation_omits_actor_when_not_provided():
    """Backward-compat: callers that don't pass ``actor`` (yet) still
    work; the audit row carries ``actor=None``. ADR-019's slicing
    plan covers migrating remaining callers."""
    from app.main import create_app

    services = build_services()
    create_app(services=services)
    document_id, version_id = _land_version_in_needs_review(services)

    services.review.handle_validation(document_id=document_id, version_id=version_id)

    rows = services.audit_events.query(event_name="review.validated")
    assert any("actor" in row.payload for row in rows)


def test_handle_rejection_bumps_drift_counter_when_router_decided_auto():
    """EPIC-A A.3 part 2 (#215, ADR-023 §6): a rejection of a version
    the router originally decided to auto is the canonical drift
    signal — bump ``samples_human_after_auto`` for the bucket so the
    drift detector ramps the bucket's sampling rate."""
    from app.schemas.validation_metadata import ValidationMetadata
    from app.services.sampling_state_store import SamplingBucket

    services = build_services()
    document_id, version_id = _land_version_in_needs_review(services)

    # Force the validation_metadata into an "auto-decided, not yet
    # promoted" shape so ``handle_rejection`` recognises this as a
    # drift signal.
    services.validation_metadata.upsert(
        ValidationMetadata(
            version_id=version_id,
            confidence_score=None,
            routing_decision="auto",
            validation_method=None,
        ),
    )

    services.review.handle_rejection(
        document_id=document_id,
        version_id=version_id,
        reviewer_note="Looked good to the router; humans disagreed.",
        actor="alice",
    )

    counters = services.sampling_state.read_counters(
        bucket=SamplingBucket(content_type="text/plain", topic_cluster="_unknown_"),
    )
    assert counters.samples_human_after_auto == 1


def test_handle_rejection_does_not_bump_drift_counter_for_human_routed_version():
    """The rejection of a version the router decided to ``human``
    (e.g. below threshold) is NOT a drift event — the router never
    thought this version was auto-eligible."""
    from app.schemas.validation_metadata import ValidationMetadata
    from app.services.sampling_state_store import SamplingBucket

    services = build_services()
    document_id, version_id = _land_version_in_needs_review(services)

    services.validation_metadata.upsert(
        ValidationMetadata(
            version_id=version_id,
            routing_decision="human",
        ),
    )

    services.review.handle_rejection(
        document_id=document_id,
        version_id=version_id,
        actor="alice",
    )

    counters = services.sampling_state.read_counters(
        bucket=SamplingBucket(content_type="text/plain", topic_cluster="_unknown_"),
    )
    assert counters.samples_human_after_auto == 0


def test_handle_rejection_does_not_bump_drift_counter_when_no_metadata():
    """Defensive: a rejection on a version with no validation_metadata
    row (scorer disabled, legacy data) is a no-op for the drift counter."""
    from app.services.sampling_state_store import SamplingBucket

    services = build_services()
    document_id, version_id = _land_version_in_needs_review(services)

    # Wipe any auto-written metadata so the lookup returns None.
    services.validation_metadata._rows.clear()  # type: ignore[attr-defined]

    services.review.handle_rejection(
        document_id=document_id,
        version_id=version_id,
        actor="alice",
    )

    counters = services.sampling_state.read_counters(
        bucket=SamplingBucket(content_type="text/plain", topic_cluster="_unknown_"),
    )
    assert counters.samples_human_after_auto == 0


def test_handle_validation_does_not_roll_back_on_projector_failure():
    """ADR-012 fire-and-log: a knowledge-projector failure must NOT
    roll back the FSM transition. The catalog stays the source of
    truth; the graph catches up via re-projection."""
    services = build_services()
    document_id, version_id = _land_version_in_needs_review(services)

    # Replace the projector on the service with one that always raises.
    class _FlakyProjector:
        def project(self, *, document, version, semantic):
            raise RuntimeError("simulated projector outage")

        def project_entities(self, *args, **kwargs):  # pragma: no cover - unused here
            raise RuntimeError("unreachable")

    # ``ReviewService`` holds the projector reference privately; reach
    # into the attribute directly so the test exercises the actual
    # service code path.
    services.review._knowledge_projector = _FlakyProjector()  # type: ignore[attr-defined]

    # Even with a guaranteed projector failure, the validation must
    # complete and persist.
    result = services.review.handle_validation(document_id=document_id, version_id=version_id)
    assert result.validation_status == "validated"
    final = services.documents.get_version(document_id=document_id, version_id=version_id)
    assert final.status == DocumentVersionStatus.VALIDATED
