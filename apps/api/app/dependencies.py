from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.schemas.taxonomy import Taxonomy
from app.services.audit_event_store import (
    AuditEventStore,
    InMemoryAuditEventStore,
    SQLiteAuditEventStore,
)
from app.services.auth import AuthService, DisabledAuthService, build_auth_service
from app.services.catalog_store import SQLiteCatalogStore
from app.services.claim_extractor import ClaimExtractor
from app.services.claim_store import (
    ClaimStore,
    InMemoryClaimStore,
    SQLiteClaimStore,
)
from app.services.confidence_scorer import ConfidenceScorer
from app.services.corpus_norms import (
    CorpusNormsProvider,
    InMemoryCorpusNormsStore,
    LazyCorpusNorms,
    SQLiteCorpusNormsStore,
)
from app.services.document_parser import ParserRegistry, PlainTextParser
from app.services.document_relations_cache import DocumentRelationsCache
from app.services.document_relations_store import (
    DocumentRelationsStore,
    InMemoryDocumentRelationsStore,
    SQLiteDocumentRelationsStore,
)
from app.services.document_service import DocumentService
from app.services.document_similarity_service import DocumentSimilarityService
from app.services.document_topic_store import (
    DocumentTopicStore,
    InMemoryDocumentTopicStore,
    SQLiteDocumentTopicStore,
)
from app.services.enrichers import RuleBasedEntityEnricher, SemanticEnricher
from app.services.enrichers.spacy_ner import SpacyNerEnricher
from app.services.extraction_job_service import ExtractionJobService
from app.services.hitl_auto_promoter import HITLAutoPromoter
from app.services.hitl_drift_detector import HITLDriftDetector
from app.services.hitl_router import HITLRouter
from app.services.idempotency_store import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
    SQLiteIdempotencyStore,
)
from app.services.knowledge import (
    AnthropicLLMClient,
    EmbeddingClient,
    EntityExtractor,
    GeminiLLMClient,
    GraphStore,
    InMemoryGraphStore,
    KnowledgeAtlasService,
    KnowledgeChatService,
    KnowledgeExploreSearchService,
    KnowledgeNeighborhoodService,
    KnowledgeProjector,
    KnowledgeRelationsService,
    KnowledgeSearchService,
    LLMClient,
    Neo4jGraphStore,
    VoyageEmbeddingClient,
)
from app.services.markdown_generator import MarkdownGenerator
from app.services.parsers import DocxParser, PdfParser, PptxParser
from app.services.process_store import (
    InMemoryProcessStore,
    ProcessStore,
    SQLiteProcessStore,
)
from app.services.projection_status_tracker import ProjectionStatusTracker
from app.services.review_service import ReviewService
from app.services.sampling_state_store import (
    InMemorySamplingStateStore,
    SamplingStateStore,
    SQLiteSamplingStateStore,
)
from app.services.semantic_extractor import SemanticExtractor
from app.services.semantic_output_service import SemanticOutputService
from app.services.storage_service import (
    FileSystemStorageService,
    InMemoryStorageService,
    StorageService,
)
from app.services.taxonomy_store import (
    InMemoryTaxonomyStore,
    SQLiteTaxonomyStore,
    TaxonomyStore,
    import_yaml_into_store,
)
from app.services.topic_extractor import TopicExtractor
from app.services.validation_metadata_store import (
    InMemoryValidationMetadataStore,
    SQLiteValidationMetadataStore,
    ValidationMetadataStore,
)
from app.settings import Settings


class _CatalogNormSampleProvider:
    """:class:`NormSampleProvider` adapter over the catalog.

    The corpus-norms lazy materialisation path needs raw samples per
    ``(content_type, topic_cluster)`` bucket. This adapter walks the
    catalog's existing semantic documents once per bucket request and
    returns the section-length / asset-count populations the
    :class:`LazyCorpusNorms` wrapper hashes into a
    :class:`CorpusNorm`.

    The walk is bounded by the catalog size (one row per
    ``DocumentVersion``); for the pilot this is small enough to scan
    on-demand. The persisted norms then short-circuit subsequent
    lookups so production traffic doesn't pay the walk cost again.

    Bucket-isolation contract: we filter on ``content_type`` directly;
    ``topic_cluster`` is filtered loosely (we accept every catalog
    document regardless of its cluster). The clustering pass is
    per-version, not catalog-wide, so persisting a cluster id with
    each catalog row is a future-slice change. Until then, the
    materialised norms span every cluster within a content type — a
    coarser but safe baseline that the section-length signal still
    benefits from.
    """

    def __init__(self, *, documents: DocumentService) -> None:
        self._documents = documents

    def section_length_samples(
        self,
        *,
        content_type: str,
        topic_cluster: str,
    ) -> list[int]:
        del topic_cluster  # see class docstring — coarse bucket
        samples: list[int] = []
        for doc in self._documents.list_documents():
            for version in doc.versions:
                if version.content_type != content_type:
                    continue
                try:
                    semantic = self._documents.catalog.get_semantic_document(version.id)
                except KeyError:
                    continue
                samples.extend(len(s.text or "") for s in semantic.sections)
        return samples

    def asset_count_samples(
        self,
        *,
        content_type: str,
        topic_cluster: str,
    ) -> list[int]:
        del topic_cluster
        samples: list[int] = []
        for doc in self._documents.list_documents():
            for version in doc.versions:
                if version.content_type != content_type:
                    continue
                try:
                    semantic = self._documents.catalog.get_semantic_document(version.id)
                except KeyError:
                    continue
                samples.append(len(semantic.assets))
        return samples


class _GraphStoreTopicProvider:
    """:class:`DocumentTopicProvider` adapter over the catalog + graph store.

    The :class:`DocumentSimilarityService` only needs two reads:

    1. The list of document ids the catalog knows about (so it can
       enumerate similarity candidates).
    2. The set of topic ids each document touches.

    There is no persisted "topics for document X" table today — topics
    are emitted by ``TopicClusteringService`` per validated version
    and projected onto the knowledge graph as
    ``ChunkNodeProperties.topic_id`` on each chunk. This adapter walks
    :meth:`GraphStore.find_subgraph_for_document` once per query and
    folds the chunk-level ``topic_id`` values into a set, which is the
    contract the similarity service consumes (ADR-025 §3).

    Cold-start (knowledge layer disabled, no projected chunks, or
    chunks with ``topic_id == None``) collapses to an empty set per the
    Protocol contract — the similar-documents route then returns an
    empty ``results`` list with HTTP 200 instead of a 5xx.
    """

    def __init__(
        self,
        *,
        documents: DocumentService,
        graph_store: GraphStore,
    ) -> None:
        self._documents = documents
        self._graph_store = graph_store

    def topic_ids_for_document(self, document_id: str) -> set[str]:
        projection = self._graph_store.find_subgraph_for_document(document_id)
        topic_ids: set[str] = set()
        for node in projection.nodes:
            if node.kind != "chunk":
                continue
            topic_id = node.properties.get("topic_id")
            if isinstance(topic_id, str) and topic_id:
                topic_ids.add(topic_id)
        return topic_ids

    def known_document_ids(self) -> list[str]:
        # Source of truth for "what documents exist" is the catalog —
        # the graph store may not have projected every document yet
        # (knowledge layer disabled, or pre-validation), and feeding
        # only graph-known ids here would silently truncate the
        # candidate set. The similarity service tolerates documents
        # with empty topic sets gracefully (returns 0.0, dropped from
        # ``top_k``), so passing every catalog id is the safe default.
        return [document.id for document in self._documents.list_documents()]


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
    # ``KW_KNOWLEDGE_LAYER_ENABLED=true`` AND an LLM key resolves
    # (``GEMINI_API_KEY`` or ``ANTHROPIC_API_KEY``, per ADR-013 §6);
    # otherwise None and the route layer treats entity extraction as
    # disabled — Phase 1a behaviour is preserved.
    entity_extractor: EntityExtractor | None = None
    # Phase 3 (ADR-015, #186): vector RAG. ``embedding_client`` is
    # constructed iff ``KW_KNOWLEDGE_LAYER_ENABLED=true`` AND
    # ``VOYAGE_API_KEY`` is set; ``knowledge_search`` requires the
    # client. Both ``None`` keeps Phase 1 + Phase 2 behaviour exactly:
    # the projector skips the embedding write, the search route
    # returns 503.
    embedding_client: EmbeddingClient | None = None
    knowledge_search: KnowledgeSearchService | None = None
    # Phase 3 chat surface. Constructed iff
    # ``KW_KNOWLEDGE_LAYER_ENABLED=true`` AND an LLM key resolves
    # (``GEMINI_API_KEY`` or ``ANTHROPIC_API_KEY``) AND a
    # ``knowledge_search`` is wired (i.e. ``VOYAGE_API_KEY`` is also
    # set). Otherwise ``None`` and the route returns 503 with
    # ``KW_CHAT_DISABLED``.
    knowledge_chat: KnowledgeChatService | None = None
    # Relation evidence service (#311, ADR-028). Always wired — it's a
    # thin read-only adapter over ``graph_store.find_edge_by_id`` so it
    # has no external dependency to gate on. The ``GET /knowledge/relations/...``
    # routes return empty / 404 when the graph store has no matching
    # edges, mirroring the rest of the knowledge-layer read surface.
    knowledge_relations: KnowledgeRelationsService | None = None
    # Cached aggregate-relation read (#380, ADR-031). Backs
    # ``GET /knowledge/relations/aggregate``: cache hit serves
    # SQLite, cache miss falls through to ``knowledge_relations``
    # and writes back. Always wired alongside ``knowledge_relations``.
    document_relations_cache: DocumentRelationsCache | None = None
    # Focused neighborhood read service (#310, ADR-028). Always wired
    # in ``build_services``; depends only on ``graph_store`` so the
    # field is Optional only for partial-construction back-compat.
    knowledge_neighborhood: KnowledgeNeighborhoodService | None = None
    # Multi-kind Explorer search (#313, ADR-028). Wired only when the
    # underlying ``knowledge_search`` is wired (Phase 3 + Voyage); the
    # route returns 503 with ``KW_VECTOR_SEARCH_DISABLED`` otherwise.
    knowledge_explore_search: KnowledgeExploreSearchService | None = None
    # Corpus atlas summary (#312, ADR-028). Always wired — the service
    # walks the catalog + graph store and works on an empty corpus
    # (returns empty blocks). The Explorer's default home (#316) hits
    # this on every load.
    knowledge_atlas: KnowledgeAtlasService | None = None
    # Audit event store (#26 residual). Always present so the
    # logging-handler wiring is unconditional; the in-memory fake is
    # the test-suite default and the SQLite store lights up only when
    # ``KW_AUDIT_ENABLED=true`` plus a persistent wiring.
    audit_events: AuditEventStore = field(default_factory=InMemoryAuditEventStore)
    # Authentication service (ADR-019). Always present; the default is
    # :class:`DisabledAuthService` so every existing test and demo
    # keeps working without setting ``KW_AUTH_MODE``. ``build_services``
    # / ``build_persistent_services`` swap in the configured impl when
    # the env var is set.
    auth: AuthService = field(default_factory=DisabledAuthService)
    # Operator-imposed taxonomy (ADR-017 + ADR-031, #379). Read from
    # the SQLite-backed :class:`TaxonomyStore` at boot. Stays cached
    # in this dataclass for hot-path reads; the ``POST
    # /admin/taxonomy/import_yaml`` route invalidates the cache by
    # rebuilding the services container (operator workflow at MVP
    # cadence). ``None`` means "no taxonomy ever published" → fall
    # back to auto-deduced clustering.
    taxonomy: Taxonomy | None = None
    # Backing store for the imposed taxonomy. Always present (the
    # in-memory default is the test/demo affordance; persistent
    # builds wire the SQLite-backed implementation). The
    # ``POST /admin/taxonomy/import_yaml`` route writes through
    # this store.
    taxonomy_store: TaxonomyStore = field(default_factory=InMemoryTaxonomyStore)
    # Atomic Claim/Fact store (#368, ADR-031). Always present — the
    # in-memory default is the test/demo affordance; persistent
    # builds wire :class:`SQLiteClaimStore`. Read by
    # ``GET /knowledge/claims``; future LLM extractor wiring writes
    # through this store via ``save_claims``.
    claim_store: ClaimStore = field(default_factory=InMemoryClaimStore)
    # First-class Playbook/Process data model (#369, ADR-031). Always
    # present (in-memory default for tests / the in-process demo;
    # persistent builds wire the SQLite-backed implementation).
    # The ``GET /knowledge/processes`` and
    # ``GET /knowledge/processes/{id}`` routes read through this
    # store; the future SOP-aware parser writes to it.
    process_store: ProcessStore = field(default_factory=InMemoryProcessStore)
    # LLM-extracted document-level topic store (#411, ADR-031).
    # Always present (in-memory default for tests / the in-process
    # demo; persistent builds wire the SQLite-backed implementation).
    # Read by ``GET /knowledge/topics``; future LLM TopicExtractor
    # wiring writes through this store via ``save_topics``. Distinct
    # from the deterministic chunk-cluster ``Topic`` that lives in
    # the knowledge graph — this store holds document-level themes
    # for the operator-facing topic UX (Explorer, Atlas, Orbital).
    document_topic_store: DocumentTopicStore = field(
        default_factory=InMemoryDocumentTopicStore,
    )
    # Resolved absolute path the taxonomy was read from, surfaced in
    # the route response so operators can verify which file the API
    # is reading. ``None`` when no taxonomy is configured.
    taxonomy_source_path: str | None = None
    # Snapshot of the typed settings used to construct this container
    # (issue #43). Routes read settings *fresh per request* via
    # ``Settings()`` so per-test ``monkeypatch.setenv`` is observable;
    # this field exists so deployment-time configuration (e.g. a
    # programmatically-constructed Settings) can be threaded through
    # ``build_services(settings=...)``.
    settings: Settings = field(default_factory=Settings)
    # Review-decision orchestrator (audit #223). Constructed lazily in
    # ``__post_init__`` from the other fields when callers don't pass
    # one explicitly — keeps every existing test that builds a
    # ``PipelineServices`` directly working without a review= kwarg
    # while ``build_services`` / ``build_persistent_services`` pass
    # the canonical instance.
    review: ReviewService = field(init=False)
    # HITL confidence scorer + sidecar metadata store (ADR-023, EPIC-A
    # slice 1, #215). The scorer is a fire-and-log side-effect of the
    # NEEDS_REVIEW transition; the metadata store persists every
    # scoring pass for the next-slice ``hitl_router.py`` to consume.
    # Both fields are ``None`` when ``KW_HITL_DISABLE_SCORER`` is
    # truthy, in which case the transition keeps working without the
    # scoring side-effect (demo-safety escape hatch per ADR-023 §5).
    confidence_scorer: ConfidenceScorer | None = None
    # HITL router (slice 2, ADR-023 §6, #215). ``None`` when the
    # scorer is disabled — the router has nothing to read in that
    # case, so the wiring keeps both fields tied. The router writes
    # ``ValidationMetadata.routing_decision`` and emits the
    # ``routing.decided`` audit event; the auto-promotion FSM
    # transition is the next slice.
    hitl_router: HITLRouter | None = None
    # HITL auto-promotion worker (slice 3, ADR-023 §6, #215). ``None``
    # when the router is None — same kill-switch tied to
    # ``KW_HITL_DISABLE_SCORER`` since the worker has no rows to act
    # on without the router writing them in the first place. The
    # worker is invoked synchronously from
    # ``POST /admin/hitl/run_auto_promote_pass``; a real scheduler
    # (cron / asyncio) is deferred until the drift-detector slice.
    hitl_auto_promoter: HITLAutoPromoter | None = field(init=False, default=None)
    sampling_state: SamplingStateStore = field(default_factory=InMemorySamplingStateStore)
    validation_metadata: ValidationMetadataStore = field(
        default_factory=InMemoryValidationMetadataStore
    )
    corpus_norms: CorpusNormsProvider = field(default_factory=InMemoryCorpusNormsStore)
    # Topic-Jaccard document similarity (ADR-025 §3, EPIC-C C.2/C.3).
    # The provider is a thin adapter over the catalog + graph store so
    # the surface stays decoupled from any specific clustering wiring;
    # see :class:`_GraphStoreTopicProvider`. The service itself is
    # stateless — building it eagerly here keeps the route layer's
    # ``Depends(...)`` injection trivial.
    document_similarity: DocumentSimilarityService = field(init=False)
    # Process-local tracker for knowledge-projection side-effect status.
    # The catalog stays the source of truth for the FSM; this tracker
    # tells reviewer UIs whether the *graph* is populated yet so they
    # can render a "Projecting…" indicator. See
    # :mod:`app.services.projection_status_tracker` for the full
    # rationale.
    projection_status: ProjectionStatusTracker = field(default_factory=ProjectionStatusTracker)

    def __post_init__(self) -> None:
        # Frozen dataclass — bypass the immutability guard for the
        # post-init fields. Every other field is set by the caller
        # (or has a default factory) so the only allowed mutations
        # are these two.
        object.__setattr__(
            self,
            "review",
            ReviewService(
                documents=self.documents,
                semantic_outputs=self.semantic_outputs,
                knowledge_projector=self.knowledge_projector,
                entity_extractor=self.entity_extractor,
                # EPIC-A A.3 part 2 drift signal: handle_rejection
                # bumps ``samples_human_after_auto`` when the rejected
                # version was originally routed to ``auto``.
                validation_metadata=self.validation_metadata,
                sampling_state=self.sampling_state,
                # Tracker so the side-effects helper records start /
                # complete / fail transitions reviewer UIs can poll.
                projection_status=self.projection_status,
            ),
        )
        object.__setattr__(
            self,
            "document_similarity",
            DocumentSimilarityService(
                topics=_GraphStoreTopicProvider(
                    documents=self.documents,
                    graph_store=self.graph_store,
                ),
            ),
        )
        # HITL auto-promotion worker (slice 3, #215). Same kill switch
        # as the router: when ``hitl_router`` is None the worker has
        # no rows to act on. Built here (rather than in
        # ``build_services``) so it can reuse the ``self.review``
        # instance the post-init just created.
        if self.hitl_router is not None:
            object.__setattr__(
                self,
                "hitl_auto_promoter",
                HITLAutoPromoter(
                    validation_metadata=self.validation_metadata,
                    review_service=self.review,
                    sampling_state=self.sampling_state,
                    catalog=self.documents.catalog,
                ),
            )


def _build_enrichers(settings: Settings) -> list[SemanticEnricher]:
    """Assemble the semantic-enricher chain for this wiring.

    Always includes the deterministic :class:`RuleBasedEntityEnricher`
    (date / monetary / requirement). When ``KW_NER_ENABLED=true`` and
    the optional ``ner`` extra is installed, also includes the
    :class:`SpacyNerEnricher` for person / organization assets (#190).
    A misconfigured NER opt-in (flag on but spaCy or the model is
    missing) raises :class:`RuntimeError` at construction time so the
    operator sees the failure at startup rather than silently shipping
    no NER assets.
    """
    chain: list[SemanticEnricher] = [RuleBasedEntityEnricher()]
    if settings.ner_enabled:
        chain.append(SpacyNerEnricher(model=settings.ner_spacy_model.strip() or "en_core_web_sm"))
    return chain


def _build_audit_store(
    settings: Settings,
    *,
    default_dir: Path | None = None,
) -> AuditEventStore:
    """Pick the audit-event store for this wiring (#26 residual).

    Returns :class:`SQLiteAuditEventStore` when ``KW_AUDIT_ENABLED`` is
    truthy and the path can be resolved (explicit ``KW_AUDIT_DB_PATH``
    or, for persistent services, ``<data_dir>/audit.sqlite3``). Falls
    back to :class:`InMemoryAuditEventStore` otherwise — that's the
    in-memory test default and the "audit explicitly disabled in
    persistent" deployment shape.
    """
    if not settings.audit_enabled:
        return InMemoryAuditEventStore()
    explicit = settings.audit_db_path.strip()
    if explicit:
        return SQLiteAuditEventStore(Path(explicit))
    if default_dir is not None:
        return SQLiteAuditEventStore(default_dir / "audit.sqlite3")
    # Truthy flag + no path + no default dir (i.e. ``build_services``
    # called from the in-memory factory). Fall back to in-memory so the
    # configured event vocabulary still flows but isn't persisted; the
    # operator's likely intent was to enable persistent audit, which
    # requires the persistent factory.
    return InMemoryAuditEventStore()


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


def _resolve_llm_provider(
    settings: Settings,
) -> Literal["gemini", "anthropic"] | None:
    """Pick the active LLM provider per ADR-013 §6.

    Resolution rules:

    - ``llm_provider="auto"`` (default): Gemini wins when
      ``GEMINI_API_KEY`` is set, otherwise Anthropic if its key is set,
      otherwise ``None`` (no provider).
    - ``llm_provider="gemini"`` / ``"anthropic"``: pin the choice;
      returns ``None`` when the pinned provider's key is missing so the
      Phase 2 / Phase 3 features stay disabled rather than silently
      falling back to the other provider (operators pinning a provider
      typically want to know when their config is wrong).

    Returns the chosen provider name, or ``None`` when no LLM is
    configured / the knowledge layer is disabled.
    """
    if not settings.knowledge_layer_enabled:
        return None
    has_gemini = bool(settings.gemini_api_key.strip())
    has_anthropic = bool(settings.anthropic_api_key.strip())
    pinned = settings.llm_provider
    if pinned == "gemini":
        return "gemini" if has_gemini else None
    if pinned == "anthropic":
        return "anthropic" if has_anthropic else None
    # ``auto``: Gemini primary, Anthropic fallback.
    if has_gemini:
        return "gemini"
    if has_anthropic:
        return "anthropic"
    return None


def _maybe_build_llm(
    settings: Settings | None = None,
) -> tuple[LLMClient, str] | None:
    """Build the active LLM client + return its model id, if enabled.

    Returns ``None`` unless **both** the knowledge-layer kill switch is
    truthy **and** a provider's API key is configured for the selected
    provider (see :func:`_resolve_llm_provider`). Callers (entity
    extractor, chat service) reuse the same client so the prompt cache
    and retry budgets are amortised across phases.
    """
    settings = settings or Settings()
    provider = _resolve_llm_provider(settings)
    if provider is None:
        return None

    from app.services.knowledge.llm_client import (  # noqa: PLC0415
        DEFAULT_ANTHROPIC_MODEL,
        DEFAULT_GEMINI_MODEL,
    )

    if provider == "gemini":
        api_key = settings.gemini_api_key.strip()
        model = settings.gemini_model.strip() or None
        gemini_timeout = settings.gemini_timeout_seconds
        gemini_concurrency = settings.gemini_max_concurrent
        llm: LLMClient = (
            GeminiLLMClient(
                api_key=api_key,
                model=model,
                timeout_seconds=gemini_timeout,
                max_concurrent=gemini_concurrency,
            )
            if model
            else GeminiLLMClient(
                api_key=api_key,
                timeout_seconds=gemini_timeout,
                max_concurrent=gemini_concurrency,
            )
        )
        return llm, (model or DEFAULT_GEMINI_MODEL)

    # provider == "anthropic"
    api_key = settings.anthropic_api_key.strip()
    model = settings.anthropic_model.strip() or None
    anthropic_timeout = settings.anthropic_timeout_seconds
    anthropic_concurrency = settings.anthropic_max_concurrent
    llm = (
        AnthropicLLMClient(
            api_key=api_key,
            model=model,
            timeout_seconds=anthropic_timeout,
            max_concurrent=anthropic_concurrency,
        )
        if model
        else AnthropicLLMClient(
            api_key=api_key,
            timeout_seconds=anthropic_timeout,
            max_concurrent=anthropic_concurrency,
        )
    )
    return llm, (model or DEFAULT_ANTHROPIC_MODEL)


# Backwards-compat alias. Older call sites and tests reach for the
# previous name; the implementation now resolves whichever provider
# the active settings select.
_maybe_build_anthropic_llm = _maybe_build_llm


def _maybe_build_entity_extractor(
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
) -> EntityExtractor | None:
    """Build the LLM-driven entity extractor if enabled (ADR-013).

    Returns ``None`` unless **both** the knowledge-layer kill switch is
    truthy **and** an LLM provider is configured (Gemini primary,
    Anthropic fallback per ADR-013 §6). The Phase 1a-only path (graph
    projection without entities) is preserved when no provider is
    configured so contributors who don't have either API key can still
    run the knowledge layer end-to-end against the in-memory graph
    store.

    Callers may pass a pre-built ``llm`` to share one client across
    Phase 2 (entity extractor) and Phase 3 (chat service); when omitted
    a fresh one is constructed from settings.
    """
    settings = settings or Settings()
    if llm is None:
        built = _maybe_build_llm(settings)
        if built is None:
            return None
        llm = built[0]
    # ADR-014 §3 circuit breaker. ``0`` means disabled; any positive
    # value caps cumulative input_tokens per document.
    cap = settings.entity_extractor_max_input_tokens_per_document
    return EntityExtractor(
        llm=llm,
        max_input_tokens_per_document=cap if cap > 0 else None,
    )


def _maybe_build_claim_extractor(
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
    llm_model: str | None = None,
) -> ClaimExtractor | None:
    """Build the LLM-driven Claim extractor if enabled (#392, ADR-031).

    Returns ``None`` unless an LLM provider is configured (Gemini
    primary, Anthropic fallback per ADR-013 §6) AND the knowledge
    layer is on. The pre-#392 path (no Claim extraction wired) is
    preserved when no provider is configured so contributors who
    don't have either API key can still run the knowledge layer
    end-to-end against the in-memory graph store.

    Callers may pass a pre-built ``llm`` + ``llm_model`` so the
    Claim extractor shares one client with the entity extractor and
    chat service — same retry budget, same prompt cache, single
    provider config to track.
    """
    settings = settings or Settings()
    if llm is None or llm_model is None:
        built = _maybe_build_llm(settings)
        if built is None:
            return None
        llm, llm_model = built
    cap = settings.claim_extractor_max_input_tokens_per_document
    return ClaimExtractor(
        llm=llm,
        model=llm_model,
        max_input_tokens=cap,
    )


def _maybe_build_topic_extractor(
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
    llm_model: str | None = None,
) -> TopicExtractor | None:
    """Build the LLM-driven document Topic extractor if enabled
    (#411, ADR-031).

    Returns ``None`` unless an LLM provider is configured (Gemini
    primary, Anthropic fallback per ADR-013 §6) AND the knowledge
    layer is on. The pre-#411 path (no Topic extraction wired) is
    preserved when no provider is configured so contributors who
    don't have either API key can still run the knowledge layer
    end-to-end against the in-memory graph store.

    Callers may pass a pre-built ``llm`` + ``llm_model`` so the
    Topic extractor shares one client with the entity / claim
    extractors and the chat service — same retry budget, same prompt
    cache, single provider config to track.
    """
    settings = settings or Settings()
    if llm is None or llm_model is None:
        built = _maybe_build_llm(settings)
        if built is None:
            return None
        llm, llm_model = built
    cap = settings.topic_extractor_max_input_tokens_per_document
    return TopicExtractor(
        llm=llm,
        model=llm_model,
        max_input_tokens=cap,
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
    voyage_timeout = settings.voyage_timeout_seconds
    voyage_concurrency = settings.voyage_max_concurrent
    return (
        VoyageEmbeddingClient(
            api_key=api_key,
            model=model,
            timeout_seconds=voyage_timeout,
            max_concurrent=voyage_concurrency,
        )
        if model
        else VoyageEmbeddingClient(
            api_key=api_key,
            timeout_seconds=voyage_timeout,
            max_concurrent=voyage_concurrency,
        )
    )


def _bootstrap_taxonomy(
    settings: Settings,
    store: TaxonomyStore,
) -> tuple[Taxonomy | None, str | None]:
    """Resolve the active taxonomy at boot, with optional one-time YAML import.

    Read order:

    1. Prefer the SQLite-backed store. If a taxonomy has been
       published, return it.
    2. If the store is empty AND ``KW_TAXONOMY_PATH`` resolves to a
       readable YAML file, run a one-time bootstrap import so the
       imposed taxonomy survives the migration to SQLite without an
       admin step. Future redeploys hit branch 1 and skip the import.
    3. If the store is empty and no YAML is configured, return
       ``(None, None)`` — same shape the YAML-only path returned
       when ``KW_TAXONOMY_PATH`` was unset.

    The ``source_path`` second-tuple is preserved for backwards
    compat with the route's ``TaxonomyResponse.source_path`` field;
    populated only when the bootstrap import actually fired.
    """
    active = store.get_active()
    if active is not None:
        return active, None
    yaml_path = settings.taxonomy_path or None
    if yaml_path is None:
        return None, None
    # Bootstrap import — fires once when the operator first redeploys
    # against a fresh SQLite store but already has their YAML in
    # place. ``import_yaml_into_store`` returns ``None`` if the YAML
    # file is missing or empty; that's a valid "not configured yet"
    # state, not an error.
    try:
        new_id = import_yaml_into_store(
            store,
            yaml_path=yaml_path,
            actor="boot.bootstrap_yaml_import",
        )
    except Exception:  # noqa: BLE001 - import errors should not stop boot
        # The YAML loader raises ``TaxonomyLoadError`` for malformed
        # files. Surface that loudly via logging but don't crash the
        # process — the operator can fix the YAML and redeploy.
        import logging

        logging.getLogger(__name__).exception(
            "knowledge.taxonomy.bootstrap_import_failed",
            extra={"taxonomy_path": yaml_path},
        )
        return None, None
    if new_id is None:
        # YAML resolved but file was empty / missing.
        return None, str(yaml_path)
    return store.get_active(), str(yaml_path)


class ProductionMisconfiguration(RuntimeError):
    """Raised on boot when a production deployment is missing required wiring.

    The single trigger today is ADR-031: ``KW_PERSISTENT=true`` AND
    ``KW_KNOWLEDGE_LAYER_ENABLED=true`` AND no Neo4j configured.
    Future production-only invariants raise the same exception so the
    operator-facing message is consistent.
    """


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

    Production guard (ADR-031): when both ``KW_PERSISTENT=true`` and
    ``KW_KNOWLEDGE_LAYER_ENABLED=true`` are set but no Neo4j URI /
    user is configured, boot fails with
    :class:`ProductionMisconfiguration`. Silently falling back to
    ``InMemoryGraphStore`` in production was never the intent — at
    the target scale (100k+ chunks) the in-memory store loses every
    edge / topic / embedding on restart, and operators need the
    failure to be loud, not silent.

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
        # Production deployments must wire Neo4j. Dev / test (KW_PERSISTENT=
        # false) keeps the in-memory affordance. See ADR-031 §"Production
        # posture" / §"Dev / test posture".
        if settings.persistent:
            raise ProductionMisconfiguration(
                "KW_KNOWLEDGE_LAYER_ENABLED=true with KW_PERSISTENT=true "
                "requires KW_NEO4J_URI and KW_NEO4J_USER to be set. "
                "Production deployments cannot fall back to the "
                "in-memory graph store — chunks, edges, and embeddings "
                "would not survive restart at the target catalog scale "
                "(see docs/adr/ADR-031-storage-boundary-sqlite-vs-neo4j.md). "
                "Either configure Neo4j, set KW_KNOWLEDGE_LAYER_ENABLED=false "
                "to ship without the knowledge layer, or unset "
                "KW_PERSISTENT to run the in-process demo."
            )
        store = InMemoryGraphStore()
    return store, KnowledgeProjector(
        graph_store=store,
        embedding_client=embedding_client,
    )


def _maybe_build_chat_service(
    *,
    llm: LLMClient | None,
    llm_model: str | None,
    knowledge_search: KnowledgeSearchService | None,
    graph_store: GraphStore,
) -> KnowledgeChatService | None:
    """Wire the Phase 3 chat service when every dependency is available.

    The chat surface needs all three of: an LLM client (Gemini or
    Anthropic key, per ADR-013 §6), a vector retrieval service (Voyage
    key + embedding write path), and a graph store (always present,
    but only meaningful once the knowledge layer is enabled). Any
    missing dependency yields ``None`` and the route returns 503 with
    ``KW_CHAT_DISABLED``.
    """
    if llm is None or llm_model is None or knowledge_search is None:
        return None
    return KnowledgeChatService(
        search=knowledge_search,
        graph_store=graph_store,
        llm=llm,
        llm_model=llm_model,
    )


def _maybe_build_confidence_scorer(
    settings: Settings,
    *,
    documents: DocumentService,
    corpus_norms: CorpusNormsProvider,
) -> ConfidenceScorer | None:
    """Construct the HITL confidence scorer iff the kill switch is off.

    Returns ``None`` when ``KW_HITL_DISABLE_SCORER`` is truthy — that
    is the demo-safety escape hatch per ADR-023 §5. The
    ``SemanticOutputService`` checks the field is non-``None`` before
    invoking the scorer, so a ``None`` here cleanly disables the
    fire-and-log side-effect at the NEEDS_REVIEW transition.
    """
    del documents  # held for forward-compat (e.g. when the OCR flag fn
    # needs catalog access). The current default OCR flag is constant
    # ``False``, so the scorer doesn't need ``documents`` yet.
    if settings.hitl_scorer_disabled:
        return None
    return ConfidenceScorer(
        weights=settings.hitl_weights,
        corpus_norms=corpus_norms,
    )


def _maybe_build_hitl_router(
    settings: Settings,
    *,
    confidence_scorer: ConfidenceScorer | None,
    sampling_state: SamplingStateStore,
) -> HITLRouter | None:
    """Construct the HITL router iff the scorer is also wired.

    The router has nothing to read when the scorer is disabled, so we
    tie the two together: a single ``KW_HITL_DISABLE_SCORER`` flips
    both off. EPIC-B is currently dead — ``external_workflow_enabled``
    is hard-wired to ``False`` here. Once EPIC-B lands, this flips to
    ``settings.iterop_enabled and bool(settings.iterop_base_url)`` and
    the router's ``external`` branch lights up without further code
    changes in the hook.

    The drift detector (EPIC-A A.3 part 2) is wired alongside the
    router so the SPC sampling rate ramps per-bucket when the
    ``samples_human_after_auto / samples_auto`` ratio crosses
    :attr:`Settings.hitl_drift_threshold`. Backward-compat: the
    router's ``sampling_rate`` constant is still threaded through so
    a future ``drift_detector=None`` posture (or a misconfiguration)
    falls back to the constant rate.
    """
    if confidence_scorer is None:
        return None
    drift_detector = HITLDriftDetector(
        sampling_state=sampling_state,
        baseline_rate=settings.hitl_spc_sample_rate,
        drift_threshold=settings.hitl_drift_threshold,
        ramp_factor=settings.hitl_drift_ramp_factor,
    )
    return HITLRouter(
        sampling_state=sampling_state,
        threshold=settings.hitl_auto_validate_threshold,
        force_auto_corpus=settings.hitl_force_auto_corpus,
        # EPIC-B placeholder — see module docstring on
        # ``hitl_router.HITLRouter`` for the wire-up plan.
        external_workflow_enabled=False,
        sampling_rate=settings.hitl_spc_sample_rate,
        drift_detector=drift_detector.sampling_rate,
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
    semantic_extractor = SemanticExtractor(enrichers=_build_enrichers(settings))
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
    # Build the LLM once and share it between the entity extractor and
    # the chat service so they amortise the same prompt cache and
    # respect the same retry budget.
    llm_pair = _maybe_build_llm(settings)
    llm_client = llm_pair[0] if llm_pair else None
    llm_model = llm_pair[1] if llm_pair else None
    taxonomy_store: TaxonomyStore = InMemoryTaxonomyStore()
    taxonomy, taxonomy_source_path = _bootstrap_taxonomy(settings, taxonomy_store)
    claim_store: ClaimStore = InMemoryClaimStore()
    process_store: ProcessStore = InMemoryProcessStore()
    document_topic_store: DocumentTopicStore = InMemoryDocumentTopicStore()
    # HITL slice 1 wiring: in-memory corpus norms + sidecar store; the
    # scorer is constructed unless the kill switch is on. The lazy
    # provider sources samples from the catalog so unknown buckets
    # warm up on first use.
    corpus_norms_store: CorpusNormsProvider = LazyCorpusNorms(
        store=InMemoryCorpusNormsStore(),
        samples=_CatalogNormSampleProvider(documents=documents),
    )
    validation_metadata_store: ValidationMetadataStore = InMemoryValidationMetadataStore()
    confidence_scorer = _maybe_build_confidence_scorer(
        settings,
        documents=documents,
        corpus_norms=corpus_norms_store,
    )
    sampling_state_store: SamplingStateStore = InMemorySamplingStateStore()
    hitl_router = _maybe_build_hitl_router(
        settings,
        confidence_scorer=confidence_scorer,
        sampling_state=sampling_state_store,
    )
    semantic_outputs = SemanticOutputService(
        documents=documents,
        extraction_jobs=extraction_jobs,
        semantic_extractor=semantic_extractor,
        markdown_generator=markdown_generator,
        confidence_scorer=confidence_scorer,
        validation_metadata_store=validation_metadata_store,
        hitl_router=hitl_router,
    )
    entity_extractor = _maybe_build_entity_extractor(settings, llm=llm_client)
    knowledge_relations = KnowledgeRelationsService(graph_store=graph_store)
    document_relations_store: DocumentRelationsStore = InMemoryDocumentRelationsStore()
    document_relations_cache = DocumentRelationsCache(
        store=document_relations_store,
        relations=knowledge_relations,
    )
    # #392: build the Claim extractor before the projector wiring so
    # we can hand it in alongside the other side-effect deps. When no
    # LLM is configured ``claim_extractor`` is None and the
    # post-projection hook stays a no-op (pre-#392 behaviour preserved).
    claim_extractor = _maybe_build_claim_extractor(
        settings,
        llm=llm_client,
        llm_model=llm_model,
    )
    # #411: same gating as the Claim extractor — only present when
    # the LLM provider is configured. ``None`` keeps the projector
    # hook a no-op (pre-#411 behaviour preserved).
    topic_extractor = _maybe_build_topic_extractor(
        settings,
        llm=llm_client,
        llm_model=llm_model,
    )
    # #385: wire the cache onto the projector so post-projection
    # warm fires for every validate. ``None``-safe: when the
    # knowledge layer is disabled, ``knowledge_projector`` is None
    # and the cache simply never gets a warm trigger (the route's
    # cache-miss path still warms on demand).
    if knowledge_projector is not None:
        knowledge_projector.set_document_relations_cache(document_relations_cache)
        # #390: wire the process_store onto the projector so the
        # deterministic SOP parser fires after each successful
        # projection. Same ``None``-safe posture as the cache wiring;
        # non-procedural docs short-circuit inside the parser and
        # leave the catalog row state unchanged.
        knowledge_projector.set_process_store(process_store)
        # #392: wire the Claim extractor + store. Both must be set
        # for the post-projection LLM pass to fire — see
        # ``KnowledgeProjector``.
        knowledge_projector.set_claim_extractor(claim_extractor)
        knowledge_projector.set_claim_store(claim_store)
        # #411: wire the Topic extractor + DocumentTopic store.
        # Same both-or-nothing posture as the Claim pair.
        knowledge_projector.set_topic_extractor(topic_extractor)
        knowledge_projector.set_document_topic_store(document_topic_store)
    return PipelineServices(
        storage=storage,
        documents=documents,
        parsers=parsers,
        extraction_jobs=extraction_jobs,
        semantic_extractor=semantic_extractor,
        markdown_generator=markdown_generator,
        semantic_outputs=semantic_outputs,
        idempotency=InMemoryIdempotencyStore(),
        graph_store=graph_store,
        knowledge_projector=knowledge_projector,
        entity_extractor=entity_extractor,
        embedding_client=embedding_client,
        knowledge_search=knowledge_search,
        knowledge_chat=_maybe_build_chat_service(
            llm=llm_client,
            llm_model=llm_model,
            knowledge_search=knowledge_search,
            graph_store=graph_store,
        ),
        knowledge_relations=knowledge_relations,
        document_relations_cache=document_relations_cache,
        knowledge_neighborhood=KnowledgeNeighborhoodService(graph_store=graph_store),
        knowledge_explore_search=(
            KnowledgeExploreSearchService(
                search=knowledge_search,
                graph_store=graph_store,
                documents=documents,
            )
            if knowledge_search is not None
            else None
        ),
        knowledge_atlas=(
            KnowledgeAtlasService(graph_store=graph_store, documents=documents)
            if graph_store is not None
            else None
        ),
        audit_events=_build_audit_store(settings),
        auth=build_auth_service(settings),
        taxonomy=taxonomy,
        taxonomy_source_path=str(taxonomy_source_path) if taxonomy_source_path else None,
        taxonomy_store=taxonomy_store,
        claim_store=claim_store,
        process_store=process_store,
        document_topic_store=document_topic_store,
        settings=settings,
        confidence_scorer=confidence_scorer,
        hitl_router=hitl_router,
        sampling_state=sampling_state_store,
        validation_metadata=validation_metadata_store,
        corpus_norms=corpus_norms_store,
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
    semantic_extractor = SemanticExtractor(enrichers=_build_enrichers(settings))
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
    llm_pair = _maybe_build_llm(settings)
    llm_client = llm_pair[0] if llm_pair else None
    llm_model = llm_pair[1] if llm_pair else None
    taxonomy_store: TaxonomyStore = SQLiteTaxonomyStore(root / "catalog.sqlite3")
    taxonomy, taxonomy_source_path = _bootstrap_taxonomy(settings, taxonomy_store)
    claim_store: ClaimStore = SQLiteClaimStore(root / "catalog.sqlite3")
    # HITL slice 1 wiring (persistent path): SQLite-backed corpus
    # norms + sidecar store. Both reuse the catalog database file so
    # the schema migrations land beside ``document_versions`` and a
    # backup of ``catalog.sqlite3`` carries the metadata along.
    catalog_db_path = root / "catalog.sqlite3"
    process_store: ProcessStore = SQLiteProcessStore(catalog_db_path)
    document_topic_store: DocumentTopicStore = SQLiteDocumentTopicStore(catalog_db_path)
    persisted_norms_store = SQLiteCorpusNormsStore(catalog_db_path)
    corpus_norms_store: CorpusNormsProvider = LazyCorpusNorms(
        store=persisted_norms_store,
        samples=_CatalogNormSampleProvider(documents=documents),
    )
    validation_metadata_store: ValidationMetadataStore = SQLiteValidationMetadataStore(
        catalog_db_path
    )
    confidence_scorer = _maybe_build_confidence_scorer(
        settings,
        documents=documents,
        corpus_norms=corpus_norms_store,
    )
    sampling_state_store: SamplingStateStore = SQLiteSamplingStateStore(catalog_db_path)
    hitl_router = _maybe_build_hitl_router(
        settings,
        confidence_scorer=confidence_scorer,
        sampling_state=sampling_state_store,
    )
    semantic_outputs = SemanticOutputService(
        documents=documents,
        extraction_jobs=extraction_jobs,
        semantic_extractor=semantic_extractor,
        markdown_generator=markdown_generator,
        confidence_scorer=confidence_scorer,
        validation_metadata_store=validation_metadata_store,
        hitl_router=hitl_router,
    )
    entity_extractor = _maybe_build_entity_extractor(settings, llm=llm_client)
    knowledge_relations = KnowledgeRelationsService(graph_store=graph_store)
    document_relations_store: DocumentRelationsStore = SQLiteDocumentRelationsStore(catalog_db_path)
    document_relations_cache = DocumentRelationsCache(
        store=document_relations_store,
        relations=knowledge_relations,
    )
    # #392: build the Claim extractor before the projector wiring so
    # we can hand it in alongside the other side-effect deps. Same
    # both-or-nothing posture as the in-memory path; when no LLM is
    # configured ``claim_extractor`` is None and the post-projection
    # hook stays a no-op.
    claim_extractor = _maybe_build_claim_extractor(
        settings,
        llm=llm_client,
        llm_model=llm_model,
    )
    # #411: same as in build_services — only present when the LLM
    # provider is configured. ``None`` keeps the projector hook a
    # no-op (pre-#411 behaviour preserved).
    topic_extractor = _maybe_build_topic_extractor(
        settings,
        llm=llm_client,
        llm_model=llm_model,
    )
    # #385: same as build_services — wire the cache onto the
    # projector so post-projection warm fires for every validate
    # in the persistent runtime.
    if knowledge_projector is not None:
        knowledge_projector.set_document_relations_cache(document_relations_cache)
        # #390: same as build_services — wire the process_store so
        # the deterministic SOP parser writes Process rows through
        # the SQLite store on the persistent path.
        knowledge_projector.set_process_store(process_store)
        # #392: wire the Claim extractor + the SQLite-backed store
        # so the post-projection LLM pass writes Claims through the
        # persistent ClaimStore.
        knowledge_projector.set_claim_extractor(claim_extractor)
        knowledge_projector.set_claim_store(claim_store)
        # #411: wire the Topic extractor + the SQLite-backed
        # DocumentTopic store so the post-projection LLM pass
        # writes Topics through the persistent store.
        knowledge_projector.set_topic_extractor(topic_extractor)
        knowledge_projector.set_document_topic_store(document_topic_store)
    return PipelineServices(
        storage=storage,
        documents=documents,
        parsers=parsers,
        extraction_jobs=extraction_jobs,
        semantic_extractor=semantic_extractor,
        markdown_generator=markdown_generator,
        semantic_outputs=semantic_outputs,
        idempotency=SQLiteIdempotencyStore(root / "idempotency.sqlite3"),
        graph_store=graph_store,
        knowledge_projector=knowledge_projector,
        entity_extractor=entity_extractor,
        embedding_client=embedding_client,
        knowledge_search=knowledge_search,
        knowledge_chat=_maybe_build_chat_service(
            llm=llm_client,
            llm_model=llm_model,
            knowledge_search=knowledge_search,
            graph_store=graph_store,
        ),
        knowledge_relations=knowledge_relations,
        document_relations_cache=document_relations_cache,
        knowledge_neighborhood=KnowledgeNeighborhoodService(graph_store=graph_store),
        knowledge_explore_search=(
            KnowledgeExploreSearchService(
                search=knowledge_search,
                graph_store=graph_store,
                documents=documents,
            )
            if knowledge_search is not None
            else None
        ),
        knowledge_atlas=(
            KnowledgeAtlasService(graph_store=graph_store, documents=documents)
            if graph_store is not None
            else None
        ),
        audit_events=_build_audit_store(settings, default_dir=root),
        auth=build_auth_service(settings),
        taxonomy=taxonomy,
        taxonomy_source_path=str(taxonomy_source_path) if taxonomy_source_path else None,
        taxonomy_store=taxonomy_store,
        claim_store=claim_store,
        process_store=process_store,
        document_topic_store=document_topic_store,
        settings=settings,
        confidence_scorer=confidence_scorer,
        hitl_router=hitl_router,
        sampling_state=sampling_state_store,
        validation_metadata=validation_metadata_store,
        corpus_norms=corpus_norms_store,
    )
