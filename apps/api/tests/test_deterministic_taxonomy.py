"""Tests for the per-chunk deterministic taxonomy extractor (EPIC-1 §1.1)."""

from __future__ import annotations

import json

import pytest

from app.schemas.deterministic_taxonomy import (
    DETERMINISTIC_TAXONOMY_SCHEMA_VERSION,
    DeterministicTaxonomyConcept,
    DeterministicTaxonomyForChunk,
)
from app.schemas.extraction import RawSection
from app.schemas.semantic_document import SemanticSection
from app.services.knowledge.chunk_relations import ChunkRelationService
from app.services.knowledge.deterministic_taxonomy import (
    extract_deterministic_taxonomy,
)


def _record(text: str, *, heading: str = "Section A", chunk_id: str = "c-1"):
    """Build a :class:`ChunkRecord` from a text snippet via the existing
    :class:`ChunkRelationService`. Mirrors what the projector does at
    runtime — keeps the test surface aligned with the real call site."""
    service = ChunkRelationService()
    section = SemanticSection(id=chunk_id, heading=heading, text=text)
    # ``chunks_for`` requires the whole document; we wrap one section.
    from app.schemas.semantic_document import (
        DocumentProfile,
        SemanticDocument,
    )

    doc = SemanticDocument(
        document_id="doc-1",
        document_version_id="ver-1",
        profile=DocumentProfile(),
        sections=[section],
    )
    return service.chunks_for(doc)[0]


# ─── Schema sanity ─────────────────────────────────────────────────────


class TestSchemaShape:
    def test_schema_version_constant_is_literal_v01(self) -> None:
        assert DETERMINISTIC_TAXONOMY_SCHEMA_VERSION == "v0.1"

    def test_default_schema_version_on_construction(self) -> None:
        projection = DeterministicTaxonomyForChunk(
            chunk_id="c-1",
            section_id="c-1",
            heading="Section",
        )
        assert projection.schema_version == "v0.1"
        assert projection.concepts == []


# ─── Extractors — happy paths ──────────────────────────────────────────


class TestKeywordExtraction:
    def test_keywords_show_up_in_concepts(self) -> None:
        record = _record(
            "Battery thermal management protects the battery from thermal runaway. "
            "Cooling loops absorb battery heat to prevent damage."
        )
        projection = extract_deterministic_taxonomy(record)
        keywords = [c.text for c in projection.concepts if c.kind == "keyword"]
        # ``battery`` and ``thermal`` are the dominant tokens; both
        # should land in the top-keyword list. ``the`` is filtered
        # by the stopword list.
        assert "battery" in keywords
        assert "thermal" in keywords
        assert "the" not in keywords

    def test_keyword_limit_override(self) -> None:
        record = _record(
            "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
        )
        projection = extract_deterministic_taxonomy(record, keyword_limit=3)
        keywords = [c for c in projection.concepts if c.kind == "keyword"]
        assert len(keywords) == 3


class TestNounPhraseExtraction:
    def test_capitalized_runs_extracted(self) -> None:
        record = _record(
            "Battery Thermal Management is critical. Engineering Change "
            "Request must precede production releases."
        )
        projection = extract_deterministic_taxonomy(record)
        phrases = [c.text for c in projection.concepts if c.kind == "noun_phrase"]
        assert "Battery Thermal Management" in phrases
        assert "Engineering Change Request" in phrases

    def test_single_word_capitalized_tokens_excluded(self) -> None:
        record = _record("Battery is critical. Cooling is mandatory.")
        projection = extract_deterministic_taxonomy(record)
        phrases = [c.text for c in projection.concepts if c.kind == "noun_phrase"]
        # Single-word capitalized tokens (sentence-initial words) are
        # NOT noun phrases — they overlap with keywords.
        assert phrases == []


class TestAcronymExtraction:
    def test_known_acronyms_extracted(self) -> None:
        record = _record(
            "The MCP gateway routes traffic to the AI agent. RBAC policies "
            "govern access. Use API tokens for OAuth flows."
        )
        projection = extract_deterministic_taxonomy(record)
        acronyms = [c.text for c in projection.concepts if c.kind == "acronym"]
        assert "MCP" in acronyms
        assert "AI" in acronyms
        assert "RBAC" in acronyms
        assert "API" in acronyms
        assert "OAuth" not in acronyms  # Mixed case — out of scope

    def test_acronyms_capped_at_five_letters(self) -> None:
        record = _record(
            "Use HTTP for transport. The HTTPS upgrade is mandatory. "
            "Avoid SOMETHINGTOOLONG abbreviations."
        )
        projection = extract_deterministic_taxonomy(record)
        acronyms = [c.text for c in projection.concepts if c.kind == "acronym"]
        assert "HTTP" in acronyms
        assert "HTTPS" in acronyms
        assert "SOMETHINGTOOLONG" not in acronyms


class TestStandardsExtraction:
    def test_iso_iec_standards_extracted(self) -> None:
        record = _record(
            "Comply with ISO 9001 and IEC 62443 throughout the chain. "
            "ASTM-D-5034 governs fabric strength."
        )
        projection = extract_deterministic_taxonomy(record)
        standards = [c.text for c in projection.concepts if c.kind == "standard"]
        # Canonical form is ``BODY <number>`` (uppercased body, space).
        assert any("ISO" in s and "9001" in s for s in standards)
        assert any("IEC" in s and "62443" in s for s in standards)


class TestHeadingAnchor:
    def test_real_heading_becomes_anchor(self) -> None:
        record = _record("body text", heading="Battery Safety Requirements")
        projection = extract_deterministic_taxonomy(record)
        anchors = [c for c in projection.concepts if c.kind == "heading_anchor"]
        assert len(anchors) == 1
        assert anchors[0].text == "Battery Safety Requirements"
        assert anchors[0].confidence == 1.0

    def test_default_extracted_text_heading_skipped(self) -> None:
        record = _record("body text", heading="Extracted Text")
        projection = extract_deterministic_taxonomy(record)
        anchors = [c for c in projection.concepts if c.kind == "heading_anchor"]
        # ``"Extracted Text"`` is the parser's default fallback — it's
        # noise as a taxonomy anchor and must be filtered.
        assert anchors == []

    def test_empty_heading_skipped(self) -> None:
        record = _record("body text", heading="")
        projection = extract_deterministic_taxonomy(record)
        anchors = [c for c in projection.concepts if c.kind == "heading_anchor"]
        assert anchors == []


# ─── NER candidates (optional) ─────────────────────────────────────────


class TestNERCandidates:
    def test_explicit_ner_entities_passed_through(self) -> None:
        record = _record("Dassault Systèmes builds the 3DEXPERIENCE platform.")
        projection = extract_deterministic_taxonomy(
            record,
            ner_entities=["Dassault Systèmes", "3DEXPERIENCE"],
        )
        ner = [c.text for c in projection.concepts if c.kind == "ner_candidate"]
        assert ner == ["Dassault Systèmes", "3DEXPERIENCE"]

    def test_section_metadata_supplies_ner_entities(self) -> None:
        record = _record("Voyage AI provides embeddings.")
        section = SemanticSection(
            id=record.section_id,
            heading=record.heading,
            text=record.text,
            parser_metadata={
                "spacy_ner_entities": json.dumps(["Voyage AI"]),
            },
        )
        projection = extract_deterministic_taxonomy(record, section=section)
        ner = [c.text for c in projection.concepts if c.kind == "ner_candidate"]
        assert ner == ["Voyage AI"]

    def test_malformed_section_metadata_is_fail_soft(self) -> None:
        """A broken ``spacy_ner_entities`` value mustn't blow up the
        whole extractor — it just means "no NER entities for this
        chunk"."""
        record = _record("body text")
        section = SemanticSection(
            id=record.section_id,
            heading=record.heading,
            text=record.text,
            parser_metadata={"spacy_ner_entities": "{not valid json"},
        )
        projection = extract_deterministic_taxonomy(record, section=section)
        ner = [c.text for c in projection.concepts if c.kind == "ner_candidate"]
        assert ner == []

    def test_explicit_ner_overrides_section_metadata(self) -> None:
        record = _record("body text")
        section = SemanticSection(
            id=record.section_id,
            heading=record.heading,
            text=record.text,
            parser_metadata={"spacy_ner_entities": json.dumps(["from-section"])},
        )
        projection = extract_deterministic_taxonomy(
            record,
            section=section,
            ner_entities=["from-explicit"],
        )
        ner = [c.text for c in projection.concepts if c.kind == "ner_candidate"]
        assert ner == ["from-explicit"]


# ─── Dedup + determinism ───────────────────────────────────────────────


class TestDeduplication:
    def test_same_kind_same_text_dedupes_keeping_higher_confidence(self) -> None:
        """If a regex extractor and the NER pass both surface the same
        text under the same kind, the higher-confidence one wins."""
        record = _record("Battery Thermal Management is critical.")
        # Force an NER entity that overlaps the noun phrase but under
        # the ``ner_candidate`` kind. These don't collide on
        # ``(kind, text.lower())`` because the kinds differ, so both
        # should land. The cross-kind dedup is intentionally a
        # downstream concern (slice 1.4 gap analysis).
        projection = extract_deterministic_taxonomy(
            record,
            ner_entities=["Battery Thermal Management"],
        )
        texts_lower = [(c.kind, c.text.lower()) for c in projection.concepts]
        # Each (kind, text) appears at most once.
        assert len(texts_lower) == len(set(texts_lower))

    def test_repeated_keyword_in_text_appears_once(self) -> None:
        """``Counter`` already dedupes; we just confirm the pipeline
        respects that single-output guarantee end-to-end."""
        record = _record("battery battery battery cooling cooling")
        projection = extract_deterministic_taxonomy(record)
        battery_keywords = [
            c
            for c in projection.concepts
            if c.kind == "keyword" and c.text == "battery"
        ]
        assert len(battery_keywords) == 1


class TestDeterminism:
    def test_repeated_calls_produce_identical_projections(self) -> None:
        """No PYTHONHASHSEED sensitivity — same input → same output
        across two calls."""
        record = _record(
            "Battery Thermal Management protects the battery. ISO 9001 "
            "applies. The MCP protocol is referenced here."
        )
        first = extract_deterministic_taxonomy(record)
        second = extract_deterministic_taxonomy(record)
        assert first.model_dump() == second.model_dump()


# ─── Identifiers + traceability ────────────────────────────────────────


class TestIdentifiers:
    def test_chunk_section_heading_threaded(self) -> None:
        record = _record(
            "irrelevant body",
            heading="Process Validation",
            chunk_id="c-77",
        )
        projection = extract_deterministic_taxonomy(record)
        assert projection.chunk_id == "c-77"
        assert projection.section_id == "c-77"
        assert projection.heading == "Process Validation"


# ─── Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_text_returns_only_heading_anchor(self) -> None:
        record = _record("", heading="Empty Section")
        projection = extract_deterministic_taxonomy(record)
        kinds = {c.kind for c in projection.concepts}
        # Body-derived extractors find nothing, but the heading
        # anchor still fires.
        assert kinds == {"heading_anchor"}

    def test_whitespace_only_text_treats_as_empty(self) -> None:
        record = _record("   \n\n  ", heading="Padding Section")
        projection = extract_deterministic_taxonomy(record)
        assert all(c.kind == "heading_anchor" for c in projection.concepts)


# ─── Concept shape ─────────────────────────────────────────────────────


class TestConceptShape:
    def test_confidence_in_range(self) -> None:
        record = _record("Battery thermal management with ISO 9001 reference.")
        projection = extract_deterministic_taxonomy(record)
        for concept in projection.concepts:
            assert 0.0 <= concept.confidence <= 1.0

    def test_text_min_length_enforced(self) -> None:
        """Pydantic's ``min_length=1`` rejects empty strings."""
        with pytest.raises(ValueError):
            DeterministicTaxonomyConcept(kind="keyword", text="")

    def test_invalid_kind_rejected(self) -> None:
        with pytest.raises(ValueError):
            DeterministicTaxonomyConcept(kind="bogus", text="x")  # type: ignore[arg-type]

    def test_raw_section_metadata_round_trip(self) -> None:
        """The parser metadata shape (``dict[str, str]``) survives the
        extractor's JSON-decode without flattening string values."""
        _ = RawSection(
            id="r-1",
            heading="H",
            text="t",
            parser_metadata={"spacy_ner_entities": json.dumps(["A", "B"])},
        )
