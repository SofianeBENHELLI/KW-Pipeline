"""Tests for the HITL auto-promotion worker (ADR-023 §6, slice 3, #215).

Drives the full worker contract:

- A pending row whose version is NEEDS_REVIEW gets promoted, the
  metadata flips to ``validation_method="auto"``, and the SPC counter
  is bumped.
- An already-promoted row is skipped on the next pass.
- A version no longer NEEDS_REVIEW (race against a human reviewer) is
  skipped with the right reason and the metadata stays untouched.
- A ReviewService failure lands in ``failed``; the pass continues.
- ``max_versions`` clamps the pass.
- ``actor="system:hitl_auto_promote"`` lands on the validation
  metadata + the underlying audit row.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from app.dependencies import build_services
from app.models.document import DocumentVersionStatus
from app.schemas.validation_metadata import (
    AutoPromoteResult,
    ConfidenceScore,
    ValidationMetadata,
)
from app.services.confidence_scorer import ALL_SIGNALS
from app.services.hitl_auto_promoter import SYSTEM_ACTOR, HITLAutoPromoter
from app.services.sampling_state_store import SamplingBucket


def _make_score(overall: float = 0.95) -> ConfidenceScore:
    return ConfidenceScore(
        overall=overall,
        signals=dict.fromkeys(ALL_SIGNALS, overall),
        weights=dict.fromkeys(ALL_SIGNALS, 0.2),
        ocr_override_active=False,
        computed_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        computed_by_version="v1",
    )


def _land_in_needs_review(
    services: Any,
    *,
    filename: str = "policy.txt",
    content: bytes = b"Hello world. This is a tiny test fixture.",
) -> tuple[str, str]:
    """Upload + extract + generate so a version lands in NEEDS_REVIEW."""
    version = services.documents.upload(
        filename=filename,
        content_type="text/plain",
        content=content,
    )
    services.extraction_jobs.extract(document_id=version.document_id, version_id=version.id)
    services.semantic_outputs.generate(document_id=version.document_id, version_id=version.id)
    refreshed = services.documents.get_version(
        document_id=version.document_id, version_id=version.id
    )
    assert refreshed.status == DocumentVersionStatus.NEEDS_REVIEW
    return version.document_id, version.id


def _force_routing(
    services: Any,
    *,
    version_id: str,
    routing: str = "auto",
    score: ConfidenceScore | None = None,
) -> None:
    """Overwrite a metadata row's routing_decision so the worker sees it.

    The router writes whatever decision its threshold + sampler picks;
    the test corpus is small so the score may land below threshold.
    Forcing the routing keeps the worker tests deterministic without
    coupling to scorer internals.
    """
    existing = services.validation_metadata.get(version_id)
    services.validation_metadata.upsert(
        ValidationMetadata(
            version_id=version_id,
            confidence_score=score or (existing and existing.confidence_score) or _make_score(),
            routing_decision=routing,  # type: ignore[arg-type]
            validation_method=None,
            validation_actor=None,
        )
    )


# ---------------------------------------------------------------------------
# Happy path: pending row → promoted, metadata flipped, SPC bumped.
# ---------------------------------------------------------------------------


def test_pending_auto_row_is_promoted_and_marks_metadata():
    """End-to-end: NEEDS_REVIEW + routing="auto" → VALIDATED + flagged."""
    services = build_services()
    document_id, version_id = _land_in_needs_review(services)
    _force_routing(services, version_id=version_id, routing="auto", score=_make_score(0.95))

    result = services.hitl_auto_promoter.run_pass()

    assert isinstance(result, AutoPromoteResult)
    assert result.scanned == 1
    assert len(result.promoted) == 1
    assert len(result.skipped) == 0
    assert len(result.failed) == 0

    promoted = result.promoted[0]
    assert promoted.document_id == document_id
    assert promoted.version_id == version_id
    assert promoted.score_overall == pytest.approx(0.95)

    # FSM transitioned.
    refreshed = services.documents.get_version(document_id=document_id, version_id=version_id)
    assert refreshed.status == DocumentVersionStatus.VALIDATED

    # Metadata flipped — next pass would skip this row.
    metadata = services.validation_metadata.get(version_id)
    assert metadata is not None
    assert metadata.validation_method == "auto"
    assert metadata.validation_actor == SYSTEM_ACTOR


def test_promoted_row_is_skipped_on_next_pass():
    """A second pass over the same data finds nothing pending — the
    ``validation_method`` filter excludes already-promoted rows."""
    services = build_services()
    _, version_id = _land_in_needs_review(services)
    _force_routing(services, version_id=version_id, routing="auto")

    first = services.hitl_auto_promoter.run_pass()
    assert len(first.promoted) == 1

    second = services.hitl_auto_promoter.run_pass()
    assert second.scanned == 0
    assert second.promoted == []
    assert second.skipped == []
    assert second.failed == []


def test_promotion_bumps_spc_counter_for_bucket():
    """The drift detector (next slice) reads ``samples_auto`` —
    every successful promotion bumps it for the bucket."""
    services = build_services()
    _, version_id = _land_in_needs_review(services)
    _force_routing(services, version_id=version_id, routing="auto")

    # The router already bumped the counter once when it picked a
    # decision during the NEEDS_REVIEW transition. Snapshot the
    # baseline so we assert the worker's *own* increment.
    bucket = SamplingBucket.from_optional(content_type="text/plain", topic_cluster=None)
    baseline = services.sampling_state.read_counters(bucket=bucket)

    services.hitl_auto_promoter.run_pass()

    after = services.sampling_state.read_counters(bucket=bucket)
    assert after.samples_taken == baseline.samples_taken + 1
    assert after.samples_auto == baseline.samples_auto + 1


# ---------------------------------------------------------------------------
# Race + skip paths.
# ---------------------------------------------------------------------------


def test_already_validated_row_is_skipped():
    """A row whose ``validation_method`` is already set is excluded by
    the store filter — it never reaches the worker. The metadata stays
    as-is."""
    services = build_services()
    _, version_id = _land_in_needs_review(services)
    services.validation_metadata.upsert(
        ValidationMetadata(
            version_id=version_id,
            confidence_score=_make_score(0.95),
            routing_decision="auto",
            validation_method="auto",  # already promoted
            validation_actor="alice",
        )
    )

    result = services.hitl_auto_promoter.run_pass()

    assert result.scanned == 0
    assert result.promoted == []
    assert result.skipped == []


def test_version_no_longer_needs_review_is_skipped_not_failed():
    """Race: a human reviewer flipped the version to VALIDATED before
    the worker got there. The worker must skip — not crash, not fail.

    The metadata row is left alone (validation_method stays None) so
    a human's manual cleanup still has the audit trail intact.
    """
    services = build_services()
    document_id, version_id = _land_in_needs_review(services)
    _force_routing(services, version_id=version_id, routing="auto")

    # Human reviewer beats the worker to it.
    services.review.handle_validation(
        document_id=document_id,
        version_id=version_id,
        reviewer_note="manual",
        actor="alice",
    )

    # The human's validation flow does NOT touch validation_metadata,
    # so the row still looks "pending" to the worker. We re-set the
    # routing in case a side-effect cleared it — simulating the race.
    _force_routing(services, version_id=version_id, routing="auto")

    result = services.hitl_auto_promoter.run_pass()

    assert result.scanned == 1
    assert result.promoted == []
    assert len(result.skipped) == 1
    skipped = result.skipped[0]
    assert skipped.document_id == document_id
    assert skipped.version_id == version_id
    assert skipped.reason == "version_no_longer_needs_review"
    assert result.failed == []

    # Metadata not flipped — race-safe.
    metadata = services.validation_metadata.get(version_id)
    assert metadata is not None
    assert metadata.validation_method is None


def test_missing_version_in_catalog_is_skipped():
    """Defensive: a metadata row whose version_id no longer matches a
    catalog row is skipped (not a failure). Should not happen with the
    migration 0007 FK in place, but the worker stays defensive."""
    services = build_services()
    services.validation_metadata.upsert(
        ValidationMetadata(
            version_id="orphan-version-id",
            confidence_score=_make_score(0.9),
            routing_decision="auto",
        )
    )

    result = services.hitl_auto_promoter.run_pass()

    assert result.scanned == 1
    assert result.promoted == []
    assert len(result.skipped) == 1
    assert result.skipped[0].version_id == "orphan-version-id"
    assert result.skipped[0].reason == "version_not_found"
    assert result.failed == []


# ---------------------------------------------------------------------------
# Failure path — fire-and-log, the pass continues.
# ---------------------------------------------------------------------------


class _ExplodingReviewService:
    """ReviewService stub whose ``handle_validation`` always raises."""

    def __init__(self, message: str = "boom"):
        self._message = message

    def handle_validation(self, **_: Any) -> Any:
        raise ValueError(self._message)


def test_review_service_failure_reports_in_failed_and_continues(
    caplog: pytest.LogCaptureFixture,
):
    """A single bad row must not abort the pass (ADR-012 §3)."""
    services = build_services()
    # Two pending rows. The first will explode (we substitute the
    # review service); the second would normally promote, but since
    # we replaced the service every row that reaches handle_validation
    # raises. Both should land in ``failed`` and the pass should
    # complete.
    document_a, version_a = _land_in_needs_review(
        services, filename="a.txt", content=b"Doc A bytes"
    )
    document_b, version_b = _land_in_needs_review(
        services, filename="b.txt", content=b"Doc B bytes"
    )
    _force_routing(services, version_id=version_a, routing="auto", score=_make_score(0.9))
    _force_routing(services, version_id=version_b, routing="auto", score=_make_score(0.91))

    promoter = HITLAutoPromoter(
        validation_metadata=services.validation_metadata,
        review_service=_ExplodingReviewService("forced failure"),  # type: ignore[arg-type]
        sampling_state=services.sampling_state,
        catalog=services.documents.catalog,
    )

    with caplog.at_level(logging.ERROR, logger="app.services.hitl_auto_promoter"):
        result = promoter.run_pass()

    assert result.scanned == 2
    assert result.promoted == []
    assert result.skipped == []
    assert len(result.failed) == 2
    assert {row.version_id for row in result.failed} == {version_a, version_b}
    for row in result.failed:
        assert "forced failure" in row.error
        assert row.document_id in {document_a, document_b}

    # Each failure logged at error level with the structured event.
    failure_events = [r for r in caplog.records if r.message == "hitl.auto_promote.version_failed"]
    assert len(failure_events) == 2

    # Metadata not flipped — the failed rows stay pending so a future
    # pass can retry once the underlying issue is fixed.
    for vid in (version_a, version_b):
        metadata = services.validation_metadata.get(vid)
        assert metadata is not None
        assert metadata.validation_method is None


# ---------------------------------------------------------------------------
# max_versions clamping.
# ---------------------------------------------------------------------------


def test_max_versions_clamps_the_pass():
    """``max_versions=2`` with 5 pending rows → exactly 2 promoted,
    3 left for the next pass."""
    services = build_services()
    pending_ids: list[str] = []
    for i in range(5):
        _, version_id = _land_in_needs_review(
            services,
            filename=f"file-{i}.txt",
            content=f"file {i} bytes".encode(),
        )
        _force_routing(services, version_id=version_id, routing="auto")
        pending_ids.append(version_id)

    first = services.hitl_auto_promoter.run_pass(max_versions=2)
    assert first.scanned == 2
    assert len(first.promoted) == 2

    # 3 remain for the next pass.
    second = services.hitl_auto_promoter.run_pass()
    assert second.scanned == 3
    assert len(second.promoted) == 3

    # Pool empty.
    third = services.hitl_auto_promoter.run_pass()
    assert third.scanned == 0


def test_max_versions_none_processes_all_pending():
    """``None`` (default) means "process all pending"."""
    services = build_services()
    for i in range(3):
        _, version_id = _land_in_needs_review(
            services,
            filename=f"file-{i}.txt",
            content=f"bytes {i}".encode(),
        )
        _force_routing(services, version_id=version_id, routing="auto")

    result = services.hitl_auto_promoter.run_pass(max_versions=None)
    assert result.scanned == 3
    assert len(result.promoted) == 3


# ---------------------------------------------------------------------------
# Empty + non-auto routing decisions are not in the worker's set.
# ---------------------------------------------------------------------------


def test_empty_pending_set_returns_empty_result():
    services = build_services()
    result = services.hitl_auto_promoter.run_pass()
    assert result.scanned == 0
    assert result.promoted == []
    assert result.skipped == []
    assert result.failed == []


def test_human_routed_rows_are_not_picked_up():
    """``routing_decision="human"`` is the human-review path; the
    worker must not touch those rows even when their version is
    still NEEDS_REVIEW."""
    services = build_services()
    _, version_id = _land_in_needs_review(services)
    _force_routing(services, version_id=version_id, routing="human", score=_make_score(0.4))

    result = services.hitl_auto_promoter.run_pass()

    assert result.scanned == 0
    assert result.promoted == []


# ---------------------------------------------------------------------------
# Store-level filter pinning.
# ---------------------------------------------------------------------------


def test_list_pending_auto_promotions_filters_correctly():
    """Pin the store contract directly — the worker depends on it."""
    services = build_services()
    # Three rows in three states; only the auto+pending one should
    # surface.
    services.validation_metadata.upsert(
        ValidationMetadata(version_id="v-auto-pending", routing_decision="auto")
    )
    services.validation_metadata.upsert(
        ValidationMetadata(
            version_id="v-auto-done", routing_decision="auto", validation_method="auto"
        )
    )
    services.validation_metadata.upsert(
        ValidationMetadata(version_id="v-human-pending", routing_decision="human")
    )

    pending = services.validation_metadata.list_pending_auto_promotions()
    assert [row.version_id for row in pending] == ["v-auto-pending"]


def test_mark_auto_promoted_is_idempotent():
    """Calling mark_auto_promoted twice does not stomp the actor."""
    services = build_services()
    services.validation_metadata.upsert(
        ValidationMetadata(version_id="ver-1", routing_decision="auto")
    )
    services.validation_metadata.mark_auto_promoted("ver-1", actor=SYSTEM_ACTOR)
    services.validation_metadata.mark_auto_promoted("ver-1", actor="someone-else")

    fetched = services.validation_metadata.get("ver-1")
    assert fetched is not None
    assert fetched.validation_method == "auto"
    # First-write wins; the second call's actor was ignored.
    assert fetched.validation_actor == SYSTEM_ACTOR


def test_mark_auto_promoted_on_missing_row_is_noop():
    """Defensive: a stale call against a row that no longer exists
    must not crash."""
    services = build_services()
    services.validation_metadata.mark_auto_promoted(
        "never-existed-id",
        actor=SYSTEM_ACTOR,
    )
    # No exception raised; nothing to assert beyond that.


# ---------------------------------------------------------------------------
# Defensive race + store-failure paths.
# ---------------------------------------------------------------------------


class _PostListFlippingStore:
    """Wraps a real store but returns rows whose validation_method has
    been flipped by a "parallel pass" between list and act.

    Models the (extremely rare) race where two worker passes race over
    the same row: the second pass should skip with ``already_validated``
    rather than crash on the FSM transition.
    """

    def __init__(self, inner):
        self._inner = inner
        self.name = "post-list-flipping"

    def upsert(self, metadata):
        self._inner.upsert(metadata)

    def get(self, version_id):
        return self._inner.get(version_id)

    def list_all(self):
        return self._inner.list_all()

    def list_pending_auto_promotions(self):
        # Take the real pending list, then mutate every row's
        # validation_method to simulate a parallel pass that flipped
        # them after we took the snapshot.
        rows = self._inner.list_pending_auto_promotions()
        return [
            row.model_copy(update={"validation_method": "auto", "validation_actor": "racer"})
            for row in rows
        ]

    def mark_auto_promoted(self, version_id, *, actor):
        self._inner.mark_auto_promoted(version_id, actor=actor)


def test_already_validated_race_is_skipped_in_promoter():
    """Models the post-list flip race — promoter must report
    ``already_validated`` rather than try (and fail) the FSM transition."""
    services = build_services()
    _, version_id = _land_in_needs_review(services)
    _force_routing(services, version_id=version_id, routing="auto")

    promoter = HITLAutoPromoter(
        validation_metadata=_PostListFlippingStore(services.validation_metadata),
        review_service=services.review,
        sampling_state=services.sampling_state,
        catalog=services.documents.catalog,
    )

    result = promoter.run_pass()

    assert result.scanned == 1
    assert result.promoted == []
    assert result.failed == []
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == "already_validated"
    assert result.skipped[0].version_id == version_id


class _ExplodingMarkPromotedStore:
    """Real store but whose ``mark_auto_promoted`` always raises.

    The worker's contract: a metadata bookkeeping miss is logged and
    swallowed — the FSM transition already landed and rolling back
    would leave the catalog in a worse state.
    """

    def __init__(self, inner):
        self._inner = inner
        self.name = "exploding-mark-promoted"

    def upsert(self, metadata):
        self._inner.upsert(metadata)

    def get(self, version_id):
        return self._inner.get(version_id)

    def list_all(self):
        return self._inner.list_all()

    def list_pending_auto_promotions(self):
        return self._inner.list_pending_auto_promotions()

    def mark_auto_promoted(self, version_id, *, actor):
        raise RuntimeError("metadata store offline")


def test_mark_promoted_failure_is_logged_but_promotion_still_succeeds(
    caplog: pytest.LogCaptureFixture,
):
    """The validation already landed — the promotion result still
    reports the row as promoted, and the failure is logged."""
    services = build_services()
    document_id, version_id = _land_in_needs_review(services)
    _force_routing(services, version_id=version_id, routing="auto", score=_make_score(0.9))

    promoter = HITLAutoPromoter(
        validation_metadata=_ExplodingMarkPromotedStore(services.validation_metadata),
        review_service=services.review,
        sampling_state=services.sampling_state,
        catalog=services.documents.catalog,
    )

    with caplog.at_level(logging.ERROR, logger="app.services.hitl_auto_promoter"):
        result = promoter.run_pass()

    assert len(result.promoted) == 1
    assert result.promoted[0].version_id == version_id
    assert result.failed == []

    # The FSM transition still landed.
    refreshed = services.documents.get_version(document_id=document_id, version_id=version_id)
    assert refreshed.status == DocumentVersionStatus.VALIDATED

    # The bookkeeping failure is captured in the log.
    failures = [r for r in caplog.records if r.message == "hitl.auto_promote.mark_promoted_failed"]
    assert len(failures) == 1


class _ExplodingSamplingStateStore:
    """SPC store stub whose ``record_decision`` always raises."""

    name = "exploding-spc"

    def record_decision(self, *, bucket, method):
        raise RuntimeError("spc store offline")

    def record_drift_event(self, *, bucket):  # pragma: no cover - unused here
        pass

    def read_counters(self, *, bucket):  # pragma: no cover - unused here
        from app.services.sampling_state_store import SamplingCounters

        return SamplingCounters()


def test_spc_bump_failure_is_logged_but_promotion_still_succeeds(
    caplog: pytest.LogCaptureFixture,
):
    """SPC bookkeeping failure is fire-and-log too — the promotion
    still counts as successful."""
    services = build_services()
    _, version_id = _land_in_needs_review(services)
    _force_routing(services, version_id=version_id, routing="auto")

    promoter = HITLAutoPromoter(
        validation_metadata=services.validation_metadata,
        review_service=services.review,
        sampling_state=_ExplodingSamplingStateStore(),
        catalog=services.documents.catalog,
    )

    with caplog.at_level(logging.ERROR, logger="app.services.hitl_auto_promoter"):
        result = promoter.run_pass()

    assert len(result.promoted) == 1
    assert result.promoted[0].version_id == version_id

    failures = [r for r in caplog.records if r.message == "hitl.auto_promote.spc_bump_failed"]
    assert len(failures) == 1
