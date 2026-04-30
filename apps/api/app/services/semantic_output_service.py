from typing import Literal

from app.schemas.semantic_document import SemanticDocument
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionJobService
from app.services.markdown_generator import MarkdownGenerator
from app.services.semantic_extractor import SemanticExtractor
from app.services.semantic_schema_loader import load_semantic_document


class SemanticOutputService:
    """Generates and persists semantic JSON + Markdown for document versions.

    Storage goes through the catalog store, so a process restart never
    loses generated artefacts — `get`, `get_markdown`, and `record_validation`
    all keep working after a fresh `build_persistent_services(...)`.
    """

    def __init__(
        self,
        documents: DocumentService,
        extraction_jobs: ExtractionJobService,
        semantic_extractor: SemanticExtractor,
        markdown_generator: MarkdownGenerator,
    ):
        self.documents = documents
        self.extraction_jobs = extraction_jobs
        self.semantic_extractor = semantic_extractor
        self.markdown_generator = markdown_generator

    def generate(self, document_id: str, version_id: str) -> SemanticDocument:
        """Generate semantic output once and return persisted output afterward."""
        try:
            payload = self.documents.catalog.get_semantic_document_payload(version_id)
            return load_semantic_document(payload)
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
        return semantic

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
