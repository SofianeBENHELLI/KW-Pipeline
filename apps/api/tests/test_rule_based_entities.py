"""Tests for the rule-based entity enricher (#48).

Covers the three deterministic entity types this PR ships —
``date``, ``monetary_amount``, ``requirement_phrase`` — with a hit,
a miss, and an ambiguous case per the issue's acceptance criteria.
Person and organization NER (spaCy-backed) is out of scope here and
will land as an additional enricher in a follow-up.
"""

from __future__ import annotations

from app.schemas.extraction import RawExtraction, RawSection, SourceReference
from app.services.enrichers import (
    NoOpEnricher,
    RuleBasedEntityEnricher,
    SemanticEnricher,
)

# ─── Fixture builder ──────────────────────────────────────────────────


def _raw_extraction(text: str, section_id: str = "s-0") -> RawExtraction:
    """One-section RawExtraction with a single source reference, useful
    for asserting on per-section enrichment."""
    ref = SourceReference(
        document_version_id="ver-test",
        section_id=section_id,
        page_number=1,
        line_start=None,
        line_end=None,
        snippet=text[:240],
    )
    return RawExtraction(
        document_version_id="ver-test",
        parser_name="plain_text",
        parser_version="1",
        text=text,
        sections=[
            RawSection(
                id=section_id,
                heading="Body",
                text=text,
                source_reference_ids=[ref.id],
                parser_metadata={},
            )
        ],
        source_references=[ref],
    )


def _types(assets) -> list[str]:
    return [a.type for a in assets]


def _texts(assets) -> list[str]:
    return [a.text for a in assets]


# ─── Protocol + wiring ────────────────────────────────────────────────


class TestProtocolConformance:
    def test_rule_based_enricher_conforms_to_semantic_enricher(self) -> None:
        enricher = RuleBasedEntityEnricher()
        assert isinstance(enricher, SemanticEnricher)
        assert enricher.name == "rule_based_entities"

    def test_noop_enricher_still_conforms(self) -> None:
        # Sanity: the existing NoOpEnricher and the new one share the
        # same Protocol surface.
        assert isinstance(NoOpEnricher(), SemanticEnricher)


# ─── date ─────────────────────────────────────────────────────────────


class TestDateExtraction:
    def test_iso_date_is_picked_up(self) -> None:
        extraction = _raw_extraction("Effective from 2026-05-03 onward.")
        assets = RuleBasedEntityEnricher().enrich(extraction, [])
        dates = [a for a in assets if a.type == "date"]
        assert _texts(dates) == ["2026-05-03"]
        assert dates[0].confidence == 0.9
        assert dates[0].review_status == "needs_review"
        # Lineage flows through from the section.
        assert dates[0].source_reference_ids == extraction.sections[0].source_reference_ids

    def test_long_form_english_date(self) -> None:
        extraction = _raw_extraction("Signed on May 3, 2026 in Paris.")
        assets = RuleBasedEntityEnricher().enrich(extraction, [])
        dates = [a for a in assets if a.type == "date"]
        assert any("May 3, 2026" in a.text for a in dates)

    def test_european_day_first_format(self) -> None:
        extraction = _raw_extraction("Renewal scheduled for 3 May 2026.")
        assets = RuleBasedEntityEnricher().enrich(extraction, [])
        dates = [a for a in assets if a.type == "date"]
        assert any("3 May 2026" in a.text for a in dates)

    def test_no_dates_in_pure_prose(self) -> None:
        extraction = _raw_extraction("This document outlines the supplier policy.")
        assets = RuleBasedEntityEnricher().enrich(extraction, [])
        assert all(a.type != "date" for a in assets)

    def test_duplicate_date_emitted_only_once(self) -> None:
        """Two identical dates in the same section should yield one asset.

        Audit greppers join on (type, text); a second duplicate row would
        just inflate counts without adding signal.
        """
        extraction = _raw_extraction("Effective 2026-01-01. Re-effective 2026-01-01 as well.")
        assets = RuleBasedEntityEnricher().enrich(extraction, [])
        dates = [a for a in assets if a.type == "date"]
        assert _texts(dates) == ["2026-01-01"]


# ─── monetary_amount ──────────────────────────────────────────────────


class TestMonetaryExtraction:
    def test_euro_amount_with_comma_thousands(self) -> None:
        extraction = _raw_extraction("Contract above €42,000 requires dual approval.")
        assets = RuleBasedEntityEnricher().enrich(extraction, [])
        money = [a for a in assets if a.type == "monetary_amount"]
        assert any("42,000" in a.text for a in money)
        assert all(a.confidence == 0.9 for a in money)

    def test_iso_currency_code(self) -> None:
        extraction = _raw_extraction("Penalty of USD 1,234.56 applies.")
        assets = RuleBasedEntityEnricher().enrich(extraction, [])
        money = [a for a in assets if a.type == "monetary_amount"]
        assert any("1,234.56" in a.text for a in money)

    def test_no_match_when_currency_marker_absent(self) -> None:
        # "100 employees" looks like a number but has no currency marker.
        extraction = _raw_extraction("The company has 100 employees and 25 offices.")
        assets = RuleBasedEntityEnricher().enrich(extraction, [])
        assert all(a.type != "monetary_amount" for a in assets)

    def test_lineage_attached(self) -> None:
        extraction = _raw_extraction("Approve when below $50.")
        money = [
            a
            for a in RuleBasedEntityEnricher().enrich(extraction, [])
            if a.type == "monetary_amount"
        ]
        assert money
        assert money[0].source_reference_ids == extraction.sections[0].source_reference_ids


# ─── requirement_phrase ───────────────────────────────────────────────


class TestRequirementPhrase:
    def test_must_clause_is_extracted(self) -> None:
        extraction = _raw_extraction("Suppliers must be evaluated annually.")
        assets = RuleBasedEntityEnricher().enrich(extraction, [])
        reqs = [a for a in assets if a.type == "requirement_phrase"]
        assert reqs, "expected at least one requirement_phrase asset"
        assert "must" in reqs[0].text.lower()
        # Lower confidence because the heuristic over-matches.
        assert reqs[0].confidence == 0.5

    def test_shall_clause_is_extracted(self) -> None:
        extraction = _raw_extraction("The reviewer shall sign within 5 business days.")
        reqs = [
            a
            for a in RuleBasedEntityEnricher().enrich(extraction, [])
            if a.type == "requirement_phrase"
        ]
        assert reqs
        assert "shall" in reqs[0].text.lower()

    def test_prose_without_modal_verb_yields_no_requirement(self) -> None:
        extraction = _raw_extraction("Procurement is the act of buying goods or services.")
        assets = RuleBasedEntityEnricher().enrich(extraction, [])
        assert all(a.type != "requirement_phrase" for a in assets)

    def test_ambiguous_must_in_unrelated_position_still_matches(self) -> None:
        """The regex catches ``must`` in any clause — call sites must
        treat ``requirement_phrase`` as a *candidate* and let the
        reviewer dismiss false positives. Pin the behaviour here so a
        future tightening (e.g. requiring sentence-initial position)
        is an explicit policy change."""
        extraction = _raw_extraction("It must have been a long meeting.")
        reqs = [
            a
            for a in RuleBasedEntityEnricher().enrich(extraction, [])
            if a.type == "requirement_phrase"
        ]
        assert reqs


# ─── End-to-end shape ────────────────────────────────────────────────


class TestEndToEndShape:
    def test_mixed_section_emits_all_three_types(self) -> None:
        extraction = _raw_extraction(
            "Effective 2026-05-03, suppliers must submit an annual review. "
            "Penalties above €42,000 require dual approval."
        )
        assets = RuleBasedEntityEnricher().enrich(extraction, [])
        kinds = sorted(set(_types(assets)))
        assert kinds == ["date", "monetary_amount", "requirement_phrase"]
        # Every asset carries the section's lineage.
        for asset in assets:
            assert asset.source_reference_ids == extraction.sections[0].source_reference_ids
            assert asset.review_status == "needs_review"

    def test_empty_section_yields_no_assets(self) -> None:
        extraction = _raw_extraction("")
        assets = RuleBasedEntityEnricher().enrich(extraction, [])
        assert assets == []

    def test_existing_assets_argument_is_ignored(self) -> None:
        """The Protocol passes a list of prior assets; this enricher
        produces additional ones independent of what was already
        extracted (per ADR-009 / SemanticEnricher contract)."""
        extraction = _raw_extraction("Renewal on 2026-06-01.")
        bare = RuleBasedEntityEnricher().enrich(extraction, [])
        # Pretending the upstream chain produced 3 prior assets — the
        # output must be the same because we only consume RawExtraction.
        with_priors = RuleBasedEntityEnricher().enrich(extraction, list(bare))
        assert _types(bare) == _types(with_priors)
        assert _texts(bare) == _texts(with_priors)
