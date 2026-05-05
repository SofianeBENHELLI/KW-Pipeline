import logging
from typing import Literal

from app.schemas.semantic_document import SemanticDocument
from app.schemas.validation_metadata import ValidationMetadata
from app.services.confidence_scorer import ConfidenceScorer
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionJobService
from app.services.markdown_generator import MarkdownGenerator
from app.services.semantic_extractor import SemanticExtractor
from app.services.semantic_schema_loader import load_semantic_document
from app.services.validation_metadata_store import ValidationMetadataStore

log = logging.getLogger(__name__)


class SemanticOutputService:
    """Generates and persists semantic JSON + Markdown for document versions.

    Storage goes through the catalog store, so a process restart never
    loses generated artefacts — `get`, `get_markdown`, and `record_validation`
    all keep working after a fresh `build_persistent_services(...)`.

    EPIC-A slice 1 (ADR-023, #215) wires the HITL confidence scorer
    here as a fire-and-log side-effect of the NEEDS_REVIEW transition.
    Both ``confidence_scorer`` and ``validation_metadata_store`` are
    optional so existing tests and demos that construct this service
    directly keep working without the scorer collaborators; the
    ``build_services`` factory passes the canonical instances.
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
    ):
        self.documents = documents
        self.extraction_jobs = extraction_jobs
        self.semantic_extractor = semantic_extractor
        self.markdown_generator = markdown_generator
        self.confidence_scorer = confidence_scorer
        self.validation_metadata_store = validation_metadata_store

    def generate(self, document_id: str, version_id: str) -> SemanticDocument:
        """Generate semantic output once and return persisted output afterward."""
        try:
            payload = self.documents.catalog.get_semantic_document_payload(version_id)
            cached = load_semantic_document(payload)
            log.info(
                "semantic.cached",
                extra={
                    "document_id": document_id,
                    "version_id": version_id,
                    "section_count": len(cached.sections),
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
        semantic = self.semantic_extractor.extract(version=version, raw_extraction=raw_extraction)
        semantic.markdown = self.markdown_generator.render(
            version=version,
            semantic=semantic,
            raw_extraction=raw_extraction,
        )
        self.documents.catalog.save_semantic_document(version_id, semantic)
        self.documents.mark_semantic_ready(document_id=document_id, version_id=version_id)
        log.info(
            "semantic.generated",
            extra={
                "document_id": document_id,
                "version_id": version_id,
                "section_count": len(semantic.sections),
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
        """Run the HITL confidence scorer (ADR-023) if wired.

        No-op when the scorer or the metadata store is missing — the
        in-memory wiring without ``KW_HITL_DISABLE_SCORER`` set still
        constructs both, so the no-op branch only fires for tests
        that build this service by hand and don't pass the
        collaborators. Failures are caught and logged; the FSM
        transition stays durable.
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
        status: Literal["validated", "rejected"],
    ) -> SemanticDocument:
        """Update the persisted SemanticDocument to reflect a reviewer's decision.

        The DocumentVersion lifecycle status is updated separately via
        ``DocumentService.mark_validated/mark_rejected``; this method keeps the
        persisted semantic JSON's ``validation_status`` in sync."""
        semantic = self.get(document_id=document_id, version_id=version_id)
        semantic.validation_status = status
        self.documents.catalog.save_semantic_document(version_id, semantic)
        return semantic
