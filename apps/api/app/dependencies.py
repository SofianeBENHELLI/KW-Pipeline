import os
from dataclasses import dataclass, field
from pathlib import Path

from app.services.catalog_store import SQLiteCatalogStore
from app.services.document_parser import ParserRegistry, PlainTextParser
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionJobService
from app.services.idempotency_store import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
    SQLiteIdempotencyStore,
)
from app.services.knowledge import (
    GraphStore,
    InMemoryGraphStore,
    KnowledgeProjector,
    Neo4jGraphStore,
)
from app.services.markdown_generator import MarkdownGenerator
from app.services.parsers import DocxParser, PdfParser
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
    idempotency: IdempotencyStore = field(default_factory=InMemoryIdempotencyStore)
    # Knowledge layer (ADR-012). The projector is a fire-and-log
    # side-effect of validation; if it is None, the route layer treats
    # graph projection as disabled and the existing pipeline behaves
    # identically to before. ``graph_store`` exposes the read shapes
    # for the new ``GET /documents/{id}/graph`` and ``GET /knowledge/graph``
    # routes.
    graph_store: GraphStore = field(default_factory=InMemoryGraphStore)
    knowledge_projector: KnowledgeProjector | None = None


def _build_parser_registry() -> ParserRegistry:
    """Construct the parser registry shared by both wirings.

    Append new parsers to the end of the list; ``ParserRegistry`` resolves
    by content type and the parsers here advertise disjoint
    ``supported_content_types`` so order is not load-bearing for behaviour,
    only for diff-collision avoidance.
    """
    return ParserRegistry(
        [
            PlainTextParser(),
            DocxParser(),
            PdfParser(),
        ]
    )


def _maybe_build_knowledge_layer() -> tuple[GraphStore, KnowledgeProjector | None]:
    """Build the knowledge graph store + projector based on env vars.

    Reads ``KW_KNOWLEDGE_LAYER_ENABLED`` and the ``KW_NEO4J_*`` family
    at process start. The defaults — knowledge layer disabled, in-memory
    store — preserve the existing pipeline's behaviour: no Neo4j needed
    to run the API or its tests.

    Returns the active ``GraphStore`` (always non-None so the read
    routes have something to query, even if it's empty) plus an
    optional ``KnowledgeProjector`` (``None`` when the layer is
    disabled).
    """
    enabled = os.environ.get("KW_KNOWLEDGE_LAYER_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled:
        return InMemoryGraphStore(), None

    uri = os.environ.get("KW_NEO4J_URI", "").strip()
    user = os.environ.get("KW_NEO4J_USER", "").strip()
    password = os.environ.get("KW_NEO4J_PASSWORD", "")
    if uri and user:
        store: GraphStore = Neo4jGraphStore(
            uri=uri,
            user=user,
            password=password,
            database=os.environ.get("KW_NEO4J_DATABASE", "neo4j"),
        )
    else:
        # ``KW_KNOWLEDGE_LAYER_ENABLED=true`` without Neo4j config still
        # turns on projection — useful for in-process demos and tests
        # without spinning up a database.
        store = InMemoryGraphStore()
    return store, KnowledgeProjector(graph_store=store)


def build_services() -> PipelineServices:
    """Create fresh in-memory services for tests and ephemeral demos."""
    storage = InMemoryStorageService()
    documents = DocumentService(storage=storage)
    parsers = _build_parser_registry()
    extraction_jobs = ExtractionJobService(documents=documents, parsers=parsers)
    semantic_extractor = SemanticExtractor(enrichers=[])
    markdown_generator = MarkdownGenerator()
    graph_store, knowledge_projector = _maybe_build_knowledge_layer()
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
        idempotency=InMemoryIdempotencyStore(),
        graph_store=graph_store,
        knowledge_projector=knowledge_projector,
    )


def build_persistent_services(data_dir: Path | str = ".kw-pipeline") -> PipelineServices:
    """Create local persistent services backed by SQLite and filesystem storage."""
    root = Path(data_dir)
    storage = FileSystemStorageService(root=root / "raw")
    documents = DocumentService(
        storage=storage,
        catalog=SQLiteCatalogStore(root / "catalog.sqlite3"),
    )
    parsers = _build_parser_registry()
    extraction_jobs = ExtractionJobService(documents=documents, parsers=parsers)
    semantic_extractor = SemanticExtractor(enrichers=[])
    markdown_generator = MarkdownGenerator()
    graph_store, knowledge_projector = _maybe_build_knowledge_layer()
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
        idempotency=SQLiteIdempotencyStore(root / "idempotency.sqlite3"),
        graph_store=graph_store,
        knowledge_projector=knowledge_projector,
    )
