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
from app.services.semantic_output_service import SemanticOutputService

log = logging.getLogger(__name__)

ReviewDecision = Literal["validated", "rejected"]


class ReviewService:
    """Drive a NEEDS_REVIEW version to VALIDATED or REJECTED.

    Construct one per :class:`PipelineServices` container; the service
    is stateless (it only holds references to its collaborators) so a
    single instance can serve every request.
    """

    def __init__(
        self,
        *,
        documents: DocumentService,
        semantic_outputs: SemanticOutputService,
        knowledge_projector: KnowledgeProjector | None = None,
        entity_extractor: EntityExtractor | None = None,
    ) -> None:
        self._documents = documents
        self._semantic_outputs = semantic_outputs
        self._knowledge_projector = knowledge_projector
        self._entity_extractor = entity_extractor

    def handle_validation(
        self,
        *,
        document_id: str,
        version_id: str,
        reviewer_note: str | None = None,
    ) -> SemanticDocument:
        """Drive a version from NEEDS_REVIEW to VALIDATED.

        Returns the persisted :class:`SemanticDocument` (with
        ``validation_status="validated"``). On success, fires the
        knowledge-graph projection and (if Phase 2 is wired) the LLM
        entity extraction as side-effects — both fire-and-log so the
        validation never rolls back if the side-effect fails.

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
        )

    def handle_rejection(
        self,
        *,
        document_id: str,
        version_id: str,
        reviewer_note: str | None = None,
    ) -> SemanticDocument:
        """Drive a version from NEEDS_REVIEW to REJECTED.

        Returns the persisted :class:`SemanticDocument` (with
        ``validation_status="rejected"``). Rejection skips the
        knowledge-graph projection entirely — only validated content
        becomes graph knowledge (ADR-012's "nothing without provenance"
        rule).

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
        )

    def _record_review(
        self,
        *,
        document_id: str,
        version_id: str,
        reviewer_note: str | None,
        mark: Callable[..., Any],
        decision: ReviewDecision,
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
        )
        result = self._semantic_outputs.record_validation(
            document_id=document_id,
            version_id=version_id,
            status=decision,
        )

        if decision == "validated":
            self._fire_knowledge_layer_side_effects(
                document_id=document_id,
                version=version,
                semantic=result,
            )

        return result

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
