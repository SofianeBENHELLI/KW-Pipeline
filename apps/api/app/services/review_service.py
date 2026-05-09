"""Review-decision orchestration (audit P0 #223).

Encapsulates the validate / reject side-effect chain that previously
lived inline in ``apps/api/app/routes/lifecycle.py::_record_review``:

1. FSM transition (catalog ``mark_validated`` / ``mark_rejected``)
2. Semantic-output persistence (``record_validation``)
3. Knowledge-graph projection (fire-and-log; ADR-012)
4. LLM entity extraction (fire-and-log; ADR-013)

Why this lives in its own service
---------------------------------
Before this module landed, the route handler called four collaborators
in sequence and mapped their domain exceptions into HTTP envelopes
inline. That coupled the validation flow to FastAPI machinery, made
unit testing the side-effect chain impossible without a TestClient,
and was the only place in the codebase where a route handler did more
than translate inputs/outputs.

By moving the chain into ``ReviewService``, three things become
straightforward:

- **Direct unit testing.** A test constructs the service with
  in-memory collaborators and exercises ``handle_validation`` /
  ``handle_rejection`` without going through HTTP.
- **HITL routing extension (audit EPIC-A, #215).** The HITL router
  decision (human / external / auto) lives at the *call site* of
  this service — the route, plus the future
  ``KnowledgeChatService``-style adapters — without churning this
  module.
- **External-workflow integration (audit EPIC-B, #216).** The
  ITEROP adapter callback path constructs a ``ReviewResult``
  through the same service, so the FSM transition + side-effects
  happen identically whether the decision came from Orbital or
  ITEROP.

Error-mapping contract
----------------------
The service raises plain ``KeyError`` / ``ValueError``. The route
layer maps them to HTTP envelopes (404 for missing entities, 409 for
lifecycle conflict). Side-effect failures (projector, entity
extractor) are caught and logged inside the service — they NEVER
propagate, in keeping with the ADR-012 fire-and-log discipline that
keeps validation atomic from the catalog's point of view.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

from app.models.document import DocumentVersionStatus
from app.schemas.semantic_document import SemanticDocument
from app.services.document_service import DocumentService
from app.services.knowledge.entity_extractor import EntityExtractor
from app.services.knowledge.projector import KnowledgeProjector
from app.services.sampling_state_store import SamplingBucket, SamplingStateStore
from app.services.semantic_output_service import SemanticOutputService
from app.services.validation_metadata_store import ValidationMetadataStore

log = logging.getLogger(__name__)

ReviewDecision = Literal["validated", "rejected"]


class ReviewService:
    """Drive a NEEDS_REVIEW version to VALIDATED or REJECTED.

    Construct one per :class:`PipelineServices` container; the service
    is stateless (it only holds references to its collaborators) so a
    single instance can serve every request.

    HITL drift signal (EPIC-A A.3 part 2, ADR-023 §6)
    -------------------------------------------------
    When a human reviewer rejects a version that the router originally
    decided to auto-validate (i.e. ``ValidationMetadata.routing_decision
    == "auto"``), the rejection counts as a drift signal: the SPC
    sampler escalated this version to a human as a quality probe and
    the human disagreed with the router's auto-eligibility. We bump
    ``samples_human_after_auto`` for the bucket so the drift detector
    can ramp the bucket's sampling rate. Wiring is fire-and-log: a
    sampling-store hiccup must not roll back the rejection.
    """

    def __init__(
        self,
        *,
        documents: DocumentService,
        semantic_outputs: SemanticOutputService,
        knowledge_projector: KnowledgeProjector | None = None,
        entity_extractor: EntityExtractor | None = None,
        validation_metadata: ValidationMetadataStore | None = None,
        sampling_state: SamplingStateStore | None = None,
    ) -> None:
        self._documents = documents
        self._semantic_outputs = semantic_outputs
        self._knowledge_projector = knowledge_projector
        self._entity_extractor = entity_extractor
        # EPIC-A A.3 part 2 drift-signal collaborators. Both optional
        # so existing tests that build ``ReviewService`` by hand
        # without the HITL wiring keep working — the drift bump is a
        # no-op when either is missing.
        self._validation_metadata = validation_metadata
        self._sampling_state = sampling_state

    def handle_validation(
        self,
        *,
        document_id: str,
        version_id: str,
        reviewer_note: str | None = None,
        actor: str | None = None,
        side_effect_dispatcher: Callable[[Callable[[], None]], None] | None = None,
    ) -> SemanticDocument:
        """Drive a version from NEEDS_REVIEW to VALIDATED.

        Returns the persisted :class:`SemanticDocument` (with
        ``validation_status="validated"``). On success, fires the
        knowledge-graph projection and (if Phase 2 is wired) the LLM
        entity extraction as side-effects — both fire-and-log so the
        validation never rolls back if the side-effect fails.

        ``actor`` is the authenticated principal id (ADR-019 §4) and
        lands on the ``review.validated`` audit event so "who validated
        doc X" is a SQL query. ``None`` is allowed for callers that
        haven't been migrated yet — those paths land an audit row
        without an actor and the slicing plan in ADR-019 covers them
        next.

        ``side_effect_dispatcher`` controls *when* the projection +
        entity-extraction side-effects run. The default (``None``)
        runs them inline before this method returns — the historical
        contract that callers reading the graph immediately after
        validate rely on. Passing a callable hands the side-effect
        closure to the dispatcher (e.g. an asyncio background task);
        validate then returns as soon as the FSM transition is
        committed. Used by the route layer when
        ``KW_KNOWLEDGE_PROJECTION_ASYNC=true``.

        Raises:
            KeyError: when the document or version cannot be found.
            ValueError: when the version is not in NEEDS_REVIEW (the
                FSM precondition is documented in
                :class:`DocumentVersionStatus`).
        """
        return self._record_review(
            document_id=document_id,
            version_id=version_id,
            reviewer_note=reviewer_note,
            mark=self._documents.mark_validated,
            decision="validated",
            actor=actor,
            side_effect_dispatcher=side_effect_dispatcher,
        )

    def handle_rejection(
        self,
        *,
        document_id: str,
        version_id: str,
        reviewer_note: str | None = None,
        actor: str | None = None,
    ) -> SemanticDocument:
        """Drive a version from NEEDS_REVIEW to REJECTED.

        Returns the persisted :class:`SemanticDocument` (with
        ``validation_status="rejected"``). Rejection skips the
        knowledge-graph projection entirely — only validated content
        becomes graph knowledge (ADR-012's "nothing without provenance"
        rule).

        ``actor`` lands on the ``review.rejected`` audit event; see
        :meth:`handle_validation` for the contract.

        Raises:
            KeyError: when the document or version cannot be found.
            ValueError: when the version is not in NEEDS_REVIEW.
        """
        return self._record_review(
            document_id=document_id,
            version_id=version_id,
            reviewer_note=reviewer_note,
            mark=self._documents.mark_rejected,
            decision="rejected",
            actor=actor,
        )

    def _record_review(
        self,
        *,
        document_id: str,
        version_id: str,
        reviewer_note: str | None,
        mark: Callable[..., Any],
        decision: ReviewDecision,
        actor: str | None,
        side_effect_dispatcher: Callable[[Callable[[], None]], None] | None = None,
    ) -> SemanticDocument:
        # FSM precheck — the catalog's ``update_version_status`` enforces
        # this at write time too, but doing it here gives the caller a
        # clean ``ValueError`` mentioning the actual current status
        # before any side-effect runs.
        version = self._documents.get_version(
            document_id=document_id,
            version_id=version_id,
        )
        if version.status != DocumentVersionStatus.NEEDS_REVIEW:
            raise ValueError(
                f"Version is in {version.status.value}, not NEEDS_REVIEW; "
                f"cannot transition to {decision.upper()}."
            )

        # Touch the semantic document to confirm it exists; the caller
        # cares about a 404 here (no semantic to validate) before the
        # FSM transition fires.
        self._semantic_outputs.get(document_id=document_id, version_id=version_id)

        mark(
            document_id=document_id,
            version_id=version_id,
            reviewer_note=reviewer_note,
            actor=actor,
        )
        result = self._semantic_outputs.record_validation(
            document_id=document_id,
            version_id=version_id,
            status=decision,
        )

        if decision == "validated":
            # ADR-025: auto-transition the most recent prior VALIDATED
            # sibling (if any) to SUPERSEDED so catalog consumers see
            # only the latest validated version per family. Runs after
            # the catalog write that landed the new VALIDATED row, with
            # fire-and-log discipline (ADR-012): a supersede failure is
            # logged but never rolls the validation back.
            self._maybe_supersede_prior_validated(
                document_id=document_id,
                new_version_id=version_id,
                actor=actor,
            )

            def _run_side_effects() -> None:
                self._fire_knowledge_layer_side_effects(
                    document_id=document_id,
                    version=version,
                    semantic=result,
                )

            if side_effect_dispatcher is None:
                _run_side_effects()
            else:
                # Fire-and-forget: the dispatcher (e.g. an asyncio task
                # spawner) takes ownership. Side-effects are already
                # exception-isolated inside ``_fire_knowledge_layer_side_effects``,
                # so a dispatcher that just calls the closure later is
                # all that's needed.
                side_effect_dispatcher(_run_side_effects)
        else:
            # decision == "rejected". EPIC-A A.3 part 2 drift signal
            # (ADR-023 §6): if the router had decided to auto-validate
            # this version (i.e. SPC sampler escalated to human and the
            # human disagreed), bump the per-bucket drift counter. The
            # drift detector reads the counter on the next router pass
            # and ramps the bucket's sampling rate.
            self._maybe_record_drift_event(version=version, version_id=version_id)

        return result

    def _maybe_record_drift_event(self, *, version: Any, version_id: str) -> None:
        """Bump ``samples_human_after_auto`` if the rejection is a drift signal.

        Fire-and-log per ADR-012 §3 — a sampling-store hiccup must
        never roll back the rejection. The drift signal is best-effort
        observability; the catalog stays the source of truth.

        Drift signal definition (ADR-023 §6):
        - The router's persisted ``routing_decision`` was ``"auto"``,
          AND
        - ``validation_method`` is still unset (no auto-promotion yet —
          the SPC sampler escalated to a human review),
        - AND a human just rejected this version.

        That trio is the canonical "the router would have auto'd, the
        sampler probed, the human disagreed" event.
        """
        if self._validation_metadata is None or self._sampling_state is None:
            return
        try:
            metadata = self._validation_metadata.get(version_id)
        except Exception:  # noqa: BLE001 - fire-and-log boundary
            log.exception(
                "hitl.drift_signal.metadata_lookup_failed",
                extra={"version_id": version_id},
            )
            return
        if metadata is None:
            return
        if metadata.routing_decision != "auto":
            # Below-threshold + ocr_override + below-confidence rejections
            # don't count as drift — the router never thought this
            # version was auto-eligible in the first place.
            return
        if metadata.validation_method is not None:
            # The auto-promoter already promoted this row; we shouldn't
            # be in handle_rejection on an already-validated version
            # (the FSM blocks it). Defensive guard so a future race
            # doesn't double-count.
            return
        bucket = SamplingBucket.from_optional(
            content_type=version.content_type,
            topic_cluster=None,
        )
        try:
            self._sampling_state.record_drift_event(bucket=bucket)
            log.info(
                "hitl.drift_signal.recorded",
                extra={
                    "version_id": version_id,
                    "bucket_content_type": bucket.content_type,
                    "bucket_topic_cluster": bucket.topic_cluster,
                },
            )
        except Exception:  # noqa: BLE001 - fire-and-log boundary
            log.exception(
                "hitl.drift_signal.bump_failed",
                extra={"version_id": version_id},
            )

    def _maybe_supersede_prior_validated(
        self,
        *,
        document_id: str,
        new_version_id: str,
        actor: str | None,
    ) -> None:
        """Find the most recent prior VALIDATED sibling and mark it SUPERSEDED.

        Idempotent / no-op when:
        - the document family cannot be loaded (defensive);
        - there is no prior VALIDATED version (first validation of the
          family);
        - the prior VALIDATED is the version we just validated (race
          shouldn't happen, but the explicit skip keeps the contract
          obvious).

        Errors during the supersede transition are caught and logged —
        the validation MUST stay durable even if the supersede write
        fails (ADR-012 fire-and-log).
        """
        try:
            document = self._documents.get_document(document_id)
        except Exception:
            log.exception(
                "version.supersede.lookup_failed",
                extra={"document_id": document_id, "version_id": new_version_id},
            )
            return
        if document is None:
            return

        candidates = [
            sibling
            for sibling in document.versions
            if sibling.id != new_version_id and sibling.status == DocumentVersionStatus.VALIDATED
        ]
        if not candidates:
            return
        prior_validated = max(candidates, key=lambda v: v.version_number)

        try:
            self._documents.mark_superseded(
                document_id=document_id,
                version_id=prior_validated.id,
                actor=actor,
                superseded_by_version_id=new_version_id,
            )
        except Exception:
            log.exception(
                "version.supersede.failed",
                extra={
                    "document_id": document_id,
                    "version_id": prior_validated.id,
                    "superseded_by_version_id": new_version_id,
                },
            )

    def _fire_knowledge_layer_side_effects(
        self,
        *,
        document_id: str,
        version: Any,
        semantic: SemanticDocument,
    ) -> None:
        """Fire-and-log knowledge-layer side-effects after validation.

        Both stages (projection and entity extraction) catch every
        exception and log; the catalog stays the source of truth and
        the graph catches up via re-projection or out-of-band
        reconciliation (ADR-012 §3).
        """
        if self._knowledge_projector is None:
            return

        document_for_projection = None
        try:
            document_for_projection = self._documents.get_document(document_id)
            if document_for_projection is not None:
                self._knowledge_projector.project(
                    document=document_for_projection,
                    version=version,
                    semantic=semantic,
                )
        except Exception:
            log.exception(
                "knowledge.projection.failed",
                extra={"document_id": document_id, "version_id": version.id},
            )

        # Phase 2 (ADR-013): LLM-driven entity extraction. Same
        # fire-and-log discipline — extraction failures must not roll
        # back validation. Runs after projection so the entity edges
        # land in the same graph the projector just primed; the
        # projector's ``delete_subgraph_for_version`` already cleaned
        # old entity edges, so the upserts are against a fresh slate.
        if self._entity_extractor is None or document_for_projection is None:
            return

        try:
            extraction_result = self._entity_extractor.extract(
                document=document_for_projection,
                version=version,
                semantic=semantic,
            )
            self._knowledge_projector.project_entities(extraction_result)
            log.info(
                "knowledge.entity_extraction.completed",
                extra={
                    "document_id": document_id,
                    "version_id": version.id,
                    "triple_count": len(extraction_result.triples),
                    "warning_count": len(extraction_result.warnings),
                    "token_usage": extraction_result.token_usage,
                },
            )
        except Exception:
            log.exception(
                "knowledge.entity_extraction.failed",
                extra={
                    "document_id": document_id,
                    "version_id": version.id,
                },
            )
