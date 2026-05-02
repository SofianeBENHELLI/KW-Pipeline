"""Tests for the deterministic chunk relation service (#141).

Covers the four relation kinds the demo cares about (shared-keyword,
near-duplicate, same-topic, shared-standard) plus the unrelated
control case, plus the determinism property the lane-C smoke
assertions rely on.

No fixtures or graph store needed — the service is pure functions
over ``SemanticSection``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticDocument,
    SemanticSection,
)
from app.services.knowledge.chunk_relations import (
    ChunkRelation,
    ChunkRelationService,
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


def _relations(service: ChunkRelationService, doc: SemanticDocument) -> list[ChunkRelation]:
    return service.relations_for(service.chunks_for(doc))


def test_unrelated_chunks_emit_no_edges():
    service = ChunkRelationService()
    doc = _semantic(
        SemanticSection(
            id="s1",
            heading="Cooking",
            text=(
                "Boil pasta in salted water for eight minutes. Drain and "
                "serve with olive oil, parmesan cheese, and freshly ground "
                "black pepper."
            ),
        ),
        SemanticSection(
            id="s2",
            heading="Astronomy",
            text=(
                "Jupiter orbits the sun every twelve years. Its largest "
                "moons were observed by Galileo in sixteen ten using a "
                "rudimentary telescope."
            ),
        ),
    )

    assert _relations(service, doc) == []


def test_shares_keyword_when_one_meaningful_token_overlaps():
    service = ChunkRelationService()
    doc = _semantic(
        SemanticSection(
            id="s1",
            heading="Boilers",
            text="Pressure boiler vessels require annual inspection records.",
        ),
        SemanticSection(
            id="s2",
            heading="Trains",
            text="Locomotive boiler maintenance follows a rotating schedule.",
        ),
    )

    relations = _relations(service, doc)

    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "shares_keyword"
    assert rel.source_chunk_id == "s1"
    assert rel.target_chunk_id == "s2"
    assert "boiler" in rel.shared_keywords
    assert rel.reason
    assert 0.0 < rel.score <= 1.0


def test_same_topic_when_multiple_keywords_overlap():
    service = ChunkRelationService()
    # Two paragraphs about quality audits — distinct phrasing, several
    # shared content words, well above the same-topic threshold.
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
    )

    relations = _relations(service, doc)

    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "same_topic_as"
    assert rel.source_chunk_id == "alpha"
    assert rel.target_chunk_id == "beta"
    assert len(rel.shared_keywords) >= 3
    assert "supplier" in rel.shared_keywords
    assert "audit" in rel.shared_keywords


def test_near_duplicate_chunks_emit_related_to():
    service = ChunkRelationService()
    base = (
        "The pressure relief valve must be tested annually by a qualified "
        "inspector and the result recorded in the equipment logbook."
    )
    doc = _semantic(
        SemanticSection(id="s1", heading="Section A", text=base),
        SemanticSection(
            id="s2",
            heading="Section B",
            text=base + " Document the inspector signature.",
        ),
    )

    relations = _relations(service, doc)

    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "related_to"
    assert rel.score >= 0.8
    assert rel.shared_keywords  # contract: never empty
    assert "near-duplicate" in rel.reason.lower()


def test_shared_standard_emits_shares_keyword_with_standard_in_reason():
    service = ChunkRelationService()
    doc = _semantic(
        SemanticSection(
            id="s1",
            heading="Quality Management",
            text=(
                "The supplier maintains a quality management system that "
                "conforms to ISO 9001 and welds critical assemblies under "
                "controlled conditions."
            ),
        ),
        SemanticSection(
            id="s2",
            heading="Subcontractor Controls",
            text=(
                "Subcontractor selection requires evidence of an "
                "ISO 9001 certified quality system and documented "
                "fabrication procedures."
            ),
        ),
    )

    relations = _relations(service, doc)

    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "shares_keyword"
    assert "ISO 9001" in rel.reason
    # The canonicalised standard token shows up in the audit list.
    assert "iso-9001" in rel.shared_keywords


def test_output_is_deterministic_across_runs():
    service_a = ChunkRelationService()
    service_b = ChunkRelationService()
    doc = _semantic(
        SemanticSection(
            id="s3",
            heading="Three",
            text="Audit programmes review supplier deliverables and supplier evidence.",
        ),
        SemanticSection(
            id="s1",
            heading="One",
            text="Audit findings classify supplier performance gaps and corrective actions.",
        ),
        SemanticSection(
            id="s2",
            heading="Two",
            text="Boiler inspections require pressure vessel certificates filed annually.",
        ),
    )

    first = _relations(service_a, doc)
    second = _relations(service_b, doc)

    assert [r.model_dump() for r in first] == [r.model_dump() for r in second]
    # Output sorted by (source, target, kind) — verify the sort key holds.
    keys = [(r.source_chunk_id, r.target_chunk_id, r.kind) for r in first]
    assert keys == sorted(keys)


def test_empty_document_yields_no_relations():
    service = ChunkRelationService()
    doc = _semantic()

    assert service.chunks_for(doc) == []
    assert service.relations_for(service.chunks_for(doc)) == []


def test_single_section_yields_no_relations():
    service = ChunkRelationService()
    doc = _semantic(
        SemanticSection(id="solo", heading="Lonely", text="Lonely paragraph here."),
    )

    chunks = service.chunks_for(doc)
    assert len(chunks) == 1
    assert service.relations_for(chunks) == []


def test_canonical_pair_ordering_independent_of_input_order():
    service = ChunkRelationService()
    text_a = "Quality audits review supplier evidence and supplier deliverables every quarter."
    text_b = "Supplier evidence is collected by audit teams during quarterly supplier reviews."

    forward = _semantic(
        SemanticSection(id="zeta", heading="Z", text=text_a),
        SemanticSection(id="alpha", heading="A", text=text_b),
    )
    backward = _semantic(
        SemanticSection(id="alpha", heading="A", text=text_b),
        SemanticSection(id="zeta", heading="Z", text=text_a),
    )

    forward_rel = _relations(service, forward)
    backward_rel = _relations(service, backward)

    assert len(forward_rel) == 1
    assert forward_rel[0].source_chunk_id == "alpha"
    assert forward_rel[0].target_chunk_id == "zeta"
    assert [r.model_dump() for r in forward_rel] == [r.model_dump() for r in backward_rel]


def test_shared_keywords_never_empty_on_emitted_edges():
    """Lane C's smoke assertion contract — every deterministic edge
    must carry at least one shared keyword as its audit trail."""
    service = ChunkRelationService()
    doc = _semantic(
        SemanticSection(
            id="s1",
            heading="A",
            text="Pressure vessels follow ISO 9001 quality manuals during fabrication.",
        ),
        SemanticSection(
            id="s2",
            heading="B",
            text="Welding qualification under ISO 9001 controls supplier deliverables.",
        ),
        SemanticSection(
            id="s3",
            heading="C",
            text="Supplier audit evidence supports quality manuals across ISO 9001 programmes.",
        ),
    )

    relations = _relations(service, doc)

    assert relations  # sanity — these chunks should produce at least one edge
    for rel in relations:
        assert rel.shared_keywords, f"relation {rel} has empty shared_keywords"
        assert rel.reason
        assert 0.0 <= rel.score <= 1.0
