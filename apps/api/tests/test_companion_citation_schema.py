"""Schema-shape tests for the AURA companion citation contract (#370 / ADR-029).

The companion route doesn't exist yet — this PR ships only the wire
contract. The tests pin the field set, the constraint behaviour
(score bounds, span half-open interval), and the trust-field naming
parity with the existing explorer search schemas. When the route
lands, additional integration tests can compose against these
guarantees.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.companion import (
    Citation,
    CitationSpan,
    GroundedAnswer,
    TrustSummary,
)
from app.schemas.knowledge_explore_search import ExploreSearchChunk


def _ts() -> TrustSummary:
    return TrustSummary(
        citation_count=0,
        validated_citation_count=0,
        source_backed_citation_count=0,
        candidate_citation_count=0,
        trust_gate_filtered_count=0,
    )


def _citation(**overrides: object) -> Citation:
    base: dict[str, object] = {
        "chunk_id": "c-1",
        "document_id": "d-1",
        "version_id": "v-1",
        "confidence": 0.85,
        "snippet": "Reviewer must validate every claim.",
    }
    base.update(overrides)
    return Citation(**base)  # type: ignore[arg-type]


class TestCitation:
    def test_minimal_citation_round_trips(self):
        c = _citation()
        # Defaults match the ADR-029 contract.
        assert c.span is None
        assert c.validation_status is None
        assert c.is_source_backed is False
        assert c.source_url is None

    def test_citation_rejects_confidence_outside_zero_one(self):
        with pytest.raises(ValidationError):
            _citation(confidence=1.5)
        with pytest.raises(ValidationError):
            _citation(confidence=-0.1)

    def test_citation_accepts_explicit_validation_status_and_trust_flag(self):
        c = _citation(validation_status="VALIDATED", is_source_backed=True)
        assert c.validation_status == "VALIDATED"
        assert c.is_source_backed is True

    def test_citation_span_requires_non_negative_offsets(self):
        with pytest.raises(ValidationError):
            CitationSpan(start_char=-1, end_char=10)
        # end_char < start_char is allowed by the schema (the model
        # doesn't enforce ordering — chunkers may emit zero-width
        # markers). The half-open interpretation is documented in
        # ADR-029 and enforced at consumption sites.
        ok = CitationSpan(start_char=10, end_char=5)
        assert ok.start_char == 10

    def test_citation_serialises_with_documented_field_set(self):
        c = _citation(
            span=CitationSpan(start_char=0, end_char=12),
            validation_status="VALIDATED",
            is_source_backed=True,
            source_url="https://example.invalid/policy.pdf",
        )
        body = c.model_dump()
        # The field set is the contract. Drift here means the wire
        # shape changed — a back-compat-policy violation per ADR-029.
        assert set(body) == {
            "chunk_id",
            "document_id",
            "version_id",
            "span",
            "confidence",
            "validation_status",
            "is_source_backed",
            "source_url",
            "snippet",
        }

    def test_citation_trust_field_names_match_explorer_search(self):
        """ADR-029: the trust labels must reuse the names already shipped
        by ``ExploreSearchChunk`` so frontends can share rendering logic.
        """
        explorer_fields = ExploreSearchChunk.model_fields
        citation_fields = Citation.model_fields
        assert "validation_status" in explorer_fields
        assert "is_source_backed" in explorer_fields
        assert "validation_status" in citation_fields
        assert "is_source_backed" in citation_fields


class TestTrustSummary:
    def test_all_counts_must_be_non_negative(self):
        for field in (
            "citation_count",
            "validated_citation_count",
            "source_backed_citation_count",
            "candidate_citation_count",
            "trust_gate_filtered_count",
        ):
            base: dict[str, int] = {
                "citation_count": 0,
                "validated_citation_count": 0,
                "source_backed_citation_count": 0,
                "candidate_citation_count": 0,
                "trust_gate_filtered_count": 0,
            }
            base[field] = -1
            with pytest.raises(ValidationError):
                TrustSummary(**base)

    def test_trust_summary_round_trips_a_realistic_breakdown(self):
        ts = TrustSummary(
            citation_count=4,
            validated_citation_count=3,
            source_backed_citation_count=1,
            candidate_citation_count=0,
            trust_gate_filtered_count=2,
        )
        assert ts.trust_gate_filtered_count == 2


class TestGroundedAnswer:
    def _answer(self, citations: list[Citation] | None = None) -> GroundedAnswer:
        return GroundedAnswer(
            answer_id="ans_01J000000000000000000000000",
            answer="The reviewer policy requires every claim to be validated.",
            citations=citations or [],
            trust_summary=_ts(),
            generated_at=datetime(2026, 5, 10, 15, 30, tzinfo=UTC),
            model="claude-sonnet-4-5",
        )

    def test_default_schema_version_is_v0_1(self):
        a = self._answer()
        assert a.schema_version == "v0.1"

    def test_schema_version_rejects_unknown_string(self):
        with pytest.raises(ValidationError):
            GroundedAnswer(
                schema_version="v9.9",  # type: ignore[arg-type]
                answer_id="ans_x",
                answer="…",
                citations=[],
                trust_summary=_ts(),
                generated_at=datetime.now(UTC),
                model="m",
            )

    def test_grounded_answer_serialises_with_documented_field_set(self):
        body = self._answer(citations=[_citation()]).model_dump()
        assert set(body) == {
            "schema_version",
            "answer_id",
            "answer",
            "citations",
            "trust_summary",
            "generated_at",
            "model",
        }

    def test_grounded_answer_carries_addressable_answer_id_for_feedback_bridge(self):
        """ADR-029 / #371: the feedback bridge addresses past responses
        by ``answer_id``; the field is required, not optional."""
        with pytest.raises(ValidationError):
            GroundedAnswer(
                # missing answer_id
                answer="…",
                citations=[],
                trust_summary=_ts(),
                generated_at=datetime.now(UTC),
                model="m",
            )  # type: ignore[call-arg]
