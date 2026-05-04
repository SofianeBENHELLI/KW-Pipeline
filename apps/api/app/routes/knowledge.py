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

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import PipelineServices
from app.errors import ApiError, ErrorCode
from app.models.document import DocumentVersionStatus
from app.schemas.document import Document
from app.schemas.knowledge import (
    ChatRequest,
    ChatResponse,
    ChunkSearchResponse,
    KnowledgeCatalogItem,
    KnowledgeCatalogResponse,
    KnowledgeGraphPage,
    KnowledgeGraphProjection,
)
from app.schemas.taxonomy import TaxonomyCategory, TaxonomyResponse
from app.services.auth import User, get_current_user
from app.services.catalog_store import InvalidCursor, _encode_cursor
from app.services.knowledge.graph_store import (
    DEFAULT_GRAPH_PAGE_LIMIT,
    DEFAULT_VECTOR_SEARCH_LIMIT,
    MAX_GRAPH_PAGE_LIMIT,
    MAX_VECTOR_SEARCH_LIMIT,
)

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
    def get_document_graph(document_id: str) -> Any:
        """Knowledge graph projection for one document family (ADR-012)."""
        return services.graph_store.find_subgraph_for_document(document_id)

    @router.get(
        "/knowledge/graph",
        operation_id="get_knowledge_graph",
        response_model=KnowledgeGraphPage,
    )
    def get_knowledge_graph(
        limit: int = Query(default=DEFAULT_GRAPH_PAGE_LIMIT, ge=MIN_GRAPH_PAGE_LIMIT),
        cursor: str | None = None,
    ) -> Any:
        """Cursor-paginated walk of the catalog-wide projection (ADR-012)."""
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
        "/knowledge/search",
        operation_id="search_knowledge_chunks",
        response_model=ChunkSearchResponse,
    )
    def search_knowledge_chunks(
        q: str = Query(min_length=1, max_length=2000),
        limit: int = Query(default=DEFAULT_VECTOR_SEARCH_LIMIT, ge=1),
    ) -> Any:
        """Top-K chunk retrieval ranked by cosine similarity (ADR-015, #186).

        Requires both ``KW_KNOWLEDGE_LAYER_ENABLED=true`` and a
        ``VOYAGE_API_KEY`` to be configured. When either gate is off
        the route returns 503 with a stable public error code so the
        frontend can surface the right remediation.
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
            return services.knowledge_search.search(q, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/knowledge/chat",
        operation_id="chat_with_knowledge",
        response_model=ChatResponse,
    )
    def chat_with_knowledge(payload: ChatRequest) -> Any:
        """Grounded chat surface (Phase 3 follow-up).

        Builds a RAG / GraphRAG / Hybrid context from the configured
        retrieval primitives, asks the LLM for a free-text answer, and
        returns the answer alongside the citations the prompt was
        grounded in. Requires both ``ANTHROPIC_API_KEY`` and
        ``VOYAGE_API_KEY`` (the chat service seeds graph traversal
        from vector hits, so the search service must be wired). When
        either gate is off the route returns 503 with
        ``KW_CHAT_DISABLED`` and the public-error remediation copy.
        """
        if services.knowledge_chat is None:
            raise ApiError(
                status_code=503,
                code=ErrorCode.CHAT_DISABLED,
                message=(
                    "Grounded chat is disabled. The Phase 3 chat surface "
                    "requires KW_KNOWLEDGE_LAYER_ENABLED=true plus both "
                    "ANTHROPIC_API_KEY and VOYAGE_API_KEY to be configured."
                ),
                retryable=False,
                remediation=(
                    "Set KW_KNOWLEDGE_LAYER_ENABLED=true and provide both "
                    "ANTHROPIC_API_KEY and VOYAGE_API_KEY (or the KW_-prefixed "
                    "aliases) in the API environment, then restart the service."
                ),
            )
        try:
            return services.knowledge_chat.answer(
                payload.question,
                mode=payload.mode,
                top_k=payload.top_k,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(
        "/knowledge/taxonomy",
        operation_id="get_knowledge_taxonomy",
        response_model=TaxonomyResponse,
    )
    def get_knowledge_taxonomy() -> Any:
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
        # TODO(D.5): scope filtering not yet enforced. The params are
        # accepted here so the frontend can wire the workspace picker
        # ahead of time; D.5 will add the predicate that joins on
        # ``document_scopes`` to drop documents the caller can't see.
        # See ADR-020 §2 for the read-side filter shape.
        scope_kind: str | None = Query(default=None),
        scope_ref: str | None = Query(default=None),
        current_user: User = Depends(get_current_user),
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
        try:
            documents = services.documents.catalog.list_documents(
                cursor=cursor,
                limit=None,
                status_filter=None,
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
            tail = services.documents.catalog.list_documents(
                cursor=tail_cursor,
                limit=None,
                status_filter=None,
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
    scopes = services.documents.catalog.list_scopes_for_document(document.id)
    return KnowledgeCatalogItem(
        document_id=document.id,
        family_filename=latest.filename,
        latest_version_number=latest.version_number,
        latest_status=latest.status,
        version_count=len(sorted_versions),
        sha256=latest.sha256,
        scopes=scopes,
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
