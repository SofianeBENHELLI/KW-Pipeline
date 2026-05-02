"""Tests for the deterministic topic clustering service (#142)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticDocument,
    SemanticSection,
)
from app.services.knowledge.chunk_relations import ChunkRelationService
from app.services.knowledge.topic_clustering import (
    TopicAssignment,
    TopicClusteringService,
)


def _semantic(*sections: SemanticSection) -> SemanticDocument:
    return SemanticDocument(
        id="sem-test",
        document_version_id="ver-1",
        document_profile=DocumentProfile(title="Test"),
        sections=list(sections),
        validation_status="validated",
        markdown="# Test\n",
        created_at=datetime(2026, 5, 2, tzinfo=UTC),
    )


def _run(doc: SemanticDocument) -> TopicAssignment:
    relations_service = ChunkRelationService()
    chunks = relations_service.chunks_for(doc)
    relations = relations_service.relations_for(chunks)
    return TopicClusteringService().cluster(chunks, relations)


def test_singleton_chunks_produce_no_topics():
    doc = _semantic(
        SemanticSection(
            id="s1",
            heading="Cooking",
            text="Boil pasta in salted water for eight minutes and drain.",
        ),
        SemanticSection(
            id="s2",
            heading="Astronomy",
            text="Jupiter orbits the sun every twelve years.",
        ),
    )

    result = _run(doc)

    assert result.topics == []
    assert result.memberships == []


def test_multi_chunk_cluster_produces_one_topic():
    doc = _semantic(
        SemanticSection(
            id="alpha",
            heading="Audit Plan",
            text=(
                "Quality audit programmes evaluate supplier performance. "
                "The audit team reviews supplier records, supplier "
                "deliverables, and supplier corrective actions during "
                "each programme cycle."
            ),
        ),
        SemanticSection(
            id="beta",
            heading="Audit Findings",
            text=(
                "Audit findings categorise supplier performance gaps. "
                "Each supplier programme tracks corrective actions to "
                "closure; the audit team verifies supplier evidence "
                "against programme deliverables."
            ),
        ),
        SemanticSection(
            id="gamma",
            heading="Audit Closure",
            text=(
                "Audit closure requires supplier corrective actions to "
                "be approved by the audit team. Supplier programme "
                "deliverables and supplier evidence are filed."
            ),
        ),
    )

    result = _run(doc)

    assert len(result.topics) == 1
    topic = result.topics[0]
    assert sorted(topic.chunk_ids) == ["alpha", "beta", "gamma"]
    assert topic.label
    # Top keywords should reflect the shared vocabulary.
    assert "supplier" in topic.keywords
    assert "audit" in topic.keywords
    assert topic.summary and "Cluster of 3" in topic.summary

    assert {m.chunk_id for m in result.memberships} == {"alpha", "beta", "gamma"}
    assert {m.topic_id for m in result.memberships} == {topic.topic_id}
    for membership in result.memberships:
        assert membership.score == 1.0


def test_two_independent_clusters_get_distinct_topics():
    audit_text_a = (
        "Quality audit programmes evaluate supplier performance. "
        "The audit team reviews supplier records and supplier "
        "deliverables every quarter."
    )
    audit_text_b = (
        "Audit findings categorise supplier performance gaps. "
        "Each supplier programme tracks supplier corrective "
        "actions to closure."
    )
    boiler_text_a = (
        "Pressure relief valves on boilers must be tested annually "
        "by a qualified inspector and the result recorded in the "
        "boiler logbook."
    )
    boiler_text_b = (
        "Boiler logbook entries certify that pressure relief valves "
        "were tested annually and the qualified inspector recorded "
        "the result."
    )

    doc = _semantic(
        SemanticSection(id="audit-a", heading="A", text=audit_text_a),
        SemanticSection(id="audit-b", heading="B", text=audit_text_b),
        SemanticSection(id="boiler-a", heading="C", text=boiler_text_a),
        SemanticSection(id="boiler-b", heading="D", text=boiler_text_b),
    )

    result = _run(doc)

    assert len(result.topics) == 2
    topic_chunks = {tuple(sorted(t.chunk_ids)) for t in result.topics}
    assert ("audit-a", "audit-b") in topic_chunks
    assert ("boiler-a", "boiler-b") in topic_chunks

    # Topic ids are distinct.
    topic_ids = {t.topic_id for t in result.topics}
    assert len(topic_ids) == 2


def test_topic_id_stable_across_runs():
    doc = _semantic(
        SemanticSection(
            id="s1",
            heading="One",
            text=(
                "Quality audit programmes evaluate supplier performance "
                "and supplier corrective actions during each cycle."
            ),
        ),
        SemanticSection(
            id="s2",
            heading="Two",
            text=(
                "Supplier corrective actions are reviewed by the audit "
                "team during each quality programme cycle."
            ),
        ),
    )

    first = _run(doc)
    second = _run(doc)

    assert [t.model_dump() for t in first.topics] == [t.model_dump() for t in second.topics]
    assert [m.model_dump() for m in first.memberships] == [
        m.model_dump() for m in second.memberships
    ]


def test_topic_id_independent_of_input_order():
    text_a = (
        "Quality audit programmes evaluate supplier performance. "
        "The audit team reviews supplier records, supplier "
        "deliverables, and supplier corrective actions during the "
        "programme cycle."
    )
    text_b = (
        "Audit findings categorise supplier performance gaps. "
        "Each supplier programme tracks corrective actions to "
        "closure; the audit team verifies supplier evidence "
        "against programme deliverables."
    )

    forward = _semantic(
        SemanticSection(id="alpha", heading="A", text=text_a),
        SemanticSection(id="beta", heading="B", text=text_b),
    )
    backward = _semantic(
        SemanticSection(id="beta", heading="B", text=text_b),
        SemanticSection(id="alpha", heading="A", text=text_a),
    )

    forward_result = _run(forward)
    backward_result = _run(backward)

    assert len(forward_result.topics) == 1
    assert len(backward_result.topics) == 1
    assert forward_result.topics[0].topic_id == backward_result.topics[0].topic_id


def test_empty_document_returns_empty_assignment():
    result = _run(_semantic())

    assert result.topics == []
    assert result.memberships == []


def test_label_capitalises_top_keywords():
    doc = _semantic(
        SemanticSection(
            id="s1",
            heading="One",
            text=(
                "Quality audit programmes evaluate supplier performance "
                "and supplier corrective actions during each cycle."
            ),
        ),
        SemanticSection(
            id="s2",
            heading="Two",
            text=(
                "Supplier corrective actions are reviewed by the audit "
                "team during each quality programme cycle."
            ),
        ),
    )

    result = _run(doc)
    topic = result.topics[0]

    # Label is "Capitalised · Word" using the top two cluster keywords.
    assert " · " in topic.label
    parts = topic.label.split(" · ")
    assert all(part[0].isupper() for part in parts)


def test_summary_truncates_to_budget():
    long_text = ("Quality audit supplier programme " * 50).strip()
    doc = _semantic(
        SemanticSection(id="s1", heading="One", text=long_text),
        SemanticSection(id="s2", heading="Two", text=long_text + " extra."),
    )

    result = _run(doc)
    assert len(result.topics) == 1
    summary = result.topics[0].summary
    assert summary is not None
    assert len(summary) <= 200


def test_topics_sorted_by_topic_id():
    """Two clusters → topics list comes back sorted by ``topic_id``."""
    doc = _semantic(
        SemanticSection(
            id="audit-a",
            heading="A",
            text=(
                "Quality audit programmes evaluate supplier performance "
                "and supplier corrective actions during each cycle."
            ),
        ),
        SemanticSection(
            id="audit-b",
            heading="B",
            text=(
                "Audit findings categorise supplier performance gaps. "
                "Each supplier programme tracks corrective actions."
            ),
        ),
        SemanticSection(
            id="boiler-a",
            heading="C",
            text=(
                "Pressure relief valves on boilers must be tested "
                "annually by a qualified inspector and the result "
                "recorded in the boiler logbook."
            ),
        ),
        SemanticSection(
            id="boiler-b",
            heading="D",
            text=(
                "Boiler logbook entries certify that pressure relief "
                "valves were tested annually and the qualified "
                "inspector recorded the result."
            ),
        ),
    )

    result = _run(doc)
    ids = [t.topic_id for t in result.topics]
    assert ids == sorted(ids)
