"""Relation explanation + evidence service (#311, ADR-028).

Resolves a relation id to its full evidence shape — kind, provenance
class, score, reason, source chunks, citations — so the Explorer's
relation inspector (#318) can render the answer to "why are these two
things connected?" without re-deriving it from a partial graph
payload.

Two service entry points:

- :meth:`KnowledgeRelationsService.explain` — single stored edge.
  Maps every :class:`GraphEdgeKind` onto the right evidence projection.
- :meth:`KnowledgeRelationsService.explain_aggregate` — synthetic
  doc-doc edge. Walks both documents' projected subgraphs, filters
  chunk-level edges that cross the boundary, scores each via #314,
  returns the top contributing pairs.

Both raise :class:`RelationNotFound` when the lookup fails so the
route layer translates it into a clean 404 envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.schemas.knowledge import GraphEdge, GraphEdgeKind
from app.schemas.knowledge_relations import (
    AggregatedRelationEvidence,
    ContributingChunkPair,
    ProvenanceClass,
    RelationEvidence,
)
from app.services.knowledge.scoring import (
    StrengthClass,
    classify_strength,
    rank_edges,
    score_edge,
)

if TYPE_CHECKING:
    from app.services.knowledge.graph_store import GraphStore

# Edge kinds that count as candidates for doc-doc aggregation. Topic
# membership and structural edges aren't useful here — we want the
# semantic / LLM connections that explain why two documents are
# meaningfully related.
_AGGREGATABLE_KINDS: frozenset[GraphEdgeKind] = frozenset(
    {"related_to", "shares_keyword", "same_topic_as", "has_entity"}
)

_STRUCTURAL_KINDS: frozenset[GraphEdgeKind] = frozenset(
    {"part_of", "has_version", "has_chunk", "belongs_to"}
)
_DETERMINISTIC_KINDS: frozenset[GraphEdgeKind] = frozenset(
    {"related_to", "shares_keyword", "same_topic_as"}
)
_LLM_KINDS: frozenset[GraphEdgeKind] = frozenset({"has_entity"})


def _coerce_float(value: object, *, default: float = 0.0) -> float:
    """Pull a numeric out of the union-typed ``GraphEdge.properties`` map.

    Property values are typed ``str | int | float | bool | list[str] | None``
    so mypy refuses a bare ``float(...)`` call. We coerce defensively
    rather than annotate-cast so a malformed value (string list,
    boolean) lands at ``default`` instead of throwing in production.
    """
    if value is None or isinstance(value, list):
        return default
    if isinstance(value, bool):
        # bool is an int subclass — exclude explicitly so a stray
        # ``True`` doesn't masquerade as ``1.0``.
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default  # pragma: no cover - GraphPropertyValue covers every reachable type above


def _coerce_str_list(value: object) -> list[str]:
    """Pull a list-of-strings out of the union-typed property map.

    A non-list value returns ``[]`` rather than raising — same
    defensive posture as :func:`_coerce_float`.
    """
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


class RelationNotFound(LookupError):
    """Raised when no edge or aggregated doc-doc relation matches the
    request. The route layer maps this to a 404 ``KW_NOT_FOUND``
    envelope so the response shape matches every other "row not
    found" surface."""


def _project_deterministic_edge(edge: GraphEdge) -> RelationEvidence:
    """Pull the deterministic-relation evidence off a chunk-relation
    edge and apply the #314 scoring policy."""
    props = edge.properties
    raw_score = _coerce_float(props.get("score"), default=0.0)
    shared_keywords = _coerce_str_list(props.get("shared_keywords"))
    reason = props.get("reason")
    document_id = props.get("document_id")
    version_id = props.get("version_id")
    source_chunk_id = props.get("source_chunk_id") or edge.source_id
    target_chunk_id = props.get("target_chunk_id") or edge.target_id

    # Topic-keywords aren't on the edge today — they live on the topic
    # nodes the edge's chunks belong to. The relation inspector
    # surfaces this as a "needs topic context" hint when bridge
    # detection is unavailable. For now we score with empty topic
    # sets, which yields ``is_bridge=True`` (per the both-empty rule
    # in :func:`topic_distance`) — which is the conservative choice:
    # a bridge label means "we couldn't rule out that this is
    # surprising." The frontend renders bridge-when-unknown as
    # informational rather than emphatic.
    scored = score_edge(
        edge_id=edge.id,
        raw_score=raw_score,
        shared_keyword_count=len(shared_keywords),
        source_chunk_count=1,
        validation_bonus=0.0,
        source_topic_keywords=(),
        target_topic_keywords=(),
    )

    return RelationEvidence(
        relation_id=edge.id,
        kind=edge.kind,
        provenance_class=ProvenanceClass.DETERMINISTIC,
        source_id=edge.source_id,
        target_id=edge.target_id,
        score=scored.score,
        strength_class=scored.strength_class.value,
        is_bridge=scored.is_bridge,
        is_outlier=scored.is_outlier,
        contributing_factors=scored.contributing_factors,
        reason=str(reason) if reason is not None else None,
        shared_keywords=[str(k) for k in shared_keywords],
        source_chunk_ids=[str(source_chunk_id), str(target_chunk_id)],
        document_id=str(document_id) if document_id else None,
        version_id=str(version_id) if version_id else None,
    )


def _project_llm_edge(edge: GraphEdge) -> RelationEvidence:
    """Pull the LLM-relation evidence (``has_entity``) off the edge.

    The ``confidence`` field replaces the deterministic ``score`` —
    it's the LLM's per-triple confidence rather than a Jaccard /
    keyword-overlap derivation. We don't run :func:`score_edge` here
    because the input shape doesn't fit (no ``raw_score`` in
    ``[0, 1]`` from a deterministic similarity); instead the
    inspector renders ``confidence`` directly.
    """
    props = edge.properties
    confidence = props.get("confidence")
    document_id = props.get("document_id")
    version_id = props.get("version_id")
    section_id = props.get("section_id")
    predicate = props.get("predicate")
    source_reference_id = props.get("source_reference_id")

    return RelationEvidence(
        relation_id=edge.id,
        kind=edge.kind,
        provenance_class=ProvenanceClass.LLM,
        source_id=edge.source_id,
        target_id=edge.target_id,
        confidence=_coerce_float(confidence) if confidence is not None else None,
        predicate=str(predicate) if predicate is not None else None,
        source_section_id=str(section_id) if section_id else None,
        source_reference_ids=[str(source_reference_id)] if source_reference_id else [],
        document_id=str(document_id) if document_id else None,
        version_id=str(version_id) if version_id else None,
    )


def _project_structural_edge(edge: GraphEdge) -> RelationEvidence:
    """Bare-bones evidence for structural (parent-child) edges.

    Carries kind + endpoints + document/version context. No score, no
    confidence — the relationship itself is the provenance per
    ADR-012 §4.
    """
    props = edge.properties
    document_id = props.get("document_id")
    version_id = props.get("version_id")

    return RelationEvidence(
        relation_id=edge.id,
        kind=edge.kind,
        provenance_class=ProvenanceClass.STRUCTURAL,
        source_id=edge.source_id,
        target_id=edge.target_id,
        document_id=str(document_id) if document_id else None,
        version_id=str(version_id) if version_id else None,
    )


def _project_edge(edge: GraphEdge) -> RelationEvidence:
    """Top-level dispatch on edge kind. Centralises the kind→projector
    mapping so ``explain`` stays a thin wrapper.

    The three kind sets above cover every member of
    :data:`GraphEdgeKind`; mypy doesn't carry that exhaustiveness
    check, so the LLM branch is the implicit catch-all.
    """
    if edge.kind in _DETERMINISTIC_KINDS:
        return _project_deterministic_edge(edge)
    if edge.kind in _STRUCTURAL_KINDS:
        return _project_structural_edge(edge)
    return _project_llm_edge(edge)


class KnowledgeRelationsService:
    """Read service for the relation evidence API (#311).

    Holds the graph store reference and stays stateless; one instance
    serves every concurrent request. The aggregation method walks two
    documents' projected subgraphs per call — that's deliberately on
    the hot path so we don't have to maintain a doc-doc edge index
    until the workload demands it.
    """

    def __init__(self, *, graph_store: GraphStore) -> None:
        self._graph_store = graph_store

    def explain(self, relation_id: str) -> RelationEvidence:
        edge = self._graph_store.find_edge_by_id(relation_id)
        if edge is None:
            raise RelationNotFound(f"Relation {relation_id!r} not found.")
        return _project_edge(edge)

    def list_bridged_documents(self, *, document_id: str) -> list[str]:
        """Return every other document id sharing a chunk-level
        boundary edge with ``document_id`` (#385).

        Thin adapter over
        :meth:`GraphStore.find_document_ids_with_boundary_edges_to`,
        used by the document_relations cache warm path. Excludes
        ``document_id`` itself; the underlying store sorts the result
        for deterministic test ordering.
        """
        return self._graph_store.find_document_ids_with_boundary_edges_to(document_id)

    def explain_aggregate(
        self,
        *,
        source_document_id: str,
        target_document_id: str,
        top_n: int = 10,
    ) -> AggregatedRelationEvidence:
        """Synthesise a doc-doc evidence payload from contributing
        chunk-level edges.

        Walks the source document's projected subgraph, filters edges
        that touch a chunk owned by the target document, scores each
        contributor via #314, and returns the top N.

        Raises :class:`RelationNotFound` when the documents have no
        contributing chunk-level edges between them — the frontend
        renders that as "no detectable relationship" rather than an
        empty panel.
        """
        if top_n < 1:
            raise ValueError(f"top_n must be >= 1, got {top_n}")

        # ``find_subgraph_for_document`` filters edges to within the
        # document's own node set, so cross-doc chunk-relation edges
        # need a dedicated graph-store helper. Filter to aggregatable
        # kinds — structural edges and topic-membership don't explain
        # a doc-doc relation.
        cross_edges = self._graph_store.find_edges_between_documents(
            source_document_id=source_document_id,
            target_document_id=target_document_id,
        )
        candidate_edges: dict[str, GraphEdge] = {
            edge.id: edge for edge in cross_edges if edge.kind in _AGGREGATABLE_KINDS
        }

        if not candidate_edges:
            raise RelationNotFound(
                f"No contributing edges found between {source_document_id!r} "
                f"and {target_document_id!r}."
            )

        # Score each contributing pair with the #314 policy.
        scored_pairs: list[ContributingChunkPair] = []
        for edge in candidate_edges.values():
            if edge.kind in _DETERMINISTIC_KINDS:
                evidence = _project_deterministic_edge(edge)
                if (  # pragma: no cover - deterministic projector always scores
                    evidence.score is None or evidence.strength_class is None
                ):
                    continue
                scored_pairs.append(
                    ContributingChunkPair(
                        relation_id=evidence.relation_id,
                        kind=evidence.kind,
                        source_chunk_id=evidence.source_id,
                        target_chunk_id=evidence.target_id,
                        score=evidence.score,
                        strength_class=evidence.strength_class,
                        reason=evidence.reason or "",
                        shared_keywords=list(evidence.shared_keywords),
                    )
                )
            elif edge.kind in _LLM_KINDS:
                # ``has_entity`` edges connect entity nodes, not chunks
                # directly — they're aggregated less cleanly. Surface
                # them with confidence-as-score for visual ordering and
                # an empty shared_keywords; the frontend can branch on
                # ``kind == "has_entity"`` to render differently.
                props = edge.properties
                confidence = _coerce_float(props.get("confidence"), default=0.0)
                scored_pairs.append(
                    ContributingChunkPair(
                        relation_id=edge.id,
                        kind=edge.kind,
                        source_chunk_id=edge.source_id,
                        target_chunk_id=edge.target_id,
                        score=confidence,
                        strength_class=classify_strength(confidence).value,
                        reason=str(props.get("predicate") or ""),
                        shared_keywords=[],
                    )
                )

        if not scored_pairs:  # pragma: no cover - candidate_edges non-empty guarantees scoreable
            raise RelationNotFound(
                f"No scoreable edges between {source_document_id!r} and {target_document_id!r}."
            )

        # Aggregate-score policy: max of contributing scores. Mean
        # would dilute one strong bridge in a sea of weak overlaps;
        # max surfaces "at least one chunk pair is THIS strong."
        aggregate_score = max(p.score for p in scored_pairs)
        # The aggregate is a bridge / outlier when at least one
        # contributing pair is — conservative interpretation.
        is_bridge = any(
            _project_deterministic_edge(candidate_edges[p.relation_id]).is_bridge
            for p in scored_pairs
            if p.kind in _DETERMINISTIC_KINDS
        )
        is_outlier = any(
            _project_deterministic_edge(candidate_edges[p.relation_id]).is_outlier
            for p in scored_pairs
            if p.kind in _DETERMINISTIC_KINDS
        )

        # Deterministic ranking via #314. Truncate to top_n; the
        # frontend's "+ N more" indicator uses ``pair_count`` to show
        # the un-truncated total.
        ranked = _rank_pairs(scored_pairs)
        top_pairs = ranked[:top_n]

        return AggregatedRelationEvidence(
            source_document_id=source_document_id,
            target_document_id=target_document_id,
            aggregate_score=aggregate_score,
            pair_count=len(scored_pairs),
            top_contributing_pairs=top_pairs,
            is_bridge=is_bridge,
            is_outlier=is_outlier,
        )


def _rank_pairs(pairs: list[ContributingChunkPair]) -> list[ContributingChunkPair]:
    """Adapter — the #314 ``rank_edges`` takes ``ScoredEdge`` shapes,
    but we have ``ContributingChunkPair``. Re-wrap so we stay on the
    same deterministic ordering policy (score desc, edge_id asc)
    rather than re-implementing tie-breaking here.
    """
    from app.services.knowledge.scoring import ScoredEdge

    proxies = [
        ScoredEdge(
            edge_id=p.relation_id,
            score=p.score,
            # Round-trip the Literal back into the enum that
            # :class:`ScoredEdge` expects. ``StrengthClass`` is a
            # ``StrEnum`` so its constructor accepts the wire-string
            # value directly.
            strength_class=StrengthClass(p.strength_class),
            is_bridge=False,
            is_outlier=False,
        )
        for p in pairs
    ]
    ordered = rank_edges(proxies, by="strength")
    by_id = {p.relation_id: p for p in pairs}
    return [by_id[scored.edge_id] for scored in ordered]


__all__ = [
    "KnowledgeRelationsService",
    "RelationNotFound",
]


# Avoid a circular import warning: StrengthClass is re-exported above
# but only used in type-hints inside this module; keep the import at
# top level for editor / lint friendliness.
_ = StrengthClass
