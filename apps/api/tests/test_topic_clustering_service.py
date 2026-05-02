"""Tests for the deterministic ``TopicClusteringService`` (#142).

Coverage targets from the issue:

- multi-chunk clusters merge correctly via connected components;
- singleton chunks are handled (default: each gets its own topic);
- ``topic_id``s are stable — running ``cluster()`` twice on the same
  input yields identical ids;
- output records match :class:`TopicNodeProperties` field-for-field;
- a hand-built input produces ≥ 3 topics (the lane-C demo fixtures
  drive the demo path; this test is hermetic).
"""

from __future__ import annotations

from app.schemas.knowledge import TopicNodeProperties
from app.schemas.semantic_document import SemanticSection
from app.services.knowledge.chunk_relation_service import ChunkRelationService
from app.services.knowledge.topic_clustering_service import (
    TopicClusteringConfig,
    TopicClusteringService,
)


def _section(section_id: str, text: str) -> SemanticSection:
    return SemanticSection(id=section_id, heading=section_id, text=text)


# ---------------------------------------------------------------------------
# Helpers — build the relations from sections so each clustering test
# exercises the full #141 + #142 pipeline. Tests that want to inject
# synthetic relations construct them directly below.
# ---------------------------------------------------------------------------


def _cluster(
    sections: list[SemanticSection],
    *,
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    relation_service: ChunkRelationService | None = None,
    clustering: TopicClusteringService | None = None,
):
    relation_service = relation_service or ChunkRelationService()
    clustering = clustering or TopicClusteringService(relation_service=relation_service)
    relations = relation_service.extract_relations(
        sections, document_id=document_id, version_id=version_id
    )
    return clustering.cluster(
        sections,
        relations,
        document_id=document_id,
        version_id=version_id,
    )


# ---------------------------------------------------------------------------
# Multi-chunk cluster
# ---------------------------------------------------------------------------


def test_related_chunks_merge_into_one_topic():
    sections = [
        _section(
            "a",
            "Vendor contract SLA payment refund policy compliance review quarterly.",
        ),
        _section(
            "b",
            "Vendor contract SLA refund policy compliance audit review quarterly.",
        ),
        _section(
            "c",
            "Cherry blossoms bloom along the riverbank in spring.",
        ),
    ]

    result = _cluster(sections)

    # ``a`` and ``b`` overlap heavily → same topic; ``c`` is a singleton.
    topic_a = result.chunk_to_topic["a"]
    topic_b = result.chunk_to_topic["b"]
    topic_c = result.chunk_to_topic["c"]
    assert topic_a == topic_b
    assert topic_c != topic_a

    # All chunks accounted for (singletons get their own topic by default).
    assert set(result.chunk_to_topic) == {"a", "b", "c"}
    assert len(result.topics) == 2

    merged = next(t for t in result.topics if t.topic_id == topic_a)
    assert isinstance(merged, TopicNodeProperties)
    assert sorted(merged.chunk_ids) == ["a", "b"]
    assert merged.chunk_count == 2
    assert merged.label  # non-empty
    assert merged.keywords  # non-empty


# ---------------------------------------------------------------------------
# Singleton handling
# ---------------------------------------------------------------------------


def test_singletons_get_their_own_topic_by_default():
    sections = [
        _section("solo-1", "Aardvark migration patterns are poorly studied."),
        _section("solo-2", "Quantum entanglement violates classical intuition."),
    ]
    result = _cluster(sections)
    # Two unrelated chunks → two singleton topics.
    assert len(result.topics) == 2
    assert set(result.chunk_to_topic.keys()) == {"solo-1", "solo-2"}
    # Different topic_ids.
    assert result.chunk_to_topic["solo-1"] != result.chunk_to_topic["solo-2"]


def test_include_singletons_false_drops_solo_chunks_from_map():
    sections = [
        _section("alone", "Aardvark migration patterns are poorly studied."),
        _section(
            "pair-a",
            "Vendor contract SLA refund policy compliance audit quarterly.",
        ),
        _section(
            "pair-b",
            "Vendor contract SLA refund policy compliance audit quarterly clauses.",
        ),
    ]
    clustering = TopicClusteringService(TopicClusteringConfig(include_singletons=False))
    result = _cluster(sections, clustering=clustering)
    assert "alone" not in result.chunk_to_topic
    assert {"pair-a", "pair-b"} <= set(result.chunk_to_topic)
    assert len(result.topics) == 1


# ---------------------------------------------------------------------------
# Stable labels / topic_ids
# ---------------------------------------------------------------------------


def test_topic_ids_are_stable_across_runs():
    sections = [
        _section(
            "a",
            "Vendor contract SLA payment refund policy compliance review quarterly.",
        ),
        _section(
            "b",
            "Vendor contract SLA refund policy compliance audit review quarterly.",
        ),
        _section(
            "c",
            "Cherry blossoms bloom along the riverbank in spring petals fall.",
        ),
    ]

    first = _cluster(sections)
    second = _cluster(sections)

    assert [t.topic_id for t in first.topics] == [t.topic_id for t in second.topics]
    assert first.chunk_to_topic == second.chunk_to_topic


def test_topic_id_is_independent_of_section_input_order():
    sections = [
        _section(
            "a",
            "Vendor contract SLA payment refund policy compliance review quarterly.",
        ),
        _section(
            "b",
            "Vendor contract SLA refund policy compliance audit review quarterly.",
        ),
    ]
    forward = _cluster(sections)
    backward = _cluster(list(reversed(sections)))
    assert forward.chunk_to_topic == backward.chunk_to_topic
    assert [t.topic_id for t in forward.topics] == [t.topic_id for t in backward.topics]


def test_topic_id_format_matches_contract():
    sections = [
        _section(
            "a",
            "Vendor contract SLA refund policy compliance audit quarterly review filings.",
        ),
        _section(
            "b",
            "Vendor contract SLA refund policy compliance audit quarterly review filings.",
        ),
    ]
    result = _cluster(sections)
    assert result.topics
    topic_id = result.topics[0].topic_id
    # ``topic-{16 hex chars}`` — see _topic_id() in the service.
    assert topic_id.startswith("topic-")
    suffix = topic_id.removeprefix("topic-")
    assert len(suffix) == 16
    assert all(ch in "0123456789abcdef" for ch in suffix)


# ---------------------------------------------------------------------------
# At least 3 topics from a constructed input (acceptance criterion)
# ---------------------------------------------------------------------------


def test_constructed_input_produces_at_least_three_topics():
    """The issue requires the demo hero fixtures to produce ≥ 3 topics,
    but lane C owns the fixtures. This test uses a hand-built input
    that exercises the same property."""
    sections = [
        # Cluster 1: contracts / vendor / SLA.
        _section(
            "contract-a",
            "Vendor contract SLA payment refund policy compliance audit quarterly.",
        ),
        _section(
            "contract-b",
            "Vendor contract SLA refund policy compliance audit review quarterly clauses.",
        ),
        # Cluster 2: ML / training / evaluation.
        _section(
            "ml-a",
            "Model training requires careful evaluation harness setup and metric tracking.",
        ),
        _section(
            "ml-b",
            "Evaluation harness drives model training metrics across calibration runs.",
        ),
        # Cluster 3: gardening (singleton — still counts as one topic).
        _section(
            "garden",
            "Tulips prefer well-drained soil and full morning sun in temperate gardens.",
        ),
    ]

    result = _cluster(sections)
    assert len(result.topics) >= 3, [t.label for t in result.topics]
    # Every chunk is assigned to a topic when include_singletons is True.
    assert set(result.chunk_to_topic.keys()) == {s.id for s in sections}


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_empty_sections_returns_empty_result():
    clustering = TopicClusteringService()
    result = clustering.cluster([], [])
    assert result.topics == []
    assert result.chunk_to_topic == {}


def test_relation_referencing_unknown_chunk_is_ignored():
    """Defensive: if a relation references a chunk not in ``sections``
    the service must log + skip rather than crash."""
    from app.schemas.knowledge import ChunkRelationEdgeProperties

    sections = [
        _section("only-known", "Anything reasonable here."),
    ]
    bogus = ChunkRelationEdgeProperties(
        document_id="d",
        version_id="v",
        source_chunk_id="only-known",
        target_chunk_id="ghost",
        score=0.9,
        reason="should be ignored",
        shared_keywords=["x"],
    )
    clustering = TopicClusteringService()
    result = clustering.cluster([sections[0]], [bogus])
    # Single chunk → one (singleton) topic.
    assert len(result.topics) == 1
    assert result.chunk_to_topic == {"only-known": result.topics[0].topic_id}


def test_topic_record_shape_matches_typed_property_model():
    sections = [
        _section(
            "a",
            "Vendor contract SLA refund policy compliance audit quarterly clauses review.",
        ),
        _section(
            "b",
            "Vendor contract SLA refund policy compliance audit quarterly clauses review.",
        ),
    ]
    result = _cluster(sections)
    assert result.topics
    topic = result.topics[0]
    flat = topic.model_dump()
    assert set(flat.keys()) >= {
        "document_id",
        "version_id",
        "topic_id",
        "label",
        "keywords",
        "summary",
        "chunk_count",
        "chunk_ids",
    }
    assert isinstance(flat["keywords"], list)
    assert isinstance(flat["chunk_ids"], list)
    assert flat["chunk_count"] == len(flat["chunk_ids"])
