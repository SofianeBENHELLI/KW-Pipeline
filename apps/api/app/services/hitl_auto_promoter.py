"""HITL auto-promotion worker (ADR-023 §6, EPIC-A A.4 / slice 3, #215).

Builds on slice 1 (:class:`ConfidenceScorer`, #268) + slice 2
(:class:`HITLRouter`, #271). The router persists
``ValidationMetadata.routing_decision = "auto"`` for every version
above the auto-validate threshold (modulo the OCR override + the SPC
sampler escalation); it does NOT transition the FSM. This worker is
the second half of that contract: it scans for those pending rows and
drives each version :data:`NEEDS_REVIEW` → :data:`VALIDATED` via
:meth:`ReviewService.handle_validation`.

Splitting decision (:class:`HITLRouter`) from action
(:class:`HITLAutoPromoter`) keeps the router pure-ish (one side
effect: the SPC counters) and lets the worker apply the idempotency
+ race-detection logic below without bloating the decision tree.

What this worker does NOT include
---------------------------------
- A real background scheduler (cron / asyncio task). EPIC-B may light
  one up later; for now the worker is a callable invoked manually
  from :class:`POST /admin/hitl/run_auto_promote_pass`. The MVP
  posture (per the slice plan) is "synchronous admin trigger only" —
  that's the safe shape we ship.
- The drift detector (next slice). The promoter does not decide; it
  acts on rows the router already decided to auto-validate. A
  future slice reads the SPC counters this worker bumps to lift the
  baseline sample rate when ``samples_human_after_auto / samples_auto``
  exceeds a threshold.

Failure handling
----------------
Per ADR-012 §3 fire-and-log discipline, a single version's
auto-promotion failure must NOT abort the worker pass. Each
per-version exception is caught, logged via
``hitl.auto_promote.version_failed``, and surfaced in the structured
:class:`AutoPromoteResult.failed` list so the admin trigger response
shows what happened. The worker keeps going on the next pending row.
"""

from __future__ import annotations

import logging

from app.models.document import DocumentVersionStatus
from app.schemas.document import DocumentVersion
from app.schemas.validation_metadata import (
    AutoPromoteResult,
    FailedVersion,
    PromotedVersion,
    SkippedReason,
    SkippedVersion,
    ValidationMetadata,
)
from app.services.catalog_store import CatalogStore
from app.services.review_service import ReviewService
from app.services.sampling_state_store import SamplingBucket, SamplingStateStore
from app.services.validation_metadata_store import ValidationMetadataStore

log = logging.getLogger(__name__)

# ADR-019 §4: audit events carry an actor. The worker is a background
# process — not a user-initiated action — so the actor is a stable
# system pseudo-id rather than a real principal id. ``"system:"`` is
# the conventional prefix for audit rows the system itself wrote
# (mirrors how ``"system:cascade"`` is used in #234 / D.5).
SYSTEM_ACTOR = "system:hitl_auto_promote"


class HITLAutoPromoter:
    """Scan and auto-promote router-flagged versions.

    Per EPIC-A slice 3 / ADR-023. The promoter does NOT include the
    drift detector (next slice) — it just does the FSM transition for
    rows the router already decided to auto-validate.

    Construction
    ------------
    The worker takes its collaborators by keyword to make the wiring
    explicit and to keep the test setup trivial (build in-memory
    versions of each, pass them in). Both :attr:`validation_metadata`
    and :attr:`sampling_state` are the same instances the
    :class:`HITLRouter` writes to; reusing them keeps the SPC
    counters consistent across decisions and promotions.
    """

    def __init__(
        self,
        *,
        validation_metadata: ValidationMetadataStore,
        review_service: ReviewService,
        sampling_state: SamplingStateStore,
        catalog: CatalogStore,
    ) -> None:
        self._validation_metadata = validation_metadata
        self._review_service = review_service
        self._sampling_state = sampling_state
        self._catalog = catalog

    def run_pass(self, *, max_versions: int | None = None) -> AutoPromoteResult:
        """One pass over the pending auto-routed versions.

        Returns a structured :class:`AutoPromoteResult` with counts +
        per-version outcomes so the admin trigger can surface what
        happened. ``max_versions`` caps the pass for safety; ``None``
        means "process all pending".

        The worker:

        1. Reads :meth:`ValidationMetadataStore.list_pending_auto_promotions`.
        2. For each row (up to ``max_versions``):
           a. Looks up the parent ``document_id`` for the row's
              ``version_id`` via the catalog. A missing document/version
              is skipped (race: the row was deleted between the list
              and the act) — see :data:`SkippedReason`.
           b. Refuses to promote a version whose status is no longer
              ``NEEDS_REVIEW``. A human reviewer beat us, or the
              version was rejected. Skipped, never failed.
           c. Calls :meth:`ReviewService.handle_validation` with
              ``actor=SYSTEM_ACTOR``. The service drives the FSM,
              records the validation, and fires the knowledge-layer
              side effects (fire-and-log per ADR-012).
           d. On success, flips ``validation_method="auto"`` via
              :meth:`ValidationMetadataStore.mark_auto_promoted` so the
              next pass skips the row, and bumps the SPC counter via
              :meth:`SamplingStateStore.record_decision` so the future
              drift detector sees the promotion.

        Any exception inside step c/d is caught and reported in the
        ``failed`` bucket; the pass continues with the next row.
        """
        pending = self._validation_metadata.list_pending_auto_promotions()
        if max_versions is not None:
            pending = pending[:max_versions]

        # Build a one-shot version_id → (document_id, version) lookup
        # by walking the catalog once per pass. Cheaper than calling
        # ``DocumentService.get_version`` per row, which would force a
        # repeated document load on the SQLite path. The walk is
        # bounded by the catalog size (one row per DocumentVersion);
        # for the pilot this is small enough to scan on-demand.
        version_index = self._build_version_index()

        promoted: list[PromotedVersion] = []
        skipped: list[SkippedVersion] = []
        failed: list[FailedVersion] = []

        for metadata in pending:
            outcome = self._promote_one(metadata, version_index=version_index)
            if isinstance(outcome, PromotedVersion):
                promoted.append(outcome)
            elif isinstance(outcome, SkippedVersion):
                skipped.append(outcome)
            else:
                failed.append(outcome)

        log.info(
            "hitl.auto_promote.pass_completed",
            extra={
                "scanned": len(pending),
                "promoted": len(promoted),
                "skipped": len(skipped),
                "failed": len(failed),
                "max_versions": max_versions,
            },
        )

        return AutoPromoteResult(
            scanned=len(pending),
            promoted=promoted,
            skipped=skipped,
            failed=failed,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _promote_one(
        self,
        metadata: ValidationMetadata,
        *,
        version_index: dict[str, tuple[str, DocumentVersion]],
    ) -> PromotedVersion | SkippedVersion | FailedVersion:
        """Promote a single metadata row.

        The full per-version branch table — kept private so the
        :meth:`run_pass` loop reads as a one-liner and the branch
        coverage lands in one focused method.
        """
        version_id = metadata.version_id

        # Defensive: the store filter excludes rows that already carry
        # a validation_method, but a parallel worker pass could have
        # flipped the row between the list and the act. Skip rather
        # than crash.
        if metadata.validation_method is not None:
            return self._skip(version_id, "", "already_validated")

        lookup = version_index.get(version_id)
        if lookup is None:
            # Row references a version_id the catalog no longer knows.
            # The migration 0007 FK should prevent this (validation_metadata
            # rows are deleted when their parent version is), but the
            # worker stays defensive.
            return self._skip(version_id, "", "version_not_found")
        document_id, version = lookup

        if version.status != DocumentVersionStatus.NEEDS_REVIEW:
            # Race: a human reviewer (or another worker pass, or a
            # FAILED transition) beat us. Don't write to validation
            # metadata — leaving the row as-is means the next pass
            # also detects the race rather than silently skipping.
            return self._skip(version_id, document_id, "version_no_longer_needs_review")

        try:
            self._review_service.handle_validation(
                document_id=document_id,
                version_id=version_id,
                reviewer_note=None,
                actor=SYSTEM_ACTOR,
            )
        except Exception as exc:  # noqa: BLE001 - fire-and-log boundary
            # ADR-012 §3: a single version's failure must NOT abort the
            # pass. Log loudly and report the row in ``failed``.
            log.exception(
                "hitl.auto_promote.version_failed",
                extra={
                    "document_id": document_id,
                    "version_id": version_id,
                    "error_type": type(exc).__name__,
                },
            )
            return FailedVersion(
                document_id=document_id,
                version_id=version_id,
                error=str(exc),
            )

        # Promotion succeeded. Flip the metadata so the next pass
        # skips this row, then bump the SPC counter.
        try:
            self._validation_metadata.mark_auto_promoted(version_id, actor=SYSTEM_ACTOR)
        except Exception:  # noqa: BLE001 - same fire-and-log discipline
            log.exception(
                "hitl.auto_promote.mark_promoted_failed",
                extra={"document_id": document_id, "version_id": version_id},
            )
            # The validation already landed — don't fail the row over
            # a metadata bookkeeping miss. The next pass will detect
            # the version is no longer NEEDS_REVIEW and skip it.

        try:
            bucket = SamplingBucket.from_optional(
                content_type=version.content_type,
                topic_cluster=None,
            )
            self._sampling_state.record_decision(bucket=bucket, method="auto")
        except Exception:  # noqa: BLE001 - same discipline
            log.exception(
                "hitl.auto_promote.spc_bump_failed",
                extra={"document_id": document_id, "version_id": version_id},
            )

        score_overall = (
            metadata.confidence_score.overall if metadata.confidence_score is not None else 0.0
        )
        return PromotedVersion(
            document_id=document_id,
            version_id=version_id,
            score_overall=score_overall,
        )

    def _skip(
        self,
        version_id: str,
        document_id: str,
        reason: SkippedReason,
    ) -> SkippedVersion:
        """Build a :class:`SkippedVersion` payload + log the event.

        Centralised so every skip path emits the same structured event
        and so the admin response shape stays consistent regardless of
        which branch triggered the skip.
        """
        log.info(
            "hitl.auto_promote.version_skipped",
            extra={
                "document_id": document_id,
                "version_id": version_id,
                "reason": reason,
            },
        )
        return SkippedVersion(
            document_id=document_id,
            version_id=version_id,
            reason=reason,
        )

    def _build_version_index(self) -> dict[str, tuple[str, DocumentVersion]]:
        """Build a one-shot ``version_id → (document_id, version)`` map.

        Walks every document family the catalog knows about and
        materialises every version. The cost is O(catalog size) per
        pass — bounded by the pilot's small corpus and amortised
        across the whole pass (cheaper than calling ``get_version``
        per row, which would force one document fetch per pending row
        on the SQLite path).

        The return shape is a dict so per-row lookup is O(1). Versions
        are stored by reference (no copy) — they are read-only here.
        """
        index: dict[str, tuple[str, DocumentVersion]] = {}
        for document in self._catalog.list_documents():
            for version in document.versions:
                index[version.id] = (document.id, version)
        return index


__all__ = ["HITLAutoPromoter", "SYSTEM_ACTOR"]
