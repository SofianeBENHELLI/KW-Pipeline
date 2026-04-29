from dataclasses import dataclass

from app.services.document_parser import PlainTextParser
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionJobService
from app.services.markdown_generator import MarkdownGenerator
from app.services.semantic_extractor import SemanticExtractor
from app.services.storage_service import InMemoryStorageService


@dataclass(frozen=True)
class PipelineServices:
    """Service container for one isolated Harvester API instance.

    The MVP uses in-memory services so tests and local demos can run without
    PostgreSQL, object storage, or a queue. Keeping construction centralized
    makes the later swap to persistent adapters explicit.
    """

    storage: InMemoryStorageService
    documents: DocumentService
    parser: PlainTextParser
    extraction_jobs: ExtractionJobService
    semantic_extractor: SemanticExtractor
    markdown_generator: MarkdownGenerator


def build_services() -> PipelineServices:
    """Create a fresh set of pipeline services with shared dependencies."""
    storage = InMemoryStorageService()
    documents = DocumentService(storage=storage)
    parser = PlainTextParser()
    return PipelineServices(
        storage=storage,
        documents=documents,
        parser=parser,
        extraction_jobs=ExtractionJobService(documents=documents, parser=parser),
        semantic_extractor=SemanticExtractor(),
        markdown_generator=MarkdownGenerator(),
    )

