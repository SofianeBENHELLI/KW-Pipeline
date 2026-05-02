"""Deterministic topic clustering over chunks (Demo KG #142).

Consumes the output of :class:`ChunkRelationService` and groups related
chunks into topics by walking the relation graph: any two chunks
linked by a strong same-topic signal end up in the same topic. We then
emit one :class:`Topic` per connected component of size ≥ 2 —
singletons stay un-clustered, which keeps the graph readable in the
Orbital inspector (per the lane-D handshake in
``docs/architecture/knowledge_graph_payload.md``).

We treat ``related_to`` and ``same_topic_as`` as unconditional cluster
edges; ``shares_keyword`` only counts when the pair shares at least
``_MIN_SHARED_KEYWORDS_FOR_CLUSTERING`` substantive words, so a single
common term ("system", "process") doesn't collapse the whole graph
into one topic.

Determinism is enforced at three levels:

* Connected components are enumerated in chunk-id order.
* ``topic_id`` is a SHA-256 hash of the sorted member chunk ids — same
  cluster, same id, across re-projections (the open question in the
  contract doc was "stable across re-projections of the same input"
  and this delivers it).
* Topic ``label``, ``keywords``, and ``summary`` derive from the
  member chunks in deterministic-frequency order (ties broken
  alphabetically), so a re-run produces the same wire bytes.

The service has no LLM dependency. Anthropic-driven topic labeling
could replace the keyword-heuristic in a later phase, but the demo
constraint (#142 acceptance criterion: "customer demo hero fixtures
produce at least 3 topics") is met with the deterministic path alone.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel
from app.services.knowledge.chunk_relations import (
    ChunkRecord,
    ChunkRelation,
)

# Tunables — module-level so lane C fixture authors and tests can
# reason about clustering output without rereading the algorithm.
_MAX_LABEL_KEYWORDS = 2
_MAX_TOPIC_KEYWORDS = 10
_SUMMARY_CHAR_BUDGET = 200
_TOPIC_ID_PREFIX = "topic-"
_TOPIC_ID_HASH_LENGTH = 16
# How many shared keywords an edge needs to count as a "same-topic
# signal" for clustering. ``same_topic_as`` and ``related_to`` are
# already strong signals (the relation service only emits them for
# substantial overlap), so they cluster unconditionally.
# ``shares_keyword`` is weaker — a single common word ("system",
# "process") is not enough; require at least two shared content words
# before two chunks coalesce into a topic.
_MIN_SHARED_KEYWORDS_FOR_CLUSTERING = 2

# Edge kinds that count as a same-topic signal. ``has_entity`` and the
# structural kinds are explicitly excluded.
_TOPIC_EDGE_KINDS: frozenset[Literal["related_to", "shares_keyword", "same_topic_as"]] = frozenset(
    {"related_to", "shares_keyword", "same_topic_as"}
)


class Topic(BaseModel):
    """One topic cluster ready for projection.

    Field order matches :class:`TopicNodeProperties` minus the
    ``document_id`` / ``version_id`` that lane A's projector adds at
    construction time.
    """

    topic_id: str
    label: str
    keywords: list[str]
    summary: str | None
    chunk_ids: list[str]


class TopicMembership(BaseModel):
    """One chunk → topic assignment, populating ``belongs_to`` edge
    properties (:class:`TopicMembershipEdgeProperties`).

    ``score`` is always ``1.0`` for the deterministic hard-cluster
    algorithm here; the field exists for forward compatibility with a
    future soft-cluster variant (per the contract doc's "consumers
    MUST treat missing ``score`` as 1.0" note).
    """

    chunk_id: str
    topic_id: str
    score: float = Field(default=1.0, ge=0.0, le=1.0)


class TopicAssignment(BaseModel):
    """Bundle of clusters and chunk-membership records."""

    topics: list[Topic]
    memberships: list[TopicMembership]


class TopicClusteringService:
    """Stateless. Construct once per projection — no caches, no
    accumulated state between calls.
    """

    def cluster(
        self,
        chunks: Sequence[ChunkRecord],
        relations: Sequence[ChunkRelation],
    ) -> TopicAssignment:
        """Group ``chunks`` into :class:`Topic` clusters using
        ``relations`` as the similarity graph.

        Singletons are not promoted to topics — the result's
        ``memberships`` only includes chunks that ended up in a
        multi-chunk cluster.
        """
        if not chunks:
            return TopicAssignment(topics=[], memberships=[])

        chunk_index = {chunk.chunk_id: chunk for chunk in chunks}
        components = _connected_components(
            chunk_ids=[chunk.chunk_id for chunk in chunks],
            relations=relations,
        )

        topics: list[Topic] = []
        memberships: list[TopicMembership] = []
        for component in components:
            if len(component) < 2:
                continue
            members = [chunk_index[chunk_id] for chunk_id in component]
            topic = _build_topic(members)
            topics.append(topic)
            memberships.extend(
                TopicMembership(chunk_id=chunk_id, topic_id=topic.topic_id)
                for chunk_id in topic.chunk_ids
            )

        # Sort outputs for byte-stability across runs. Topics by id;
        # memberships by (chunk_id, topic_id) — chunk-major ordering
        # matches what lane A's projector will iterate.
        topics.sort(key=lambda t: t.topic_id)
        memberships.sort(key=lambda m: (m.chunk_id, m.topic_id))
        return TopicAssignment(topics=topics, memberships=memberships)


def _connected_components(
    *,
    chunk_ids: Sequence[str],
    relations: Iterable[ChunkRelation],
) -> list[list[str]]:
    """Union-find over the relation graph, returning components in
    chunk-id order. Components themselves are sorted internally so the
    membership list and the topic-id hash are deterministic.
    """
    parent: dict[str, str] = {chunk_id: chunk_id for chunk_id in chunk_ids}

    def find(node: str) -> str:
        # Path compression — keeps repeated finds O(α).
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        # Smaller id wins to keep canonical roots stable.
        if ra < rb:
            parent[rb] = ra
        else:
            parent[ra] = rb

    for relation in relations:
        if relation.kind not in _TOPIC_EDGE_KINDS:
            continue
        if (
            relation.kind == "shares_keyword"
            and len(relation.shared_keywords) < _MIN_SHARED_KEYWORDS_FOR_CLUSTERING
        ):
            continue
        if relation.source_chunk_id not in parent:
            continue
        if relation.target_chunk_id not in parent:
            continue
        union(relation.source_chunk_id, relation.target_chunk_id)

    grouped: dict[str, list[str]] = {}
    for chunk_id in chunk_ids:
        grouped.setdefault(find(chunk_id), []).append(chunk_id)
    components = [sorted(members) for members in grouped.values()]
    components.sort(key=lambda members: members[0])
    return components


def _build_topic(members: Sequence[ChunkRecord]) -> Topic:
    chunk_ids = sorted(chunk.chunk_id for chunk in members)
    topic_id = _make_topic_id(chunk_ids)
    keywords = _aggregate_keywords(members)
    label = _make_label(keywords, members)
    summary = _make_summary(members, keywords)
    return Topic(
        topic_id=topic_id,
        label=label,
        keywords=keywords,
        summary=summary,
        chunk_ids=chunk_ids,
    )


def _aggregate_keywords(members: Sequence[ChunkRecord]) -> list[str]:
    """Top-N keywords across the cluster by frequency, ties broken
    alphabetically. Each member contributes its keyword list once
    (no double-counting if a keyword appears multiple times within
    a single chunk — that's already handled by the relation service's
    Counter).
    """
    counts: Counter[str] = Counter()
    for chunk in members:
        counts.update(set(chunk.keywords))
    if not counts:
        return []
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [token for token, _ in ranked[:_MAX_TOPIC_KEYWORDS]]


def _make_label(keywords: Sequence[str], members: Sequence[ChunkRecord]) -> str:
    """Render a human-readable label.

    Falls back to the first member's heading when the keyword set is
    empty (e.g. tiny stub chunks); a label is required by lane D's
    inspector and an empty string would render badly.
    """
    if keywords:
        return " · ".join(keyword.capitalize() for keyword in keywords[:_MAX_LABEL_KEYWORDS])
    if members and members[0].heading:
        return members[0].heading
    return f"Cluster of {len(members)} chunks"


def _make_summary(members: Sequence[ChunkRecord], keywords: Sequence[str]) -> str | None:
    """One-sentence-ish description of the cluster.

    Combines the cluster size with the leading keywords so the summary
    is non-empty even when every chunk is short. Returns ``None`` only
    when there's literally nothing to describe (no members, which
    :meth:`cluster` already filters out — kept defensive).
    """
    if not members:
        return None
    keyword_phrase = ", ".join(keywords[:_MAX_LABEL_KEYWORDS]) if keywords else ""
    base = f"Cluster of {len(members)} related chunks"
    if keyword_phrase:
        base = f"{base} discussing {keyword_phrase}"
    base = f"{base}."
    # Splice in a preview from the first member to give the inspector
    # something concrete to render. Truncate hard at the budget so wire
    # payloads stay small.
    preview = (members[0].text or "").strip().replace("\n", " ")
    if preview:
        remaining = _SUMMARY_CHAR_BUDGET - len(base) - 1
        if remaining > 20:
            if len(preview) > remaining:
                preview = preview[: remaining - 1].rstrip() + "…"
            base = f"{base} {preview}"
    return base


def _make_topic_id(chunk_ids: Sequence[str]) -> str:
    """Hash the sorted member ids into a stable topic id.

    The contract doc suggested
    ``topic-{sha256(sorted(chunk_ids)::label)[:16]}``; we drop the
    label term from the hash input so renaming a topic later (e.g.
    when richer keywords arrive) doesn't shift the id and orphan
    references in saved Orbital state.
    """
    digest = hashlib.sha256("\n".join(sorted(chunk_ids)).encode("utf-8")).hexdigest()
    return f"{_TOPIC_ID_PREFIX}{digest[:_TOPIC_ID_HASH_LENGTH]}"
