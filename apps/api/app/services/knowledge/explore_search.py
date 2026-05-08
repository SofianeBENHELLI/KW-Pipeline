"""Multi-kind Explorer search service (#313, ADR-028).

Composes the existing :class:`KnowledgeSearchService` (chunk-level
vector retrieval) with graph-store + catalog reads to produce a
grouped result set: chunks, documents, topics. Entities and relations
are deferred to v0.2 of the wire shape — the response includes the
empty lists so consumers can render the shell unchanged.

The service is read-only and stateless. It expects every dependency
to be wired before construction; the route layer 503s when the
underlying ``KnowledgeSearchService`` is missing (Phase 3 disabled).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.schemas.knowledge_explore_search import (
    ExploreSearchChunk,
    ExploreSearchDocument,
    ExploreSearchResponse,
    ExploreSearchTopic,
)

if TYPE_CHECKING:
    from app.services.document_service import DocumentService
    from app.services.knowledge.graph_store import GraphStore
    from app.services.knowledge.search import KnowledgeSearchService

log = logging.getLogger(__name__)

#: Documents-group cap. Aggregating chunks → documents may produce
#: many doc-level hits; the cap mirrors the chunk-search ceiling so
#: the response payload stays bounded.
DEFAULT_DOCUMENT_LIMIT = 10
MAX_DOCUMENT_LIMIT = 50

#: Topics-group cap. Same shape as the document cap.
DEFAULT_TOPIC_LIMIT = 10
MAX_TOPIC_LIMIT = 50

#: Per-document contributing-chunk cap. The Explorer renders 1-3
#: snippets per document by default; 5 is the ceiling the route's
#: bounded-payload contract enforces.
DEFAULT_CONTRIBUTING_CHUNKS = 3
MAX_CONTRIBUTING_CHUNKS = 5


class KnowledgeExploreSearchService:
    """Aggregate chunk-level search results into the Explorer's grouped
    response shape.

    The service does NOT re-implement vector retrieval — it delegates
    to :class:`KnowledgeSearchService` and projects the chunks onto
    documents (via ``document_id``) and topics (via the chunks' graph
    nodes carrying ``topic_id``).
    """

    def __init__(
        self,
        *,
        search: KnowledgeSearchService,
        graph_store: GraphStore,
        documents: DocumentService,
    ) -> None:
        self._search = search
        self._graph_store = graph_store
        self._documents = documents

    def search(
        self,
        *,
        query: str,
        chunk_limit: int = 10,
        document_limit: int = DEFAULT_DOCUMENT_LIMIT,
        topic_limit: int = DEFAULT_TOPIC_LIMIT,
        contributing_chunks_per_document: int = DEFAULT_CONTRIBUTING_CHUNKS,
        can_see_document: Callable[[str], bool] | None = None,
    ) -> ExploreSearchResponse:
        """Run a vector search and project it across the kind groups.

        ``can_see_document``: per-call predicate that returns whether
        the caller can access the given document_id. The route layer
        constructs this with a closure over the auth context + a
        per-request cache so a doc referenced by multiple chunks
        pays the catalog hit once. ``None`` means "no scope filter"
        (disabled-mode callers).
        """
        if not query or not query.strip():
            raise ValueError("query must not be empty.")
        if chunk_limit < 1 or chunk_limit > 50:
            raise ValueError(f"chunk_limit must be in [1, 50]; got {chunk_limit}.")
        if document_limit < 1 or document_limit > MAX_DOCUMENT_LIMIT:
            raise ValueError(
                f"document_limit must be in [1, {MAX_DOCUMENT_LIMIT}]; got {document_limit}."
            )
        if topic_limit < 1 or topic_limit > MAX_TOPIC_LIMIT:
            raise ValueError(f"topic_limit must be in [1, {MAX_TOPIC_LIMIT}]; got {topic_limit}.")
        if (
            contributing_chunks_per_document < 1
            or contributing_chunks_per_document > MAX_CONTRIBUTING_CHUNKS
        ):
            raise ValueError(
                f"contributing_chunks_per_document must be in "
                f"[1, {MAX_CONTRIBUTING_CHUNKS}]; got "
                f"{contributing_chunks_per_document}."
            )

        # Step 1: chunk-level search via the existing Phase 3 service.
        chunk_response = self._search.search(query, limit=chunk_limit)

        # Step 2: scope filter — drop chunks whose owning document the
        # caller can't access. Empty hits short-circuit the rest.
        if can_see_document is None:
            accessible_chunk_results = list(chunk_response.results)
        else:
            accessible_chunk_results = [
                r for r in chunk_response.results if can_see_document(r.document_id)
            ]

        # Step 3: project chunks onto the wire shape (no validation_status
        # / source-backed lookup yet — those land per-document below).
        chunks: list[ExploreSearchChunk] = [
            ExploreSearchChunk(
                chunk_id=r.chunk_id,
                document_id=r.document_id,
                version_id=r.version_id,
                section_id=r.section_id,
                snippet=r.snippet,
                score=r.score,
            )
            for r in accessible_chunk_results
        ]

        # Step 4: aggregate by document. Best-score-first; track the
        # contributing chunks per document up to the cap.
        doc_buckets: dict[str, list[ExploreSearchChunk]] = {}
        for chunk in chunks:
            doc_buckets.setdefault(chunk.document_id, []).append(chunk)

        documents: list[ExploreSearchDocument] = []
        for document_id, bucket in doc_buckets.items():
            top_chunks = sorted(bucket, key=lambda c: -c.score)[:contributing_chunks_per_document]
            best_score = top_chunks[0].score if top_chunks else 0.0
            title, validation_status, is_source_backed = self._document_metadata(document_id)
            documents.append(
                ExploreSearchDocument(
                    document_id=document_id,
                    title=title,
                    score=best_score,
                    validation_status=validation_status,
                    is_source_backed=is_source_backed,
                    contributing_chunks=top_chunks,
                )
            )
        documents.sort(key=lambda d: (-d.score, d.document_id))
        documents = documents[:document_limit]

        # Step 5: aggregate by topic. Look up the topic_id on each
        # chunk node via the graph store; group chunks by topic.
        topic_buckets: dict[str, list[ExploreSearchChunk]] = {}
        for chunk in chunks:
            node = self._graph_store.find_node_by_id(chunk.chunk_id)
            if node is None:
                continue
            topic_id_property = node.properties.get("topic_id")
            if not isinstance(topic_id_property, str) or not topic_id_property:
                continue
            topic_buckets.setdefault(topic_id_property, []).append(chunk)

        topics: list[ExploreSearchTopic] = []
        for topic_id, evidence in topic_buckets.items():
            topic_node = self._graph_store.find_node_by_id(topic_id)
            label = topic_node.label if topic_node is not None else topic_id
            keywords_property = (
                topic_node.properties.get("keywords") if topic_node is not None else []
            )
            keywords = (
                [str(k) for k in keywords_property] if isinstance(keywords_property, list) else []
            )
            top_evidence = sorted(evidence, key=lambda c: -c.score)[
                :contributing_chunks_per_document
            ]
            best_score = top_evidence[0].score if top_evidence else 0.0
            topics.append(
                ExploreSearchTopic(
                    topic_id=topic_id,
                    label=label,
                    keywords=keywords,
                    score=best_score,
                    evidence_chunks=top_evidence,
                )
            )
        topics.sort(key=lambda t: (-t.score, t.topic_id))
        topics = topics[:topic_limit]

        log.info(
            "knowledge.explore_search.queried",
            extra={
                "query_char_count": len(query),
                "chunk_hits": len(chunks),
                "document_hits": len(documents),
                "topic_hits": len(topics),
                "scope_filtered_count": len(chunk_response.results) - len(chunks),
            },
        )

        return ExploreSearchResponse(
            query=query,
            embedding_model=chunk_response.embedding_model,
            chunks=chunks,
            documents=documents,
            topics=topics,
        )

    def _document_metadata(self, document_id: str) -> tuple[str, str | None, bool]:
        """Resolve a document's title + trust flags.

        Falls back to the raw document_id when the catalog can't
        return a row — empty result rather than raising keeps the
        search surface usable on partial-projection demos.
        """
        try:
            document = self._documents.get_document(document_id)
        except (KeyError, AttributeError):
            return document_id, None, False
        if document is None:
            return document_id, None, False
        title = document.original_filename or document_id
        # Trust flags: the latest version's status (if known).
        # Phase 1's ``DocumentVersionStatus`` is the authoritative
        # field for "validated"; ``is_source_backed`` is reserved
        # for the Phase-2 entity-source-backed signal which isn't
        # surfaced on the document level today — keep False until a
        # follow-up wires it in.
        validation_status: str | None = None
        latest = next(
            (v for v in document.versions if v.id == document.latest_version_id),
            None,
        )
        if latest is not None:
            validation_status = (
                latest.status.value if hasattr(latest.status, "value") else str(latest.status)
            )
        return title, validation_status, False


__all__ = [
    "DEFAULT_CONTRIBUTING_CHUNKS",
    "DEFAULT_DOCUMENT_LIMIT",
    "DEFAULT_TOPIC_LIMIT",
    "MAX_CONTRIBUTING_CHUNKS",
    "MAX_DOCUMENT_LIMIT",
    "MAX_TOPIC_LIMIT",
    "KnowledgeExploreSearchService",
]
