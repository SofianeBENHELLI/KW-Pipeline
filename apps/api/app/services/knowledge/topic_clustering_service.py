"""Deterministic topic clustering over chunk relations (#142).

Consumes the output of :class:`ChunkRelationService` (#141) and groups
chunks into topics via connected-components on a similarity graph.

The service is intentionally simple:

- **No LLM, no Anthropic key**, no Neo4j driver — pure stdlib. Same
  hard rule as the relation service.
- **Deterministic.** Running ``cluster()`` twice on the same input
  produces the same ``topic_id``s, byte-for-byte. The id formula is
  ``topic-{sha256(sorted(chunk_ids)::label)[:16]}`` per the contract
  doc; the label is the highest-scoring shared keyword across the
  cluster, broken on ties by lexicographic order.
- **Singleton-safe.** Chunks with no qualifying edges are emitted as
  their own topic by default — that keeps the chunk → topic map total,
  so the projector (#143/#144) can rely on every chunk having a
  ``belongs_to`` edge. Set ``include_singletons=False`` if a caller
  wants to skip them; the chunk → topic map then has fewer entries.

Output shape mirrors :class:`~app.schemas.knowledge.TopicNodeProperties`
field-for-field. The projector flattens via ``.model_dump()`` to fill
``GraphNode.properties`` for each ``kind == "topic"`` node.
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from app.schemas.knowledge import ChunkRelationEdgeProperties, TopicNodeProperties
from app.schemas.semantic_document import SemanticSection
from app.services.knowledge.chunk_relation_service import ChunkRelationService

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TopicClusteringConfig:
    """Tunable thresholds for the clustering service.

    The cluster graph is built from relations whose ``score`` clears
    :attr:`min_relation_score`. Pick a higher threshold to bias toward
    smaller, tighter topics; a lower one to bias toward fewer, broader
    ones. The default 0.1 is permissive and matches the
    :class:`ChunkRelationService` defaults — anything that emitted a
    ``shares_keyword`` edge contributes to clustering.
    """

    # Minimum relation ``score`` to count as a clustering edge. Below
    # this, the relation is "noise" — present in the graph for the
    # inspector but not strong enough to merge components.
    min_relation_score: float = 0.1
    # Whether singletons get their own topic. ``True`` keeps the chunk
    # → topic map total; ``False`` lets the projector treat unclustered
    # chunks as topic-less.
    include_singletons: bool = True
    # How many keywords to surface on the topic record. Top-N by
    # frequency across member chunks.
    top_n_keywords: int = 12


@dataclass(frozen=True)
class TopicClusteringResult:
    """Output of one clustering pass.

    Carries the topic records (typed property shape, ready to flatten
    into ``GraphNode.properties``) plus the chunk → topic id map used
    to build ``belongs_to`` edges in the projector.
    """

    topics: list[TopicNodeProperties] = field(default_factory=list)
    chunk_to_topic: dict[str, str] = field(default_factory=dict)


class TopicClusteringService:
    """Connected-components clustering over deterministic chunk relations.

    Stateless; the optional :class:`ChunkRelationService` reference is
    used only as a fallback keyword extractor when the caller needs the
    same tokenization as the relation service. Tests that want a
    different keyword pipeline can swap it out.
    """

    def __init__(
        self,
        config: TopicClusteringConfig | None = None,
        relation_service: ChunkRelationService | None = None,
    ) -> None:
        self._config = config or TopicClusteringConfig()
        # Defer instantiation so the tokenization defaults stay aligned
        # with the relation service even when callers don't pass one.
        self._relation_service = relation_service or ChunkRelationService()

    @property
    def config(self) -> TopicClusteringConfig:
        return self._config

    def cluster(
        self,
        sections: list[SemanticSection],
        relations: list[ChunkRelationEdgeProperties],
        *,
        document_id: str = "",
        version_id: str = "",
    ) -> TopicClusteringResult:
        """Group chunks into topics via connected components.

        Args:
            sections: validated semantic sections (chunks 1:1 today).
                The order is irrelevant — internal sorting makes the
                output deterministic.
            relations: deterministic chunk relations from #141. The
                clustering uses any relation whose ``score`` clears
                :attr:`TopicClusteringConfig.min_relation_score`.
            document_id: passed through to each emitted topic record.
                The clustering service has no way to derive it; the
                projector / test supplies it. Defaults to ``""``.
            version_id: see ``document_id``.

        Returns:
            :class:`TopicClusteringResult` carrying topics (in stable
            order — sorted by ``topic_id``) and the total chunk → topic
            map. Re-running on the same input returns identical
            ``topic_id``s.
        """
        if not sections:
            return TopicClusteringResult()

        cfg = self._config

        # Sorted by id to make every downstream loop deterministic.
        sections_sorted = sorted(sections, key=lambda s: s.id)
        chunk_ids = [s.id for s in sections_sorted]
        section_by_id: dict[str, SemanticSection] = {s.id: s for s in sections_sorted}

        # Compute keyword sets once. We intentionally reuse the relation
        # service's tokenizer so topic labels and chunk-relation
        # ``shared_keywords`` agree.
        keyword_lists: dict[str, list[str]] = {
            cid: self._relation_service.keywords_for(section_by_id[cid]) for cid in chunk_ids
        }

        # ----------- union-find over qualifying relations ----------- #

        parent: dict[str, str] = {cid: cid for cid in chunk_ids}

        def find(x: str) -> str:
            # Iterative path compression — recursive form blows the
            # stack on long chains and we'd rather not enable
            # ``sys.setrecursionlimit`` for a clustering helper.
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            # Stable tie-break: smaller id wins root, so the canonical
            # representative depends only on the chunk-id alphabet, not
            # iteration order.
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

        for rel in sorted(relations, key=lambda r: (r.source_chunk_id, r.target_chunk_id)):
            if rel.score < cfg.min_relation_score:
                continue
            # Only merge over chunks we actually saw. A relation that
            # references a chunk not in ``sections`` is a caller bug —
            # log and skip rather than crash.
            if rel.source_chunk_id not in parent or rel.target_chunk_id not in parent:
                log.warning(
                    "knowledge.topic_clustering.unknown_chunk",
                    extra={
                        "source": rel.source_chunk_id,
                        "target": rel.target_chunk_id,
                    },
                )
                continue
            union(rel.source_chunk_id, rel.target_chunk_id)

        # ----------- group by component root ----------- #

        components: dict[str, list[str]] = defaultdict(list)
        for cid in chunk_ids:
            components[find(cid)].append(cid)

        # ----------- materialise topics ----------- #

        topics: list[TopicNodeProperties] = []
        chunk_to_topic: dict[str, str] = {}

        for member_ids in components.values():
            if not cfg.include_singletons and len(member_ids) < 2:
                # Skip singletons when the caller asked us to. Their
                # chunks won't appear in ``chunk_to_topic``; the
                # projector treats absence as "no belongs_to edge".
                continue

            members_sorted = sorted(member_ids)
            label, ranked_keywords = self._label_and_keywords(
                member_ids=members_sorted,
                keyword_lists=keyword_lists,
            )
            topic_id = _topic_id(members_sorted, label)
            topic = TopicNodeProperties(
                document_id=document_id,
                version_id=version_id,
                topic_id=topic_id,
                label=label,
                keywords=ranked_keywords[: cfg.top_n_keywords],
                summary=None,  # left for a future LLM-summarisation pass
                chunk_count=len(members_sorted),
                chunk_ids=members_sorted,
            )
            topics.append(topic)
            for cid in members_sorted:
                chunk_to_topic[cid] = topic_id

        # Stable output order — sorted by topic_id. Tests check this.
        topics.sort(key=lambda t: t.topic_id)

        log.debug(
            "knowledge.topic_clustering.clustered",
            extra={
                "document_id": document_id,
                "version_id": version_id,
                "chunk_count": len(chunk_ids),
                "topic_count": len(topics),
                "relation_count": len(relations),
            },
        )

        return TopicClusteringResult(topics=topics, chunk_to_topic=chunk_to_topic)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _label_and_keywords(
        self,
        *,
        member_ids: list[str],
        keyword_lists: dict[str, list[str]],
    ) -> tuple[str, list[str]]:
        """Pick a human-readable label + ranked keyword list for one topic.

        Frequency = number of member chunks that include the keyword
        (not raw occurrence count). That biases towards keywords shared
        across the cluster, which is what the inspector wants — a topic
        whose label only appears in one chunk would feel wrong.

        Tie-break for the label: alphabetic. The result is deterministic.
        Singleton clusters fall back to the chunk's first keyword, then
        the chunk id (so labels are never empty).
        """
        per_keyword_chunks: Counter[str] = Counter()
        for cid in member_ids:
            for kw in set(keyword_lists.get(cid, [])):
                per_keyword_chunks[kw] += 1

        if not per_keyword_chunks:
            # No keywords at all — extremely short text. Fall back to a
            # synthetic label so ``topic_id`` derivation stays
            # deterministic.
            return (f"topic-{member_ids[0]}", [])

        # Sort by (-count, keyword) for stable, frequency-first order.
        ordered = sorted(per_keyword_chunks.items(), key=lambda kv: (-kv[1], kv[0]))
        ranked_keywords = [kw for kw, _ in ordered]
        label = ranked_keywords[0]
        return (label, ranked_keywords)


# ---------------------------------------------------------------------------
# Deterministic topic id
# ---------------------------------------------------------------------------


def _topic_id(sorted_chunk_ids: list[str], label: str) -> str:
    """Stable id derived from ``sha256(sorted(chunk_ids)::label)[:16]``.

    Per the contract doc's open-question note (#142 owns the formula).
    Including the label means renaming a topic regenerates its id —
    which is correct: the topic identity *is* its members + the
    keyword that named them. If reviewers later prefer pure-membership
    ids, dropping the label suffix is a one-line change here.
    """
    payload = "::".join(sorted_chunk_ids) + "::" + label
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"topic-{digest}"


__all__ = [
    "TopicClusteringConfig",
    "TopicClusteringResult",
    "TopicClusteringService",
]
