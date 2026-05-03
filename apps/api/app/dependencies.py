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
    ChatService,
    EmbeddingClient,
    EntityExtractor,
    GraphStore,
    InMemoryGraphStore,
    KnowledgeProjector,
    KnowledgeSearchService,
    LLMClient,
    Neo4jGraphStore,
    VoyageEmbeddingClient,
)
from app.services.knowledge.llm_client import DEFAULT_ANTHROPIC_MODEL
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
    # Phase 3 (ADR-015, #186): vector RAG. ``embedding_client`` is
    # constructed iff ``KW_KNOWLEDGE_LAYER_ENABLED=true`` AND
    # ``VOYAGE_API_KEY`` is set; ``knowledge_search`` requires the
    # client. Both ``None`` keeps Phase 1 + Phase 2 behaviour exactly:
    # the projector skips the embedding write, the search route
    # returns 503.
    embedding_client: EmbeddingClient | None = None
    knowledge_search: KnowledgeSearchService | None = None
    # Phase 3 chat surface (RAG mode). ``chat`` is constructed iff
    # **both** the search service AND an entity-extractor LLM client
    # are wired (i.e. ``KW_KNOWLEDGE_LAYER_ENABLED=true`` AND
    # ``VOYAGE_API_KEY`` AND ``ANTHROPIC_API_KEY`` are all set).
    # ``None`` keeps the route at 503 with the public error envelope.
    chat: ChatService | None = None
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


def _maybe_build_embedding_client(
    settings: Settings | None = None,
) -> EmbeddingClient | None:
    """Build the Voyage embedding client iff Phase 3 is configured (ADR-015).

    Returns ``None`` unless **both** the knowledge-layer kill switch is
    truthy **and** ``VOYAGE_API_KEY`` is set. When ``None`` is
    returned, the projector skips its embedding write path and the
    ``GET /knowledge/search`` route returns 503 — Phase 1 / Phase 2
    behaviour is preserved exactly.
    """
    settings = settings or Settings()
    if not settings.knowledge_layer_enabled or not settings.voyage_api_key.strip():
        return None
    api_key = settings.voyage_api_key.strip()
    # ``Settings.embedding_model`` defaults to ``"voyage-3"`` per
    # ADR-015, so this is always truthy in production. We still tolerate
    # an explicit empty override for forward-compat (a future "infer
    # model from API key" path), at which point the SDK's own default
    # kicks in via ``VoyageEmbeddingClient``'s constructor default.
    model = settings.embedding_model.strip() or None
    return (
        VoyageEmbeddingClient(api_key=api_key, model=model)
        if model
        else VoyageEmbeddingClient(api_key=api_key)
    )


def _maybe_build_chat_service(
    *,
    settings: Settings,
    knowledge_search: KnowledgeSearchService | None,
) -> ChatService | None:
    """Build the Phase 3 chat service iff both gates are wired.

    Requires:
    - ``knowledge_search is not None`` (Phase 3 vector retrieval is on,
      which transitively means ``KW_KNOWLEDGE_LAYER_ENABLED=true`` AND
      ``VOYAGE_API_KEY`` is set).
    - ``settings.anthropic_api_key`` is non-empty (otherwise we can't
      generate the answer).

    Returns ``None`` otherwise; the route layer maps ``None`` to a
    503 with ``KW_CHAT_DISABLED``.

    The chat LLM client is **separate** from the entity extractor's
    LLM client. Same Anthropic credentials, different per-call
    settings (chat uses larger ``max_tokens`` and no tool binding).
    """
    if knowledge_search is None:
        return None
    if not settings.anthropic_api_key.strip():
        return None
    api_key = settings.anthropic_api_key.strip()
    model = settings.anthropic_model.strip() or DEFAULT_ANTHROPIC_MODEL
    chat_llm: LLMClient = AnthropicLLMClient(
        api_key=api_key,
        model=model,
        # Larger budget than the extractor so multi-paragraph answers
        # don't get truncated. Still bounded so a runaway prompt
        # doesn't burn the whole token cap.
        max_tokens=4096,
    )
    return ChatService(
        search=knowledge_search,
        llm=chat_llm,
        llm_model=model,
    )


def _maybe_build_knowledge_layer(
    settings: Settings | None = None,
    *,
    embedding_client: EmbeddingClient | None = None,
) -> tuple[GraphStore, KnowledgeProjector | None]:
    """Build the knowledge graph store + projector based on settings.

    Reads ``KW_KNOWLEDGE_LAYER_ENABLED`` and the ``KW_NEO4J_*`` family
    via :class:`app.settings.Settings`. The defaults — knowledge layer
    disabled, in-memory store — preserve the existing pipeline's
    behaviour: no Neo4j needed to run the API or its tests.

    When ``embedding_client`` is provided, the projector wires it
    through so the embedding write path activates after each
    structural projection.

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
    return store, KnowledgeProjector(
        graph_store=store,
        embedding_client=embedding_client,
    )


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
    embedding_client = _maybe_build_embedding_client(settings)
    graph_store, knowledge_projector = _maybe_build_knowledge_layer(
        settings, embedding_client=embedding_client
    )
    knowledge_search = (
        KnowledgeSearchService(
            embedding_client=embedding_client,
            graph_store=graph_store,
        )
        if embedding_client is not None
        else None
    )
    chat = _maybe_build_chat_service(
        settings=settings,
        knowledge_search=knowledge_search,
    )
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
        embedding_client=embedding_client,
        knowledge_search=knowledge_search,
        chat=chat,
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
    embedding_client = _maybe_build_embedding_client(settings)
    graph_store, knowledge_projector = _maybe_build_knowledge_layer(
        settings, embedding_client=embedding_client
    )
    knowledge_search = (
        KnowledgeSearchService(
            embedding_client=embedding_client,
            graph_store=graph_store,
        )
        if embedding_client is not None
        else None
    )
    chat = _maybe_build_chat_service(
        settings=settings,
        knowledge_search=knowledge_search,
    )
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
        embedding_client=embedding_client,
        knowledge_search=knowledge_search,
        chat=chat,
        settings=settings,
    )
