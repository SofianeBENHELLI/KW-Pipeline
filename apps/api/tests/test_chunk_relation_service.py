"""Tests for the deterministic ``ChunkRelationService`` (#141).

Coverage targets from the issue:

- shared-keyword example produces ``shares_keyword`` with the right
  audit trail;
- near-duplicate example produces a high-score ``related_to`` whose
  reason calls out the duplication;
- same-topic-style example produces both ``shares_keyword`` and
  ``related_to`` (the service intentionally defers ``same_topic_as``
  to the clustering service — documented in module docstring);
- unrelated example yields no edges;
- the service is deterministic across runs and across input order.
"""

from __future__ import annotations

from app.schemas.knowledge import ChunkRelationEdgeProperties
from app.schemas.semantic_document import SemanticSection
from app.services.knowledge.chunk_relation_service import (
    ChunkRelationConfig,
    ChunkRelationService,
)


def _section(section_id: str, text: str) -> SemanticSection:
    return SemanticSection(id=section_id, heading=section_id, text=text)


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------


def test_keywords_for_drops_stopwords_and_short_tokens():
    service = ChunkRelationService()
    section = _section(
        "c1",
        "The vendor must deliver the contract on time. We shall pay the vendor.",
    )
    keywords = service.keywords_for(section)
    # Stop words (the, must, on, we, shall) and the short token "pay"
    # are dropped; "vendor" stems through unchanged; plurals merge.
    assert "vendor" in keywords
    assert "contract" in keywords
    assert "deliver" in keywords
    assert "the" not in keywords
    assert "we" not in keywords


def test_keywords_for_is_deterministic_across_runs():
    service = ChunkRelationService()
    section = _section("c1", "Apples and oranges. Apples again. Bananas. Oranges.")
    one = service.keywords_for(section)
    two = service.keywords_for(section)
    assert one == two
    # Frequency wins ordering. The two-occurrence stems come first;
    # tie-break is alphabetic. The stemmer is deliberately crude so we
    # assert on the count rather than the surface form ("apples" stems
    # via the trailing-"es" rule, not perfect Porter stemming).
    assert len(one) == 3
    assert one[2] == "banana"  # one-occurrence singleton trails the pair.


# ---------------------------------------------------------------------------
# Shared-keyword case
# ---------------------------------------------------------------------------


def test_shared_keyword_emits_shares_keyword_with_audit_trail():
    service = ChunkRelationService()
    sections = [
        _section(
            "chunk-a",
            "The vendor signed the contract for payment processing on the SLA.",
        ),
        _section(
            "chunk-b",
            "Payment vendors must follow the contract SLA when processing refunds.",
        ),
    ]

    relations = service.extract_relations(sections, document_id="doc-1", version_id="ver-1")

    # At minimum we should see a shares_keyword-style record.
    assert relations, "expected at least one relation between overlapping chunks"
    rec = relations[0]
    assert isinstance(rec, ChunkRelationEdgeProperties)
    assert rec.document_id == "doc-1"
    assert rec.version_id == "ver-1"
    assert {rec.source_chunk_id, rec.target_chunk_id} == {"chunk-a", "chunk-b"}
    # Audit trail mandated by the contract doc:
    assert rec.shared_keywords, "shared_keywords must be non-empty"
    assert rec.reason, "reason must be non-empty"
    assert "vendor" in rec.shared_keywords or "contract" in rec.shared_keywords
    assert 0.0 <= rec.score <= 1.0


def test_below_shared_keyword_floor_emits_nothing():
    # Only one shared keyword ("contract") — under the default floor of 2.
    service = ChunkRelationService()
    sections = [
        _section("a", "The contract describes deliverables for engineering."),
        _section("b", "The contract handles plumbing and HVAC inspections."),
    ]
    relations = service.extract_relations(sections)
    # Single overlap is below the shared_keyword_min — and Jaccard is
    # well below the related_to floor — so nothing should fire.
    assert relations == []


# ---------------------------------------------------------------------------
# Near-duplicate case
# ---------------------------------------------------------------------------


def test_near_duplicate_emits_related_to_with_near_duplicate_reason():
    service = ChunkRelationService()
    text = (
        "The compliance officer reviews quarterly reports and ensures "
        "regulatory filings stay aligned with policy deadlines."
    )
    sections = [
        _section("dup-a", text),
        _section("dup-b", text + " Quarterly review extra clause."),
    ]

    relations = service.extract_relations(sections)

    # We expect both a shares_keyword-style entry (the lighter signal)
    # and a related_to entry whose reason flags it as near-duplicate.
    assert len(relations) >= 2
    near_dup = [r for r in relations if "near-duplicate" in r.reason]
    reasons = [r.reason for r in relations]
    assert near_dup, f"expected a near-duplicate reason, got: {reasons}"
    rec = near_dup[0]
    # Score should be very high — Jaccard of an almost-identical pair.
    assert rec.score >= 0.85
    assert rec.shared_keywords
    assert {rec.source_chunk_id, rec.target_chunk_id} == {"dup-a", "dup-b"}


# ---------------------------------------------------------------------------
# "Same topic" / high overlap (still a related_to under v0.2 — see the
# service docstring for why same_topic_as is deferred).
# ---------------------------------------------------------------------------


def test_high_jaccard_emits_related_to_high_overlap_reason():
    service = ChunkRelationService(
        ChunkRelationConfig(
            top_n_keywords=20,
            min_keyword_length=3,
            shared_keyword_min=2,
            related_to_jaccard_min=0.4,
            near_duplicate_jaccard_min=0.95,
        )
    )
    sections = [
        _section(
            "s1",
            "Quarterly compliance reviews cover regulatory filings and audit deadlines.",
        ),
        _section(
            "s2",
            "Compliance reviews examine regulatory filings, audit deadlines, and policy drift.",
        ),
    ]

    relations = service.extract_relations(sections)

    high_overlap = [r for r in relations if "high keyword overlap" in r.reason]
    assert high_overlap, [r.reason for r in relations]
    assert all("near-duplicate" not in r.reason for r in high_overlap)


# ---------------------------------------------------------------------------
# Unrelated case
# ---------------------------------------------------------------------------


def test_unrelated_chunks_emit_no_relations():
    service = ChunkRelationService()
    sections = [
        _section("alpha", "Cherry blossoms bloom along quiet riverbanks each spring."),
        _section("beta", "Distributed databases benefit from quorum-based replication."),
    ]
    relations = service.extract_relations(sections)
    assert relations == []


def test_single_section_returns_empty():
    service = ChunkRelationService()
    relations = service.extract_relations(
        [_section("only", "anything goes here, but there is no pair to relate")]
    )
    assert relations == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_extract_relations_is_deterministic_across_input_order():
    service = ChunkRelationService()
    sections = [
        _section(
            "c1",
            "Vendors must deliver software on the agreed contract SLA every quarter.",
        ),
        _section(
            "c2",
            "Software contracts include vendor penalties when SLA quarters slip.",
        ),
        _section(
            "c3",
            "Cherry blossoms bloom along the riverbank in spring.",
        ),
    ]

    forward = service.extract_relations(sections, document_id="d", version_id="v")
    reversed_input = list(reversed(sections))
    backward = service.extract_relations(reversed_input, document_id="d", version_id="v")
    # Same records, same order. Relation service sorts internally so
    # reversing the caller's section order must not change output.
    assert forward == backward


def test_relations_are_typed_property_records_ready_for_projector():
    """The contract requires the records flatten via ``model_dump()``
    into the keys the projector needs."""
    service = ChunkRelationService()
    sections = [
        _section(
            "a",
            "vendor contract SLA payment refund policy compliance review.",
        ),
        _section(
            "b",
            "vendor contract refund policy compliance audit review payment.",
        ),
    ]
    rels = service.extract_relations(sections, document_id="doc", version_id="ver")
    assert rels
    flat = rels[0].model_dump()
    assert set(flat.keys()) >= {
        "document_id",
        "version_id",
        "source_chunk_id",
        "target_chunk_id",
        "score",
        "reason",
        "shared_keywords",
    }
    assert isinstance(flat["shared_keywords"], list)
    assert isinstance(flat["score"], float)
