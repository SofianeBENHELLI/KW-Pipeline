"""Corpus atlas summary service (#312, ADR-028).

Composes the catalog (validation status / recent documents) with the
graph store (topics / chunk-relation edges) to produce the read-only
summary that powers the Explorer's default home (#316).

The service is stateless and read-only. The route layer wires a
per-request scope predicate (``can_see_document``) so D.5 hidden-
existence applies — counts and rankings only ever cover documents
the caller can access.

Bridge / outlier detection delegates to :mod:`app.services.knowledge.scoring`
(the #314 policy module) so the atlas, the relation inspector, and
the neighborhood walker all rank the same way.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.schemas.knowledge_atlas import (
    AtlasBridgeDocument,
    AtlasOutlierRelation,
    AtlasRecentDocument,
    AtlasResponse,
    AtlasTopicSummary,
    AtlasValidationCoverage,
)
from app.services.knowledge.scoring import (
    bridge_document_score,
    is_outlier,
    relation_strength_score,
    topic_distance,
)

if TYPE_CHECKING:
    from app.schemas.document import Document
    from app.services.document_service import DocumentService
    from app.services.knowledge.graph_store import GraphStore

log = logging.getLogger(__name__)

#: Per-block caps. Generous enough to fill an Explorer home tile,
#: tight enough that the response payload stays renderable without
#: pagination. Bumping these is a wire-shape decision.
DEFAULT_TOP_TOPICS = 10
DEFAULT_RECENT_DOCUMENTS = 10
DEFAULT_BRIDGE_DOCUMENTS = 10
DEFAULT_OUTLIER_RELATIONS = 10

MAX_BLOCK_LIMIT = 50


class KnowledgeAtlasService:
    """Read-only summary builder for ``GET /knowledge/atlas``.

    All counts are computed lazily on each call — there is no cached
    snapshot. The atlas is bounded by the catalog size (one document
    walk for coverage + recent) and the topic count (one walk over
    ``topic`` nodes for the topic block); each is ~O(catalog) in the
    worst case, comfortably small for the Explorer's home request.
    """

    def __init__(
        self,
        *,
        graph_store: GraphStore,
        documents: DocumentService,
    ) -> None:
        self._graph_store = graph_store
        self._documents = documents

    def build(
        self,
        *,
        top_topics_limit: int = DEFAULT_TOP_TOPICS,
        recent_documents_limit: int = DEFAULT_RECENT_DOCUMENTS,
        bridge_documents_limit: int = DEFAULT_BRIDGE_DOCUMENTS,
        outlier_relations_limit: int = DEFAULT_OUTLIER_RELATIONS,
        can_see_document: Callable[[str], bool] | None = None,
    ) -> AtlasResponse:
        """Walk catalog + graph and assemble the five atlas blocks.

        ``can_see_document``: D.5 scope predicate. When supplied, every
        document referenced by any block is filtered through this
        callback; the route closes over a per-request cache so a
        document seen by both the topic block and the bridge block
        pays the access check once. ``None`` disables the filter
        (disabled-mode callers).
        """
        if not 1 <= top_topics_limit <= MAX_BLOCK_LIMIT:
            raise ValueError(
                f"top_topics_limit must be in [1, {MAX_BLOCK_LIMIT}]; got {top_topics_limit}."
            )
        if not 1 <= recent_documents_limit <= MAX_BLOCK_LIMIT:
            raise ValueError(
                f"recent_documents_limit must be in [1, {MAX_BLOCK_LIMIT}]; "
                f"got {recent_documents_limit}."
            )
        if not 1 <= bridge_documents_limit <= MAX_BLOCK_LIMIT:
            raise ValueError(
                f"bridge_documents_limit must be in [1, {MAX_BLOCK_LIMIT}]; "
                f"got {bridge_documents_limit}."
            )
        if not 1 <= outlier_relations_limit <= MAX_BLOCK_LIMIT:
            raise ValueError(
                f"outlier_relations_limit must be in [1, {MAX_BLOCK_LIMIT}]; "
                f"got {outlier_relations_limit}."
            )

        # Seed: every document in the catalog the caller can see. This
        # is the universe every block filters against; computing it
        # once means we make at most one ``can_see_document`` call per
        # document for the whole atlas response.
        all_documents = self._documents.list_documents()
        if can_see_document is None:
            visible_documents = list(all_documents)
        else:
            visible_documents = [d for d in all_documents if can_see_document(d.id)]
        visible_ids = {d.id for d in visible_documents}

        validation_coverage = _build_validation_coverage(visible_documents)
        recent_documents = _build_recent_documents(visible_documents, limit=recent_documents_limit)
        top_topics = self._build_top_topics(visible_ids=visible_ids, limit=top_topics_limit)
        bridge_documents = self._build_bridge_documents(
            visible_documents=visible_documents, limit=bridge_documents_limit
        )
        outlier_relations = self._build_outlier_relations(
            visible_ids=visible_ids, limit=outlier_relations_limit
        )

        log.info(
            "knowledge.atlas.built",
            extra={
                "visible_document_count": len(visible_documents),
                "top_topic_count": len(top_topics),
                "recent_document_count": len(recent_documents),
                "bridge_document_count": len(bridge_documents),
                "outlier_relation_count": len(outlier_relations),
            },
        )

        return AtlasResponse(
            top_topics=top_topics,
            validation_coverage=validation_coverage,
            recent_documents=recent_documents,
            bridge_documents=bridge_documents,
            outlier_relations=outlier_relations,
        )

    # ── Top topics ────────────────────────────────────────────────────

    def _build_top_topics(
        self,
        *,
        visible_ids: set[str],
        limit: int,
    ) -> list[AtlasTopicSummary]:
        topic_nodes = self._graph_store.find_nodes_by_kind("topic")
        if not topic_nodes:
            return []

        # Aggregate chunk → topic membership over visible chunks only
        # so D.5 is enforced at the topic-coverage level too.
        chunk_nodes = self._graph_store.find_nodes_by_kind("chunk")
        topic_chunk_counts: dict[str, int] = defaultdict(int)
        topic_document_sets: dict[str, set[str]] = defaultdict(set)
        for chunk in chunk_nodes:
            topic_id = chunk.properties.get("topic_id")
            if not isinstance(topic_id, str) or not topic_id:
                continue  # pragma: no cover - chunks without topics are skipped upstream
            document_id = chunk.properties.get("document_id")
            if not isinstance(document_id, str) or document_id not in visible_ids:
                continue
            topic_chunk_counts[topic_id] += 1
            topic_document_sets[topic_id].add(document_id)

        summaries: list[AtlasTopicSummary] = []
        for topic in topic_nodes:
            chunk_count = topic_chunk_counts.get(topic.id, 0)
            if chunk_count == 0:
                continue
            keywords_property = topic.properties.get("keywords")
            keywords = (
                [str(k) for k in keywords_property] if isinstance(keywords_property, list) else []
            )
            summaries.append(
                AtlasTopicSummary(
                    topic_id=topic.id,
                    label=topic.label,
                    keywords=keywords,
                    document_count=len(topic_document_sets.get(topic.id, set())),
                    chunk_count=chunk_count,
                )
            )

        # Rank by chunk_count desc, document_count desc, topic_id asc
        # for deterministic tie-breaking.
        summaries.sort(key=lambda s: (-s.chunk_count, -s.document_count, s.topic_id))
        return summaries[:limit]

    # ── Bridge documents ──────────────────────────────────────────────

    def _build_bridge_documents(
        self,
        *,
        visible_documents: list[Document],
        limit: int,
    ) -> list[AtlasBridgeDocument]:
        if not visible_documents:
            return []

        # Build (document_id) -> set of topic_ids touched.
        chunk_nodes = self._graph_store.find_nodes_by_kind("chunk")
        document_topics: dict[str, set[str]] = defaultdict(set)
        for chunk in chunk_nodes:
            document_id = chunk.properties.get("document_id")
            topic_id = chunk.properties.get("topic_id")
            if not isinstance(document_id, str) or not isinstance(topic_id, str):
                continue  # pragma: no cover - defensive type-narrowing
            if not topic_id:
                continue  # pragma: no cover - defensive type-narrowing
            document_topics[document_id].add(topic_id)

        # topic_id -> keywords list, for the Jaccard distance.
        topic_keyword_index: dict[str, list[str]] = {}
        for topic in self._graph_store.find_nodes_by_kind("topic"):
            keywords_property = topic.properties.get("keywords")
            topic_keyword_index[topic.id] = (
                [str(k) for k in keywords_property] if isinstance(keywords_property, list) else []
            )

        candidates: list[AtlasBridgeDocument] = []
        visible_id_to_title = {d.id: (d.original_filename or d.id) for d in visible_documents}
        for document_id, topic_ids in document_topics.items():
            if document_id not in visible_id_to_title:
                continue
            if len(topic_ids) < 2:
                continue
            keyword_sets = [topic_keyword_index.get(t, []) for t in sorted(topic_ids)]
            score = bridge_document_score(keyword_sets)
            if score <= 0.0:
                continue  # pragma: no cover - identical-keyword topics rare in practice
            candidates.append(
                AtlasBridgeDocument(
                    document_id=document_id,
                    title=visible_id_to_title[document_id],
                    score=score,
                    topic_count=len(topic_ids),
                )
            )

        candidates.sort(key=lambda d: (-d.score, -d.topic_count, d.document_id))
        return candidates[:limit]

    # ── Outlier relations ─────────────────────────────────────────────

    def _build_outlier_relations(
        self,
        *,
        visible_ids: set[str],
        limit: int,
    ) -> list[AtlasOutlierRelation]:
        # Outliers are chunk-relation edges (kind ``related_to``) that
        # qualify as both strong-or-medium-strong AND a bridge per the
        # #314 policy. We look up each endpoint's chunk → topic to
        # compute topic distance; chunks whose owning document is
        # invisible drop out entirely.
        chunk_nodes = self._graph_store.find_nodes_by_kind("chunk")
        chunk_index: dict[str, tuple[str, str | None]] = {}
        for chunk in chunk_nodes:
            document_id = chunk.properties.get("document_id")
            topic_id = chunk.properties.get("topic_id")
            if not isinstance(document_id, str):
                continue  # pragma: no cover - defensive type-narrowing
            chunk_index[chunk.id] = (
                document_id,
                topic_id if isinstance(topic_id, str) and topic_id else None,
            )

        topic_keyword_index: dict[str, list[str]] = {}
        for topic in self._graph_store.find_nodes_by_kind("topic"):
            keywords_property = topic.properties.get("keywords")
            topic_keyword_index[topic.id] = (
                [str(k) for k in keywords_property] if isinstance(keywords_property, list) else []
            )

        # Walk every chunk's incident edges. Deduplicate via the edge
        # id so an edge whose source and target both live in the
        # visible set isn't counted twice.
        seen_edge_ids: set[str] = set()
        candidates: list[AtlasOutlierRelation] = []
        for chunk_id in chunk_index:
            for edge in self._graph_store.find_edges_incident_to_node(chunk_id):
                if edge.id in seen_edge_ids:
                    continue
                seen_edge_ids.add(edge.id)
                if edge.kind != "related_to":
                    continue  # pragma: no cover - other edge kinds aren't outlier candidates
                source_meta = chunk_index.get(edge.source_id)
                target_meta = chunk_index.get(edge.target_id)
                if source_meta is None or target_meta is None:
                    continue  # pragma: no cover - chunk index always covers chunk endpoints
                source_doc, source_topic = source_meta
                target_doc, target_topic = target_meta
                if source_doc not in visible_ids or target_doc not in visible_ids:
                    continue
                raw_score = edge.properties.get("score")
                if not isinstance(raw_score, (int, float)):
                    continue  # pragma: no cover - defensive type-narrowing
                shared_keywords_property = edge.properties.get("shared_keywords")
                shared_keywords = (
                    [str(k) for k in shared_keywords_property]
                    if isinstance(shared_keywords_property, list)
                    else []
                )
                reason_property = edge.properties.get("reason")
                reason = reason_property if isinstance(reason_property, str) else None
                score = relation_strength_score(
                    raw_score=float(raw_score),
                    shared_keyword_count=len(shared_keywords),
                )
                source_keywords = topic_keyword_index.get(source_topic, []) if source_topic else []
                target_keywords = topic_keyword_index.get(target_topic, []) if target_topic else []
                distance = topic_distance(source_keywords, target_keywords)
                bridge = distance >= 0.60  # mirrors BRIDGE_TOPIC_DISTANCE_THRESHOLD
                if not is_outlier(score=score, is_bridge=bridge):
                    continue
                candidates.append(
                    AtlasOutlierRelation(
                        relation_id=edge.id,
                        kind=edge.kind,
                        source_id=edge.source_id,
                        target_id=edge.target_id,
                        score=score,
                        reason=reason,
                        shared_keywords=shared_keywords,
                    )
                )

        candidates.sort(key=lambda r: (-r.score, r.relation_id))
        return candidates[:limit]


# ── Validation coverage + recent documents (pure functions) ───────────


def _build_validation_coverage(documents: list[Document]) -> AtlasValidationCoverage:
    counts = {"VALIDATED": 0, "NEEDS_REVIEW": 0, "REJECTED": 0, "OTHER": 0}
    for document in documents:
        latest = next(
            (v for v in document.versions if v.id == document.latest_version_id),
            None,
        )
        if latest is None:  # pragma: no cover - defensive: catalog always has latest
            counts["OTHER"] += 1
            continue
        status_value = (
            latest.status.value if hasattr(latest.status, "value") else str(latest.status)
        )
        if status_value in counts:
            counts[status_value] += 1
        else:
            counts["OTHER"] += 1
    return AtlasValidationCoverage(
        total_documents=len(documents),
        validated_count=counts["VALIDATED"],
        needs_review_count=counts["NEEDS_REVIEW"],
        rejected_count=counts["REJECTED"],
        other_count=counts["OTHER"],
    )


def _build_recent_documents(documents: list[Document], *, limit: int) -> list[AtlasRecentDocument]:
    if not documents:
        return []
    sorted_documents = sorted(documents, key=lambda d: (d.created_at, d.id), reverse=True)
    output: list[AtlasRecentDocument] = []
    for document in sorted_documents[:limit]:
        latest = next(
            (v for v in document.versions if v.id == document.latest_version_id),
            None,
        )
        validation_status: str | None = None
        if latest is not None:  # pragma: no branch - catalog always has latest
            validation_status = (
                latest.status.value if hasattr(latest.status, "value") else str(latest.status)
            )
        output.append(
            AtlasRecentDocument(
                document_id=document.id,
                title=document.original_filename or document.id,
                created_at=document.created_at,
                validation_status=validation_status,
            )
        )
    return output


__all__ = [
    "DEFAULT_BRIDGE_DOCUMENTS",
    "DEFAULT_OUTLIER_RELATIONS",
    "DEFAULT_RECENT_DOCUMENTS",
    "DEFAULT_TOP_TOPICS",
    "MAX_BLOCK_LIMIT",
    "KnowledgeAtlasService",
]
