from typing import Literal

from app.schemas.semantic_document import SemanticDocument
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionJobService
from app.services.markdown_generator import MarkdownGenerator
from app.services.semantic_extractor import SemanticExtractor


class SemanticOutputService:
    """Caches generated semantic JSON and Markdown for document versions."""

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
        self.semantic_documents: dict[str, SemanticDocument] = {}

    def generate(self, document_id: str, version_id: str) -> SemanticDocument:
        """Generate semantic output once and return cached output afterward."""
        existing = self.semantic_documents.get(version_id)
        if existing is not None:
            return existing

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
        self.semantic_documents[version_id] = semantic
        self.documents.mark_semantic_ready(document_id=document_id, version_id=version_id)
        return semantic

    def get(self, document_id: str, version_id: str) -> SemanticDocument:
        """Return cached semantic output for a document version."""
        self.documents.get_version(document_id=document_id, version_id=version_id)
        semantic = self.semantic_documents.get(version_id)
        if semantic is None:
            raise KeyError("Semantic output not found.")
        return semantic

    def get_markdown(self, document_id: str, version_id: str) -> str:
        """Return cached Markdown output for a document version."""
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
        """Update the cached SemanticDocument to reflect a reviewer's decision.

        The DocumentVersion lifecycle status is updated separately via
        ``DocumentService.mark_validated/mark_rejected``; this method keeps the
        cached semantic JSON's ``validation_status`` in sync."""
        semantic = self.get(document_id=document_id, version_id=version_id)
        semantic.validation_status = status
        return semantic
