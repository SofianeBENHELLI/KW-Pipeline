"""Knowledge-layer routes — graph, search, chat, taxonomy.

Lives behind ``KW_KNOWLEDGE_LAYER_ENABLED`` and the per-feature
service gates (Voyage for search, Anthropic + Voyage for chat).
Each gated route returns a stable 503 envelope with the public error
code so the frontend can render the right "this feature is off"
remediation copy without inspecting the message text.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.dependencies import PipelineServices
from app.errors import ApiError, ErrorCode
from app.models.document import DocumentVersionStatus
from app.schemas.document import Document
from app.schemas.knowledge import (
    ChatRequest,
    ChatResponse,
    ChunkSearchResponse,
    ChunkSearchResult,
    KnowledgeCatalogItem,
    KnowledgeCatalogResponse,
    KnowledgeGraphPage,
    KnowledgeGraphProjection,
)
from app.schemas.knowledge_neighborhood import (
    NEIGHBORHOOD_DEFAULT_DEPTH,
    NEIGHBORHOOD_DEFAULT_LIMIT,
    NEIGHBORHOOD_MAX_DEPTH,
    NEIGHBORHOOD_MAX_LIMIT,
    NEIGHBORHOOD_MIN_DEPTH,
    NEIGHBORHOOD_MIN_LIMIT,
    FocusedNeighborhood,
    NeighborhoodRootKind,
)
from app.schemas.knowledge_relations import (
    AggregatedRelationEvidence,
    RelationEvidence,
)
from app.schemas.scope import ScopeRef
from app.schemas.taxonomy import TaxonomyCategory, TaxonomyResponse
from app.services.auth import (
    User,
    assert_can_access_document,
    get_caller_scopes,
    require_contributor,
    require_viewer,
)
from app.services.auth.scope_filter import ALL_SCOPES_SENTINEL, user_can_access
from app.services.catalog_store import InvalidCursor, _encode_cursor
from app.services.knowledge.graph_store import (
    DEFAULT_GRAPH_PAGE_LIMIT,
    DEFAULT_VECTOR_SEARCH_LIMIT,
    MAX_GRAPH_PAGE_LIMIT,
    MAX_VECTOR_SEARCH_LIMIT,
)
from app.services.knowledge.neighborhood import NeighborhoodNotFound
from app.services.knowledge.relations import RelationNotFound
from app.settings import Settings

from ._helpers import MIN_GRAPH_PAGE_LIMIT

log = logging.getLogger(__name__)

# Default visibility set for the EPIC-C catalog view (ADR-025 §3).
# ``SUPERSEDED`` is not in this set so the route hides stale rows
# behind a newer validated sibling. Operator/audit reads can still
# request hidden statuses by passing them explicitly via ``?status=``.
_CATALOG_DEFAULT_STATUS_VISIBILITY: frozenset[DocumentVersionStatus] = frozenset(
    {DocumentVersionStatus.VALIDATED, DocumentVersionStatus.NEEDS_REVIEW}
)

# Page-size guardrails for ``GET /knowledge/catalog``. Default matches
# ``GET /documents`` so clients can switch between the two without
# re-tuning ``limit``; ceiling is bumped to 200 to mirror the catalog
# list route per the EPIC-C C.3 spec.
_CATALOG_DEFAULT_PAGE_LIMIT = 50
_CATALOG_MIN_PAGE_LIMIT = 1
_CATALOG_MAX_PAGE_LIMIT = 200


def build_knowledge_router(services: PipelineServices) -> APIRouter:
    """Register the knowledge-layer routes."""
    router = APIRouter()

    @router.get(
        "/documents/{document_id}/graph",
        operation_id="get_document_graph",
        response_model=KnowledgeGraphProjection,
    )
    def get_document_graph(
        request: Request,
        document_id: str,
        current_user: User = Depends(require_viewer),
    ) -> Any:
        """Knowledge graph projection for one document family (ADR-012).

        D.5: 404 when the caller's scope set excludes a *known*
        document — same hidden-existence rule the rest of the
        ``/documents/{id}/...`` surface follows. Unknown ids fall
        through to the graph store, which returns an empty projection
        (existing contract). That keeps the route usable as a
        "do I have a graph for this?" probe without leaking which
        ids exist in other users' scopes — the empty payload is
        identical to "exists but no projection yet".
        """
        if services.documents.catalog.get_document(document_id) is not None:
            assert_can_access_document(request=request, document_id=document_id, user=current_user)
        return services.graph_store.find_subgraph_for_document(document_id)

    @router.get(
        "/knowledge/graph",
        operation_id="get_knowledge_graph",
        response_model=KnowledgeGraphPage,
    )
    def get_knowledge_graph(
        limit: int = Query(default=DEFAULT_GRAPH_PAGE_LIMIT, ge=MIN_GRAPH_PAGE_LIMIT),
        cursor: str | None = None,
        _user: User = Depends(require_viewer),
    ) -> Any:
        """Cursor-paginated walk of the catalog-wide projection (ADR-012).

        D.5 note: this route returns aggregated graph nodes/edges
        (sections, chunks, entities) — not document rows. The graph
        store's projection doesn't carry a per-node ``document_id``
        filter today, so a fully-correct scope filter would require a
        new GraphStore method (or a per-page join against
        ``document_scopes``). Deferred until D.6 / a follow-up — the
        document-list endpoints (``GET /documents``,
        ``/knowledge/catalog``) and the per-document graph
        (``GET /documents/{id}/graph``) ARE filtered, so a caller who
        does the obvious "list docs → fetch each graph" loop sees the
        correct restricted set. Direct hits to the catalog-wide graph
        page see the unfiltered projection — operator/audit shape.
        """
        if limit > MAX_GRAPH_PAGE_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"limit must be between {MIN_GRAPH_PAGE_LIMIT} "
                    f"and {MAX_GRAPH_PAGE_LIMIT}; got {limit}."
                ),
            )
        try:
            return services.graph_store.find_subgraph(limit=limit, cursor=cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(
        "/knowledge/neighborhood",
        operation_id="get_knowledge_neighborhood",
        response_model=FocusedNeighborhood,
    )
    def get_knowledge_neighborhood(
        request: Request,
        root_kind: NeighborhoodRootKind = Query(
            description="Kind of the root node — document, topic, or chunk.",
        ),
        root_id: str = Query(min_length=1, description="Stable id of the root node."),
        depth: int = Query(
            default=NEIGHBORHOOD_DEFAULT_DEPTH,
            ge=NEIGHBORHOOD_MIN_DEPTH,
            le=NEIGHBORHOOD_MAX_DEPTH,
            description="BFS expansion depth from the root.",
        ),
        edge_limit: int = Query(
            default=NEIGHBORHOOD_DEFAULT_LIMIT,
            ge=NEIGHBORHOOD_MIN_LIMIT,
            le=NEIGHBORHOOD_MAX_LIMIT,
            description=(
                "Maximum number of visible edges in the response. "
                "Edges past the budget land in ``hidden_edge_count``."
            ),
        ),
        min_strength: float = Query(
            default=0.0,
            ge=0.0,
            le=1.0,
            description=(
                "Filter out deterministic edges whose combined #314 "
                "score is below this threshold. Non-deterministic edges "
                "(structural / has_entity / belongs_to) are not affected."
            ),
        ),
        current_user: User = Depends(require_viewer),
    ) -> Any:
        """Bounded subgraph around one focus root (#310, ADR-028).

        Replaces the corpus-scale "fetch the whole graph and rank
        client-side" pattern with a server-side BFS that respects an
        edge budget and a strength threshold. Each visible edge
        carries its #314 score / strength_class / is_bridge /
        is_outlier inline so the canvas can rank without re-running
        the policy.

        D.5 hidden-existence: when the root node carries a
        ``document_id`` property (chunks, topics, or the document
        node itself), the scope filter applies — a caller without
        scope on that document sees a 404 indistinguishable from
        "no such root."

        Truncation metadata (``hidden_node_count`` /
        ``hidden_edge_count`` / ``truncated``) is always populated;
        clients render a "+ N more" indicator on the canvas without
        re-querying.
        """
        assert services.knowledge_neighborhood is not None
        # Pre-fetch the root so we can apply the scope check before
        # exposing any structural details. ``find_node_by_id``
        # returning ``None`` becomes the same 404 envelope as a
        # scope-hidden root.
        root_node = services.graph_store.find_node_by_id(root_id)
        if root_node is None or root_node.kind != root_kind:
            raise HTTPException(status_code=404, detail="Root node not found.")
        root_document_id: str | None = None
        if root_node.kind == "document":
            root_document_id = root_node.id
        else:
            doc_id_property = root_node.properties.get("document_id")
            if isinstance(doc_id_property, str) and doc_id_property:
                root_document_id = doc_id_property
        if root_document_id is not None:
            assert_can_access_document(
                request=request,
                document_id=root_document_id,
                user=current_user,
            )
        try:
            return services.knowledge_neighborhood.neighborhood(
                root_kind=root_kind,
                root_id=root_id,
                depth=depth,
                edge_limit=edge_limit,
                min_strength=min_strength,
            )
        except NeighborhoodNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/knowledge/relations/aggregate",
        operation_id="explain_aggregate_relation",
        response_model=AggregatedRelationEvidence,
    )
    def explain_aggregate_relation(
        request: Request,
        source_document_id: str = Query(min_length=1),
        target_document_id: str = Query(min_length=1),
        top_n: int = Query(default=10, ge=1, le=100),
        current_user: User = Depends(require_viewer),
    ) -> Any:
        """Synthesised doc-doc relation evidence (#311, ADR-028).

        Walks the chunk-level edges that cross the boundary between
        ``source_document_id`` and ``target_document_id``, scores each
        via the #314 policy, and returns the top contributing pairs
        sorted by combined score.

        D.5: both endpoints must be visible to the caller. Either side
        hidden by the scope filter → 404 (hidden-existence) before the
        graph walk runs.

        Returns 404 with a ``KW_NOT_FOUND`` envelope when the documents
        have no detectable cross-boundary edges. ``pair_count`` on the
        response is the un-truncated total so the frontend can render
        a "+ N more contributing pairs" indicator.
        """
        # Hidden-existence: enforce on both endpoints before any
        # graph-store work.
        assert_can_access_document(
            request=request, document_id=source_document_id, user=current_user
        )
        assert_can_access_document(
            request=request, document_id=target_document_id, user=current_user
        )
        # ``knowledge_relations`` is always wired in ``build_services``
        # (graph_store is always-on) — the field is Optional only so
        # back-compat tests that build ``PipelineServices`` partially
        # don't break. If we ever land here with ``None``, that's a
        # construction bug, not a runtime gate.
        assert services.knowledge_relations is not None
        try:
            return services.knowledge_relations.explain_aggregate(
                source_document_id=source_document_id,
                target_document_id=target_document_id,
                top_n=top_n,
            )
        except RelationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/knowledge/relations/{relation_id}",
        operation_id="explain_relation",
        response_model=RelationEvidence,
    )
    def explain_relation(
        request: Request,
        relation_id: str,
        current_user: User = Depends(require_viewer),
    ) -> Any:
        """Single-edge relation evidence (#311, ADR-028).

        Resolves the ``relation_id`` to its stored ``GraphEdge`` and
        projects it onto :class:`RelationEvidence` — kind, provenance
        class, score (deterministic) or confidence (LLM), reason,
        shared keywords, source chunks, citations.

        D.5: when the edge carries a ``document_id`` property the
        scope filter applies — a caller without scope on that document
        sees 404 (hidden-existence), same envelope as missing-edge.
        Edges that don't carry a document_id (rare; structural-only
        catalog-wide edges) are visible to any authenticated viewer.

        URL note: edge ids contain ``:`` and ``->`` separators per the
        projector's id pattern. Clients must URL-encode the
        ``relation_id`` path segment; FastAPI decodes transparently.
        """
        assert services.knowledge_relations is not None
        try:
            evidence = services.knowledge_relations.explain(relation_id)
        except RelationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if evidence.document_id is not None:
            assert_can_access_document(
                request=request,
                document_id=evidence.document_id,
                user=current_user,
            )
        return evidence

    @router.get(
        "/knowledge/search",
        operation_id="search_knowledge_chunks",
        response_model=ChunkSearchResponse,
    )
    def search_knowledge_chunks(
        q: str = Query(min_length=1, max_length=2000),
        limit: int = Query(default=DEFAULT_VECTOR_SEARCH_LIMIT, ge=1),
        current_user: User = Depends(require_viewer),
    ) -> Any:
        """Top-K chunk retrieval ranked by cosine similarity (ADR-015, #186).

        Requires both ``KW_KNOWLEDGE_LAYER_ENABLED=true`` and a
        ``VOYAGE_API_KEY`` to be configured. When either gate is off
        the route returns 503 with a stable public error code so the
        frontend can surface the right remediation.

        D.5: results are filtered to chunks whose owning document the
        caller can see. The filter runs after retrieval (not at the
        embedding store level) so a future store-side scope index is a
        drop-in optimisation. Empty results after the filter are
        returned as ``results: []`` (HTTP 200) — same shape as
        empty-retrieval today.
        """
        if services.knowledge_search is None:
            raise ApiError(
                status_code=503,
                code=ErrorCode.VECTOR_SEARCH_DISABLED,
                message=(
                    "Vector search is disabled. Phase 3 requires "
                    "KW_KNOWLEDGE_LAYER_ENABLED=true and VOYAGE_API_KEY "
                    "to be configured."
                ),
                retryable=False,
                remediation=(
                    "Set both KW_KNOWLEDGE_LAYER_ENABLED=true and a non-empty "
                    "VOYAGE_API_KEY (or KW_VOYAGE_API_KEY) in the API "
                    "environment, then restart the service."
                ),
            )
        if limit > MAX_VECTOR_SEARCH_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=(f"limit must be between 1 and {MAX_VECTOR_SEARCH_LIMIT}; got {limit}."),
            )
        try:
            response = services.knowledge_search.search(q, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        settings = Settings()
        catalog = services.documents.catalog
        filtered: list[ChunkSearchResult] = []
        seen: dict[str, bool] = {}
        for hit in response.results:
            cached = seen.get(hit.document_id)
            if cached is None:
                cached = user_can_access(
                    user=current_user,
                    document_id=hit.document_id,
                    catalog=catalog,
                    settings=settings,
                )
                seen[hit.document_id] = cached
            if cached:
                filtered.append(hit)
        # Re-emit the response with the filtered hit list. Other fields
        # (model id, query, embedding dim) are preserved verbatim so
        # operator-facing telemetry stays stable.
        return ChunkSearchResponse(
            query=response.query,
            embedding_model=response.embedding_model,
            query_embedding_dim=response.query_embedding_dim,
            results=filtered,
        )

    @router.post(
        "/knowledge/chat",
        operation_id="chat_with_knowledge",
        response_model=ChatResponse,
    )
    def chat_with_knowledge(
        payload: ChatRequest,
        current_user: User = Depends(require_contributor),
    ) -> Any:
        """Grounded chat surface (Phase 3 follow-up).

        Builds a RAG / GraphRAG / Hybrid context from the configured
        retrieval primitives, asks the LLM for a free-text answer, and
        returns the answer alongside the citations the prompt was
        grounded in. Requires an LLM key (``GEMINI_API_KEY`` or
        ``ANTHROPIC_API_KEY`` — see ADR-013 §6) plus ``VOYAGE_API_KEY``
        (the chat service seeds graph traversal from vector hits, so
        the search service must be wired). When any gate is off the
        route returns 503 with ``KW_CHAT_DISABLED`` and the
        public-error remediation copy.

        D.5: the retrieval set is filtered to documents the caller can
        see before being injected into the LLM prompt — so the model
        cannot quote / cite content that lives outside the user's
        scope. Citations on the response are guaranteed to resolve
        against documents the caller could otherwise list.
        """
        if services.knowledge_chat is None:
            raise ApiError(
                status_code=503,
                code=ErrorCode.CHAT_DISABLED,
                message=(
                    "Grounded chat is disabled. The Phase 3 chat surface "
                    "requires KW_KNOWLEDGE_LAYER_ENABLED=true, an LLM "
                    "provider key (GEMINI_API_KEY or ANTHROPIC_API_KEY), "
                    "and VOYAGE_API_KEY to be configured."
                ),
                retryable=False,
                remediation=(
                    "Set KW_KNOWLEDGE_LAYER_ENABLED=true, configure at least "
                    "one LLM key (GEMINI_API_KEY or ANTHROPIC_API_KEY), and "
                    "provide VOYAGE_API_KEY (or the KW_-prefixed aliases) in "
                    "the API environment, then restart the service."
                ),
            )
        settings = Settings()
        catalog = services.documents.catalog

        def _accessible(document_id: str) -> bool:
            return user_can_access(
                user=current_user,
                document_id=document_id,
                catalog=catalog,
                settings=settings,
            )

        try:
            return services.knowledge_chat.answer(
                payload.question,
                mode=payload.mode,
                top_k=payload.top_k,
                accessible_document_id=_accessible,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(
        "/knowledge/taxonomy",
        operation_id="get_knowledge_taxonomy",
        response_model=TaxonomyResponse,
    )
    def get_knowledge_taxonomy(
        _user: User = Depends(require_viewer),
    ) -> Any:
        """Read the hybrid taxonomy (ADR-017, #249).

        The response merges two halves under one ``categories`` list:

        * **Imposed** — categories parsed out of the operator-authored
          YAML at ``KW_TAXONOMY_PATH``. Each one is tagged
          ``source="imposed"`` by the loader.
        * **Computed** — categories synthesised from the topic-
          clustering output the projector wrote into the graph store
          (one ``topic`` node per cluster). Each one is tagged
          ``source="computed"``.

        **Merge rule:** dedupe by ``id``; **imposed wins on conflict**.
        That is, if the YAML defines ``id="hr"`` and a topic cluster
        also happens to land on ``id="hr"``, the operator's definition
        is the one that flows out — operators get the final say over
        what the catalog calls a category. The computed entry is
        dropped in that case (we do not merge labels / descriptions
        across sources).

        ``is_configured`` is ``true`` when **either** half has at
        least one category — an operator with no YAML but a populated
        topic-clustering output still gets a configured response.
        Only an entirely empty result (no YAML, no topic clusters)
        returns ``is_configured=false`` with an empty list. Never
        404s — a missing taxonomy is a valid deployment state.
        """
        taxonomy = services.taxonomy
        imposed: list[TaxonomyCategory] = list(taxonomy.categories) if taxonomy is not None else []
        computed = _load_computed_categories(services)

        # Imposed wins on conflict. Walk imposed first so its ids
        # become the authoritative set, then append computed entries
        # whose ids haven't been claimed.
        seen_ids = {category.id for category in imposed}
        merged: list[TaxonomyCategory] = list(imposed)
        for category in computed:
            if category.id in seen_ids:
                continue
            merged.append(category)
            seen_ids.add(category.id)

        is_configured = len(merged) > 0
        return TaxonomyResponse(
            is_configured=is_configured,
            source_path=services.taxonomy_source_path,
            categories=merged,
        )

    # ─── EPIC-C C.3 catalog view (ADR-025 §3) ─────────────────────────
    # Appended at the end of the file by convention so the parallel
    # frontend agent (E) editing the existing /knowledge/taxonomy
    # response can merge cleanly without a route-table reorder.
    @router.get(
        "/knowledge/catalog",
        operation_id="get_knowledge_catalog",
        response_model=KnowledgeCatalogResponse,
    )
    def get_knowledge_catalog(
        status: list[str] | None = Query(default=None),
        q: str | None = Query(default=None, max_length=200),
        cursor: str | None = Query(default=None),
        limit: int = Query(
            default=_CATALOG_DEFAULT_PAGE_LIMIT,
            ge=_CATALOG_MIN_PAGE_LIMIT,
            le=_CATALOG_MAX_PAGE_LIMIT,
        ),
        # D.5: scope filtering enforced. ``scope_kind`` / ``scope_ref``
        # are accepted as query params and resolved against the
        # caller's allowed scope set via ``get_caller_scopes``. Cross
        # user / community / project asks return 403 until the Swym
        # membership client (D.3) ships. See ADR-020 §2 for the
        # read-side filter shape.
        caller_scopes: tuple[ScopeRef, ...] = Depends(get_caller_scopes),
        _user: User = Depends(require_viewer),
    ) -> Any:
        """Paginated catalog view filtered for the EPIC-C surface (ADR-025).

        Differences from ``GET /documents``:

        - **SUPERSEDED-aware "latest"**: ``latest_status`` is the highest
          version-numbered version whose status is NOT ``SUPERSEDED``.
          A document whose only versions are ``SUPERSEDED`` is hidden
          entirely — there's nothing to review.
        - **Default visibility**: ``VALIDATED`` and ``NEEDS_REVIEW`` are
          the only statuses shown by default. ``REJECTED``, ``FAILED``,
          and ``SUPERSEDED`` are hidden unless the explicit ``status=``
          filter requests them (admin/audit use case).
        - **Scope params (``scope_kind`` / ``scope_ref``) are accepted but
          not yet enforced** — D.5 wires the predicate. The frontend can
          start sending them today without any backend behaviour change.

        Cursor encoding is shared with ``GET /documents`` (the
        catalog's ``(created_at, id)`` codec) so a future change to the
        codec only happens once.
        """
        # Normalize the explicit status filter. Empty / whitespace
        # strings are dropped; unknown values yield 400 with the same
        # error message ``GET /documents`` uses so clients debug the
        # filter without inspecting two different error texts.
        status_set: frozenset[DocumentVersionStatus] | None = None
        if status:
            valid_values = {s.value for s in DocumentVersionStatus}
            normalized = {value.strip().upper() for value in status if value.strip()}
            unknown = normalized - valid_values
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Unknown status: {', '.join(sorted(unknown))}. "
                        f"Allowed values: {', '.join(sorted(valid_values))}."
                    ),
                )
            if normalized:
                status_set = frozenset(DocumentVersionStatus(v) for v in normalized)

        filename_query = q.strip() if q is not None else None
        if not filename_query:
            filename_query = None

        # Pull *all* documents with the cursor / filename predicates
        # honoured at the store layer. We then apply the EPIC-C-specific
        # SUPERSEDED + visibility filter in-memory because the latest
        # non-superseded version is a derived field the store doesn't
        # currently index.
        #
        # D.5: when ``caller_scopes`` is the
        # :data:`ALL_SCOPES_SENTINEL` (``KW_AUTH_MODE=disabled``) we
        # walk the unfiltered list. Otherwise we walk only documents
        # in the caller's scope set; with the strict
        # ``personal:<user.id>`` default the scoped store call is the
        # whole catalog the caller can see anyway.
        try:
            if caller_scopes == ALL_SCOPES_SENTINEL:
                documents = services.documents.catalog.list_documents(
                    cursor=cursor,
                    limit=None,
                    status_filter=None,
                    filename_query=filename_query,
                )
            else:
                documents = _scoped_documents_for_catalog(
                    services=services,
                    caller_scopes=caller_scopes,
                    cursor=cursor,
                    filename_query=filename_query,
                )
        except InvalidCursor as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid cursor: {exc}",
            ) from exc

        visibility = status_set or _CATALOG_DEFAULT_STATUS_VISIBILITY
        items: list[KnowledgeCatalogItem] = []
        last_visible: Document | None = None
        for document in documents:
            row = _build_catalog_item(
                document=document,
                visibility=visibility,
                explicit_status_filter=status_set is not None,
                services=services,
            )
            if row is None:
                continue
            items.append(row)
            last_visible = document
            if len(items) >= limit:
                break

        # Emit a next cursor only when there are still rows behind the
        # current page. Because we filter post-store, a "page short of
        # limit" is not a reliable end-of-stream signal — we have to
        # peek for a strictly-greater visible candidate.
        next_cursor: str | None = None
        if len(items) >= limit and last_visible is not None:
            tail_cursor = _encode_cursor((last_visible.created_at, last_visible.id))
            if caller_scopes == ALL_SCOPES_SENTINEL:
                tail = services.documents.catalog.list_documents(
                    cursor=tail_cursor,
                    limit=None,
                    status_filter=None,
                    filename_query=filename_query,
                )
            else:
                tail = _scoped_documents_for_catalog(
                    services=services,
                    caller_scopes=caller_scopes,
                    cursor=tail_cursor,
                    filename_query=filename_query,
                )
            for document in tail:
                row = _build_catalog_item(
                    document=document,
                    visibility=visibility,
                    explicit_status_filter=status_set is not None,
                    services=services,
                )
                if row is not None:
                    next_cursor = tail_cursor
                    break

        return KnowledgeCatalogResponse(items=items, next_cursor=next_cursor)

    return router


def _scoped_documents_for_catalog(
    *,
    services: PipelineServices,
    caller_scopes: tuple[ScopeRef, ...],
    cursor: str | None,
    filename_query: str | None,
) -> list[Document]:
    """Return documents the caller can see for the catalog projection.

    Walks every scope in ``caller_scopes`` via
    :meth:`CatalogStore.list_documents_in_scope`, merges the results
    into ``(created_at, id)`` order, and filters by ``filename_query``
    in-memory because the scoped store call doesn't index it yet. The
    cursor is applied uniformly across the merged stream.

    For the strict D.5 default — a single ``personal:<user.id>`` —
    this is one round-trip; multi-scope callers (D.3) iterate.
    """
    seen: dict[str, Document] = {}
    for scope in caller_scopes:
        # The scoped list returns ``(page, next_cursor)`` and uses the
        # same cursor codec; we ask for the whole stream by walking
        # forward until ``next_cursor is None``. Acceptable because the
        # caller's personal scope is small in the D.5 timeframe (one
        # user's uploads). Multi-scope queries that prove out a perf
        # issue land a paginated merge in D.3.
        next_cursor = cursor
        while True:
            page, next_cursor = services.documents.catalog.list_documents_in_scope(
                scope.kind,
                scope.ref,
                cursor=next_cursor,
                limit=_CATALOG_MAX_PAGE_LIMIT,
            )
            for doc in page:
                seen.setdefault(doc.id, doc)
            if next_cursor is None:
                break
    documents = sorted(seen.values(), key=lambda d: (d.created_at, d.id))
    if filename_query:
        needle = filename_query.lower()
        documents = [d for d in documents if needle in d.original_filename.lower()]
    return documents


def _build_catalog_item(
    *,
    document: Document,
    visibility: frozenset[DocumentVersionStatus],
    explicit_status_filter: bool,
    services: PipelineServices,
) -> KnowledgeCatalogItem | None:
    """Project a :class:`Document` into a :class:`KnowledgeCatalogItem`.

    Returns ``None`` when the document should be hidden from the
    response — either because every version is ``SUPERSEDED`` (no
    "latest visible" version exists) or because the resolved
    ``latest_status`` is not in the active visibility set.

    ``explicit_status_filter`` flips the SUPERSEDED handling: when the
    caller explicitly requests SUPERSEDED via ``?status=SUPERSEDED``,
    we still need to expose the row, so we fall back to the unfiltered
    latest version. Otherwise the "filter out SUPERSEDED first" rule
    applies and a stale-only family is hidden entirely.
    """
    if not document.versions:
        return None
    sorted_versions = sorted(document.versions, key=lambda v: v.version_number)
    if explicit_status_filter and DocumentVersionStatus.SUPERSEDED in visibility:
        # Audit / admin path — show the highest-numbered version,
        # whatever its status, so SUPERSEDED rows surface.
        latest = sorted_versions[-1]
    else:
        non_superseded = [
            v for v in sorted_versions if v.status != DocumentVersionStatus.SUPERSEDED
        ]
        if not non_superseded:
            return None
        latest = max(non_superseded, key=lambda v: v.version_number)
    if latest.status not in visibility:
        return None
    # #258 — every catalog read path now populates ``Document.scopes``
    # (filtered to active links), so we surface that directly instead
    # of a follow-up ``list_scopes_for_document`` round-trip.
    return KnowledgeCatalogItem(
        document_id=document.id,
        family_filename=latest.filename,
        latest_version_number=latest.version_number,
        latest_status=latest.status,
        version_count=len(sorted_versions),
        sha256=latest.sha256,
        scopes=list(document.scopes),
    )


def _load_computed_categories(services: PipelineServices) -> list[TaxonomyCategory]:
    """Synthesise ``TaxonomyCategory`` entries from topic-clustering output.

    Reads every ``kind="topic"`` node out of the graph store (the
    projector emits one per cluster; see
    :mod:`app.services.knowledge.topic_clustering` and
    :class:`KnowledgeProjector.project_topics`) and shapes it into the
    taxonomy wire model with ``source="computed"``.

    Defensive against:

    * a graph store that doesn't yet support
      :meth:`GraphStore.find_nodes_by_kind` (e.g. an older test fake)
      → returns an empty list rather than 500ing.
    * topic nodes whose properties are missing required fields
      → that one node is skipped (logged at debug); the others still
      flow through.
    * topics whose ``label`` is empty or whose synthesised
      ``description`` would be empty → padded with a deterministic
      fallback so the schema's ``min_length=1`` validators don't
      reject the synthesised category.
    """
    graph_store = getattr(services, "graph_store", None)
    if graph_store is None:
        return []
    finder = getattr(graph_store, "find_nodes_by_kind", None)
    if finder is None:
        # Backwards-compat: a third-party store that hasn't been
        # updated to the #249 protocol still serves Phase 1/2/3.
        return []
    try:
        topic_nodes = finder("topic")
    except Exception:  # noqa: BLE001 - defensive
        log.warning("knowledge.taxonomy.computed_lookup_failed", exc_info=True)
        return []

    categories: list[TaxonomyCategory] = []
    for node in topic_nodes:
        props = node.properties or {}
        label = (node.label or "").strip() or _string_prop(props, "label").strip()
        if not label:
            # Last-resort: use the topic id so the rail still has
            # something legible. Schemas reject empty labels.
            label = node.id
        keywords = _string_list_prop(props, "keywords")
        summary = _string_prop(props, "summary").strip()
        description = summary or (
            f"Auto-deduced topic cluster covering: {', '.join(keywords)}."
            if keywords
            else f"Auto-deduced topic cluster {node.id}."
        )
        try:
            categories.append(
                TaxonomyCategory(
                    id=node.id,
                    label=label,
                    description=description,
                    subcategories=[],
                    source="computed",
                )
            )
        except Exception:  # noqa: BLE001 - skip malformed cluster
            log.debug(
                "knowledge.taxonomy.computed_node_skipped",
                extra={"topic_id": node.id},
                exc_info=True,
            )
            continue
    # Stable ordering — find_nodes_by_kind already sorts by id, but
    # repeat the guarantee here so future callers can depend on it.
    categories.sort(key=lambda c: c.id)
    return categories


def _string_prop(props: dict[str, Any], key: str) -> str:
    value = props.get(key)
    return value if isinstance(value, str) else ""


def _string_list_prop(props: dict[str, Any], key: str) -> list[str]:
    value = props.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
