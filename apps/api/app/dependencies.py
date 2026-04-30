from dataclasses import dataclass
from pathlib import Path

from app.services.catalog_store import SQLiteCatalogStore
from app.services.document_parser import ParserRegistry, PlainTextParser
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionJobService
from app.services.markdown_generator import MarkdownGenerator
from app.services.semantic_extractor import SemanticExtractor
from app.services.semantic_output_service import SemanticOutputService
from app.services.storage_service import (
    FileSystemStorageService,
    InMemoryStorageService,
    StorageService,
)


@dataclass(frozen=True)
class PipelineServices:
    """Service container for one isolated Harvester API instance.

    The MVP uses in-memory services so tests and local demos can run without
    PostgreSQL, object storage, or a queue. Keeping construction centralized
    makes the later swap to persistent adapters explicit.
    """

    storage: StorageService
    documents: DocumentService
    parsers: ParserRegistry
    extraction_jobs: ExtractionJobService
    semantic_extractor: SemanticExtractor
    markdown_generator: MarkdownGenerator
    semantic_outputs: SemanticOutputService


def build_services() -> PipelineServices:
    """Create fresh in-memory services for tests and ephemeral demos."""
    storage = InMemoryStorageService()
    documents = DocumentService(storage=storage)
    parsers = ParserRegistry([PlainTextParser()])
    extraction_jobs = ExtractionJobService(documents=documents, parsers=parsers)
    semantic_extractor = SemanticExtractor()
    markdown_generator = MarkdownGenerator()
    return PipelineServices(
        storage=storage,
        documents=documents,
        parsers=parsers,
        extraction_jobs=extraction_jobs,
        semantic_extractor=semantic_extractor,
        markdown_generator=markdown_generator,
        semantic_outputs=SemanticOutputService(
            documents=documents,
            extraction_jobs=extraction_jobs,
            semantic_extractor=semantic_extractor,
            markdown_generator=markdown_generator,
        ),
    )


def build_persistent_services(data_dir: Path | str = ".kw-pipeline") -> PipelineServices:
    """Create local persistent services backed by SQLite and filesystem storage."""
    root = Path(data_dir)
    storage = FileSystemStorageService(root=root / "raw")
    documents = DocumentService(
        storage=storage,
        catalog=SQLiteCatalogStore(root / "catalog.sqlite3"),
    )
    parsers = ParserRegistry([PlainTextParser()])
    extraction_jobs = ExtractionJobService(documents=documents, parsers=parsers)
    semantic_extractor = SemanticExtractor()
    markdown_generator = MarkdownGenerator()
    return PipelineServices(
        storage=storage,
        documents=documents,
        parsers=parsers,
        extraction_jobs=extraction_jobs,
        semantic_extractor=semantic_extractor,
        markdown_generator=markdown_generator,
        semantic_outputs=SemanticOutputService(
            documents=documents,
            extraction_jobs=extraction_jobs,
            semantic_extractor=semantic_extractor,
            markdown_generator=markdown_generator,
        ),
    )
