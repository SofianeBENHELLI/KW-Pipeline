from dataclasses import dataclass, field
from pathlib import Path

from app.services.catalog_store import SQLiteCatalogStore
from app.services.document_parser import ParserRegistry, PlainTextParser
from app.services.document_service import DocumentService
from app.services.enrichers import RuleBasedEntityEnricher
from app.services.extraction_job_service import ExtractionJobService
from app.services.idempotency_store import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
    SQLiteIdempotencyStore,
)
from app.services.knowledge import (
    AnthropicLLMClient,
    EntityExtractor,
    GraphStore,
    InMemoryGraphStore,
    KnowledgeProjector,
    Neo4jGraphStore,
)
from app.services.markdown_generator import MarkdownGenerator
from app.services.parsers import DocxParser, PdfParser, PptxParser
from app.services.semantic_extractor import SemanticExtractor
from app.services.semantic_output_service import SemanticOutputService
from app.services.storage_service import (
    FileSystemStorageService,
    InMemoryStorageService,
    StorageService,
)
from app.settings import Settings


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
    # Phase 2 (ADR-013): LLM-driven entity extraction. Constructed iff
    # ``KW_KNOWLEDGE_LAYER_ENABLED=true`` AND ``ANTHROPIC_API_KEY`` is
    # set; otherwise None and the route layer treats entity extraction
    # as disabled — Phase 1a behaviour is preserved.
    entity_extractor: EntityExtractor | None = None
    # Snapshot of the typed settings used to construct this container
    # (issue #43). Routes read settings *fresh per request* via
    # ``Settings()`` so per-test ``monkeypatch.setenv`` is observable;
    # this field exists so deployment-time configuration (e.g. a
    # programmatically-constructed Settings) can be threaded through
    # ``build_services(settings=...)``.
    settings: Settings = field(default_factory=Settings)


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
            PptxParser(),
        ]
    )


def _maybe_build_entity_extractor(settings: Settings | None = None) -> EntityExtractor | None:
    """Build the LLM-driven entity extractor if enabled (ADR-013).

    Returns ``None`` unless **both** the knowledge-layer kill switch is
    truthy **and** the Anthropic API key is set. The Phase 1a-only path
    (graph projection without entities) is preserved when the API key
    is absent so contributors who don't have an Anthropic account can
    still run the knowledge layer end-to-end against the in-memory
    graph store.

    All env reads flow through :class:`app.settings.Settings` (issue
    #43); the legacy unprefixed ``ANTHROPIC_API_KEY`` keeps working
    via :class:`pydantic.AliasChoices`.
    """
    settings = settings or Settings()
    if not settings.knowledge_layer_enabled or not settings.anthropic_api_key.strip():
        return None
    api_key = settings.anthropic_api_key.strip()
    model = settings.anthropic_model.strip() or None
    llm = (
        AnthropicLLMClient(api_key=api_key, model=model)
        if model
        else AnthropicLLMClient(api_key=api_key)
    )
    # ADR-014 §3 circuit breaker. ``0`` means disabled; any positive
    # value caps cumulative input_tokens per document.
    cap = settings.entity_extractor_max_input_tokens_per_document
    return EntityExtractor(
        llm=llm,
        max_input_tokens_per_document=cap if cap > 0 else None,
    )


def _maybe_build_knowledge_layer(
    settings: Settings | None = None,
) -> tuple[GraphStore, KnowledgeProjector | None]:
    """Build the knowledge graph store + projector based on settings.

    Reads ``KW_KNOWLEDGE_LAYER_ENABLED`` and the ``KW_NEO4J_*`` family
    via :class:`app.settings.Settings`. The defaults — knowledge layer
    disabled, in-memory store — preserve the existing pipeline's
    behaviour: no Neo4j needed to run the API or its tests.

    Returns the active ``GraphStore`` (always non-None so the read
    routes have something to query, even if it's empty) plus an
    optional ``KnowledgeProjector`` (``None`` when the layer is
    disabled).
    """
    settings = settings or Settings()
    if not settings.knowledge_layer_enabled:
        return InMemoryGraphStore(), None

    uri = settings.neo4j_uri.strip()
    user = settings.neo4j_user.strip()
    if uri and user:
        store: GraphStore = Neo4jGraphStore(
            uri=uri,
            user=user,
            password=settings.neo4j_password,
            database=settings.neo4j_database or "neo4j",
        )
    else:
        # ``KW_KNOWLEDGE_LAYER_ENABLED=true`` without Neo4j config still
        # turns on projection — useful for in-process demos and tests
        # without spinning up a database.
        store = InMemoryGraphStore()
    return store, KnowledgeProjector(graph_store=store)


def build_services(settings: Settings | None = None) -> PipelineServices:
    """Create fresh in-memory services for tests and ephemeral demos.

    Accepts an optional ``settings`` so callers (typically deployment
    wiring, not tests) can pass an already-validated configuration.
    Defaults to ``Settings()``, which reads the current process env.
    """
    settings = settings or Settings()
    storage = InMemoryStorageService()
    documents = DocumentService(storage=storage)
    parsers = _build_parser_registry()
    extraction_jobs = ExtractionJobService(documents=documents, parsers=parsers)
    # Default enricher chain: deterministic rule-based entity extraction
    # (#48). Pure regex / no model dep, safe to run on every wiring path
    # including the in-memory unit suite.
    semantic_extractor = SemanticExtractor(enrichers=[RuleBasedEntityEnricher()])
    markdown_generator = MarkdownGenerator()
    graph_store, knowledge_projector = _maybe_build_knowledge_layer(settings)
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
        entity_extractor=_maybe_build_entity_extractor(settings),
        settings=settings,
    )


def build_persistent_services(
    data_dir: Path | str = ".kw-pipeline",
    settings: Settings | None = None,
) -> PipelineServices:
    """Create local persistent services backed by SQLite and filesystem storage."""
    settings = settings or Settings()
    root = Path(data_dir)
    storage = FileSystemStorageService(root=root / "raw")
    documents = DocumentService(
        storage=storage,
        catalog=SQLiteCatalogStore(root / "catalog.sqlite3"),
    )
    parsers = _build_parser_registry()
    extraction_jobs = ExtractionJobService(documents=documents, parsers=parsers)
    # Default enricher chain: deterministic rule-based entity extraction
    # (#48). Pure regex / no model dep, safe to run on every wiring path
    # including the in-memory unit suite.
    semantic_extractor = SemanticExtractor(enrichers=[RuleBasedEntityEnricher()])
    markdown_generator = MarkdownGenerator()
    graph_store, knowledge_projector = _maybe_build_knowledge_layer(settings)
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
        entity_extractor=_maybe_build_entity_extractor(settings),
        settings=settings,
    )
