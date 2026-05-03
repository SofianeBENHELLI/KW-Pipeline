"""Vector retrieval primitive for the knowledge layer (Phase 3, ADR-015).

The :class:`KnowledgeSearchService` is the seam between the
``GET /knowledge/search`` route and the embedding + graph stores.
It owns the embed-the-query → query-the-vector-index → shape-the-result
flow so the route stays thin and the storage backends stay swappable.

The default ``pytest`` invocation runs this against
:class:`FakeEmbeddingClient` + :class:`InMemoryGraphStore`; the real
Voyage SDK + Neo4j are exercised only behind
``pytest -m embedding_integration``.
"""

from __future__ import annotations

import logging
import time

from app.schemas.knowledge import ChunkSearchResponse, ChunkSearchResult
from app.services.knowledge.embedding_client import EmbeddingClient
from app.services.knowledge.graph_store import (
    DEFAULT_VECTOR_SEARCH_LIMIT,
    MAX_VECTOR_SEARCH_LIMIT,
    VECTOR_INDEX_NAME,
    GraphStore,
)

log = logging.getLogger(__name__)


class KnowledgeSearchService:
    """Retrieve chunks ranked by cosine similarity to a query.

    Stateless beyond the injected clients; safe to construct once per
    ``PipelineServices`` and reuse across requests.
    """

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient,
        graph_store: GraphStore,
        index_name: str = VECTOR_INDEX_NAME,
    ) -> None:
        self._embedding_client = embedding_client
        self._graph_store = graph_store
        self._index_name = index_name

    @property
    def embedding_model(self) -> str:
        return self._embedding_client.name

    def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_VECTOR_SEARCH_LIMIT,
    ) -> ChunkSearchResponse:
        """Embed ``query`` and return the top-``limit`` ranked chunks.

        Empty or whitespace-only queries are rejected with
        :class:`ValueError`; the route layer maps that to a 422 with
        the public error envelope. ``limit`` is clamped to the
        :data:`MAX_VECTOR_SEARCH_LIMIT` ceiling so a malformed client
        cannot ask the index for thousands of rows.
        """
        if not query or not query.strip():
            raise ValueError("query must not be empty.")
        if limit < 1 or limit > MAX_VECTOR_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_VECTOR_SEARCH_LIMIT}; got {limit}.")

        started = time.perf_counter()
        query_vector = self._embedding_client.embed_query(query.strip())
        hits = self._graph_store.find_chunks_by_similarity(
            query_vector,
            limit=limit,
            index_name=self._index_name,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        results = [
            ChunkSearchResult(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                version_id=hit.version_id,
                section_id=hit.section_id,
                snippet=hit.snippet,
                score=hit.score,
            )
            for hit in hits
        ]

        log.info(
            "knowledge.search.queried",
            extra={
                "query_char_count": len(query),
                "top_k": limit,
                "result_count": len(results),
                "embedding_model": self._embedding_client.name,
                "latency_ms": elapsed_ms,
            },
        )

        return ChunkSearchResponse(
            query=query,
            embedding_model=self._embedding_client.name,
            query_embedding_dim=len(query_vector),
            results=results,
        )


__all__ = ["KnowledgeSearchService"]
