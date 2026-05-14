import logging
from typing import Literal

from app.models.document import DocumentVersionStatus
from app.schemas.semantic_document import SemanticDocument
from app.schemas.validation_metadata import ConfidenceScore, ValidationMetadata
from app.services.confidence_scorer import ConfidenceScorer
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionJobService
from app.services.hitl_router import HITLRouter
from app.services.markdown_generator import MarkdownGenerator
from app.services.semantic_extractor import SemanticExtractor
from app.services.semantic_generators import (
    DEFAULT_SEMANTIC_METHOD,
    SEMANTIC_METHOD_STRUCTURE_FIRST,
    SemanticGenerator,
    StructureFirstSemanticGenerator,
)
from app.services.semantic_schema_loader import load_semantic_document
from app.services.validation_metadata_store import ValidationMetadataStore

log = logging.getLogger(__name__)


class UnknownSemanticMethod(KeyError):
    """Requested method id is not in the registry."""


class SemanticGenerationFailed(RuntimeError):
    """The underlying generator raised on this version."""


class SemanticOutputService:
    """Generates and persists semantic JSON + Markdown for document versions.

    Storage goes through the catalog store, so a process restart never
    loses generated artefacts — `get`, `get_markdown`, and `record_validation`
    all keep working after a fresh `build_persistent_services(...)`.

    EPIC-A slice 1 (ADR-023, #215) wires the HITL confidence scorer
    here as a fire-and-log side-effect of the NEEDS_REVIEW transition.
    Slice 2 adds the :class:`HITLRouter` call right after the scorer:
    the router reads the just-persisted score, picks a routing
    decision, and writes the decision back to ``ValidationMetadata``.
    Both ``confidence_scorer`` and ``validation_metadata_store`` are
    optional so existing tests and demos that construct this service
    directly keep working without the scorer collaborators; the
    ``build_services`` factory passes the canonical instances. The
    router collaborator is also optional for the same reason — the
    router only fires when both the scorer and the router are wired,
    which is the production posture.
    """

    def __init__(
        self,
        documents: DocumentService,
        extraction_jobs: ExtractionJobService,
        semantic_extractor: SemanticExtractor,
        markdown_generator: MarkdownGenerator,
        *,
        confidence_scorer: ConfidenceScorer | None = None,
        validation_metadata_store: ValidationMetadataStore | None = None,
        hitl_router: HITLRouter | None = None,
        generators: dict[str, SemanticGenerator] | None = None,
    ):
        self.documents = documents
        self.extraction_jobs = extraction_jobs
        self.semantic_extractor = semantic_extractor
        self.markdown_generator = markdown_generator
        self.confidence_scorer = confidence_scorer
        self.validation_metadata_store = validation_metadata_store
        self.hitl_router = hitl_router
        # Registry keyed by method id. The legacy ``semantic_extractor``
        # arg is wrapped in a Deterministic adapter so the default
        # method is always present — callers that don't pass
        # ``generators`` keep the pre-method-dispatch behaviour exactly.
        registry: dict[str, SemanticGenerator] = {
            SEMANTIC_METHOD_STRUCTURE_FIRST: StructureFirstSemanticGenerator(
                extractor=semantic_extractor,
            ),
        }
        if generators:
            registry.update(generators)
        self._generators = registry

    @property
    def available_methods(self) -> list[str]:
        """Ordered list of registered method ids (default first)."""
        ordered: list[str] = [SEMANTIC_METHOD_STRUCTURE_FIRST]
        for name in self._generators:
            if name != SEMANTIC_METHOD_STRUCTURE_FIRST:
                ordered.append(name)
        return ordered

    def generate(
        self,
        document_id: str,
        version_id: str,
        *,
        method: str | None = None,
    ) -> SemanticDocument:
        """Generate semantic output once and return persisted output afterward.

        When ``method`` is omitted the existing cache-first behaviour
        applies and the deterministic generator runs on a cache miss.
        When ``method`` is supplied it must resolve in the registry; a
        cached row whose ``extraction_method`` matches is returned
        unchanged, otherwise the chosen generator runs and overwrites
        the persisted row.
        """
        requested_method = method or DEFAULT_SEMANTIC_METHOD
        if requested_method not in self._generators:
            raise UnknownSemanticMethod(requested_method)

        try:
            payload = self.documents.catalog.get_semantic_document_payload(version_id)
            cached = load_semantic_document(payload)
            # When the caller didn't ask for a specific method, the
            # cached row wins regardless of how it was produced — that's
            # the pre-method-dispatch contract.
            # When the caller did ask, the cached row only short-circuits
            # if its recorded method matches.
            if method is None or (
                cached.extraction_method == requested_method
                or (
                    cached.extraction_method is None
                    and requested_method == SEMANTIC_METHOD_STRUCTURE_FIRST
                )
            ):
                log.info(
                    "semantic.cached",
                    extra={
                        "document_id": document_id,
                        "version_id": version_id,
                        "section_count": len(cached.sections),
                        "method": cached.extraction_method,
                    },
                )
                return cached
        except KeyError:
            pass  # nothing cached yet — generate below

        raw_extraction = self.extraction_jobs.get_raw_extraction(
            document_id=document_id,
            version_id=version_id,
        )
        version = self.documents.get_version(document_id=document_id, version_id=version_id)
        generator = self._generators[requested_method]
        try:
            semantic = generator.generate(
                version=version, raw_extraction=raw_extraction
            )
        except Exception as exc:  # noqa: BLE001 - boundary
            log.warning(
                "semantic.generation_failed",
                extra={
                    "document_id": document_id,
                    "version_id": version_id,
                    "method": requested_method,
                    "error_type": type(exc).__name__,
                },
            )
            raise SemanticGenerationFailed(
                f"Semantic generator {requested_method!r} failed: {exc}"
            ) from exc
        # Belt-and-braces — every generator is expected to stamp the
        # method id itself, but enforce it here so a misbehaving
        # generator can't desync the registry from the persisted row.
        if semantic.extraction_method != requested_method:
            semantic = semantic.model_copy(
                update={"extraction_method": requested_method}
            )
        semantic.markdown = self.markdown_generator.render(
            version=version,
            semantic=semantic,
            raw_extraction=raw_extraction,
        )
        self.documents.catalog.save_semantic_document(version_id, semantic)
        # Only fire ``EXTRACTED → NEEDS_REVIEW`` when the version is
        # still pre-review. Method-switch regeneration on a version
        # that's already NEEDS_REVIEW / SEMANTIC_READY / VALIDATED /
        # REJECTED keeps the FSM state intact — the operator changed
        # the semantic shape, not the lifecycle decision. The catalog
        # row is rewritten regardless above.
        if version.status == DocumentVersionStatus.EXTRACTED:
            self.documents.mark_semantic_ready(
                document_id=document_id, version_id=version_id
            )
        log.info(
            "semantic.generated",
            extra={
                "document_id": document_id,
                "version_id": version_id,
                "section_count": len(semantic.sections),
                "method": requested_method,
                "fsm_transitioned": version.status == DocumentVersionStatus.EXTRACTED,
            },
        )
        # EPIC-A slice 1 (ADR-023): score the version that just landed
        # in NEEDS_REVIEW and persist the breakdown to
        # ``validation_metadata``. Fire-and-log per ADR-012 §3 — a
        # scorer hiccup must NOT roll back the FSM transition. The
        # next-slice ``hitl_router.py`` reads the persisted metadata
        # to make a routing decision; this slice only writes data.
        self._maybe_score_for_hitl(
            document_id=document_id,
            version_id=version_id,
            semantic=semantic,
        )
        return semantic

    def _maybe_score_for_hitl(
        self,
        *,
        document_id: str,
        version_id: str,
        semantic: SemanticDocument,
    ) -> None:
        """Run the HITL scorer + (slice 2) router if wired.

        No-op when the scorer or the metadata store is missing — the
        in-memory wiring without ``KW_HITL_DISABLE_SCORER`` set still
        constructs both, so the no-op branch only fires for tests
        that build this service by hand and don't pass the
        collaborators. Failures are caught and logged; the FSM
        transition stays durable.

        Slice 2 (this slice) adds the router call: after the
        ``confidence.scored`` event lands, we run :class:`HITLRouter`
        to pick auto/human/external, persist the decision back to
        ``ValidationMetadata.routing_decision``, and emit a
        ``routing.decided`` audit event. The router does NOT
        transition the FSM — that's the next slice's
        auto-promotion worker. The actor on the audit event is
        ``"system"`` because this hook fires from the FSM
        path which doesn't carry a ``current_user`` (the worker
        slice will preserve the actor when it calls
        ``ReviewService.handle_validation``).
        """
        if self.confidence_scorer is None or self.validation_metadata_store is None:
            return
        try:
            version = self.documents.get_version(document_id=document_id, version_id=version_id)
            score = self.confidence_scorer.score(
                version=version,
                semantic=semantic,
            )
            self.validation_metadata_store.upsert(
                ValidationMetadata(
                    version_id=version_id,
                    confidence_score=score,
                ),
            )
            log.info(
                "confidence.scored",
                extra={
                    "document_id": document_id,
                    "version_id": version_id,
                    "overall": score.overall,
                    "signals": score.signals,
                    "weights": score.weights,
                    "ocr_override_active": score.ocr_override_active,
                    "computed_by_version": score.computed_by_version,
                },
            )
        except Exception:  # noqa: BLE001 - fire-and-log boundary
            log.exception(
                "confidence.scoring_failed",
                extra={"document_id": document_id, "version_id": version_id},
            )
            return
        # Slice 2: route the just-scored version. We do this in a
        # second try-block so a router-internal failure (e.g. SPC
        # state-store outage) does NOT undo the persisted score —
        # the score is the more important record from an audit
        # perspective and the router can be re-run by the next-slice
        # worker against the persisted score.
        self._maybe_route_for_hitl(
            document_id=document_id,
            version_id=version_id,
            score=score,
            semantic=semantic,
        )

    def _maybe_route_for_hitl(
        self,
        *,
        document_id: str,
        version_id: str,
        score: ConfidenceScore,
        semantic: SemanticDocument,
    ) -> None:
        """Run the HITL router on a freshly-persisted score.

        Same fire-and-log discipline as :meth:`_maybe_score_for_hitl`:
        a router hiccup must not roll back the FSM transition or the
        score persistence. The router is a no-op when not wired
        (same as the scorer) so tests that build the service by
        hand without a router keep working.

        The router's ``content_type`` is sourced from the
        :class:`DocumentVersion`; the ``topic_cluster`` is left empty
        for now — the next slice's worker (drift detector) will
        derive the dominant topic cluster from the projected graph
        and persist a richer bucket. Today the SPC sampler keys on
        ``(content_type, "_unknown_")`` for every version, which is
        a coarser bucket than the ADR's eventual goal but matches
        the corpus_norms wiring's "unknown bucket scores 1.0"
        cold-start posture.
        """
        if self.hitl_router is None or self.validation_metadata_store is None:
            return
        try:
            decision = self.hitl_router.decide(
                score=score,
                content_type=self._content_type_for_version(
                    document_id=document_id, version_id=version_id
                ),
                topic_cluster=None,
            )
            # Re-upsert with the routing decision filled in. The
            # validation_method stays None — the auto-promotion
            # worker (next slice) is the one that flips it to
            # ``"auto"`` after calling ``ReviewService.handle_validation``,
            # and the human path leaves it None until a reviewer
            # acts in Orbital.
            self.validation_metadata_store.upsert(
                ValidationMetadata(
                    version_id=version_id,
                    confidence_score=score,
                    routing_decision=decision.method,
                ),
            )
            log.info(
                "routing.decided",
                extra={
                    "document_id": document_id,
                    "version_id": version_id,
                    "method": decision.method,
                    "reason": decision.reason,
                    "score_overall": decision.score_overall,
                    "threshold": decision.threshold,
                    "bucket_content_type": decision.bucket[0],
                    "bucket_topic_cluster": decision.bucket[1],
                    "actor": "system",
                },
            )
        except Exception:  # noqa: BLE001 - fire-and-log boundary
            log.exception(
                "routing.decision_failed",
                extra={"document_id": document_id, "version_id": version_id},
            )

    def _content_type_for_version(self, *, document_id: str, version_id: str) -> str:
        """Read the content_type off the catalog row.

        Pulled into a helper so the (failure-tolerant) router branch
        doesn't pay a second ``get_version`` if the score branch
        already fetched the version — but in practice the catalog
        read is in-memory cheap and fetching once-per-branch keeps
        the score-vs-route branches independent under failure.
        """
        version = self.documents.get_version(document_id=document_id, version_id=version_id)
        return version.content_type

    def get(self, document_id: str, version_id: str) -> SemanticDocument:
        """Return persisted semantic output for a document version.

        The catalog returns the raw JSON payload; the schema loader is the
        single boundary that produces a typed ``SemanticDocument``. Per
        ADR-008, this lets older payloads route through registered
        migrators when the schema evolves.
        """
        self.documents.get_version(document_id=document_id, version_id=version_id)
        payload = self.documents.catalog.get_semantic_document_payload(version_id)
        return load_semantic_document(payload)

    def get_markdown(self, document_id: str, version_id: str) -> str:
        """Return persisted Markdown output for a document version."""
        semantic = self.get(document_id=document_id, version_id=version_id)
        if semantic.markdown is None:
            raise KeyError("Markdown output not found.")
        return semantic.markdown

    def record_validation(
        self,
        document_id: str,
        version_id: str,
        status: Literal["validated", "rejected", "needs_review"],
    ) -> SemanticDocument:
        """Update the persisted SemanticDocument to reflect a reviewer's decision.

        The DocumentVersion lifecycle status is updated separately via
        ``DocumentService.mark_validated/mark_rejected/mark_demoted_to_review``;
        this method keeps the persisted semantic JSON's ``validation_status``
        in sync. ``"needs_review"`` covers the demote path
        (VALIDATED/REJECTED → NEEDS_REVIEW)."""
        semantic = self.get(document_id=document_id, version_id=version_id)
        semantic.validation_status = status
        self.documents.catalog.save_semantic_document(version_id, semantic)
        return semantic
