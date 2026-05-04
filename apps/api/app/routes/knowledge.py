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

from fastapi import APIRouter, HTTPException, Query

from app.dependencies import PipelineServices
from app.errors import ApiError, ErrorCode
from app.schemas.knowledge import (
    ChatRequest,
    ChatResponse,
    ChunkSearchResponse,
    KnowledgeGraphPage,
    KnowledgeGraphProjection,
)
from app.schemas.taxonomy import TaxonomyResponse
from app.services.knowledge.graph_store import (
    DEFAULT_GRAPH_PAGE_LIMIT,
    DEFAULT_VECTOR_SEARCH_LIMIT,
    MAX_GRAPH_PAGE_LIMIT,
    MAX_VECTOR_SEARCH_LIMIT,
)

from ._helpers import MIN_GRAPH_PAGE_LIMIT

log = logging.getLogger(__name__)


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
        """Read the operator-imposed taxonomy (ADR-017).

        Returns the loaded taxonomy when ``KW_TAXONOMY_PATH`` points
        at a YAML file the loader could parse; returns
        ``is_configured=false`` with empty ``categories`` otherwise.
        Never 404s — a missing taxonomy is a valid deployment state
        (the platform falls back to auto-deduced topic clustering)
        and the frontend uses ``is_configured`` to decide which empty
        state to render.
        """
        taxonomy = services.taxonomy
        is_configured = taxonomy is not None
        return TaxonomyResponse(
            is_configured=is_configured,
            source_path=services.taxonomy_source_path,
            categories=taxonomy.categories if taxonomy is not None else [],
        )

    return router
