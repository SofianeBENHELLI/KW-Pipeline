"""Tests for the corpus-level emerging taxonomy aggregator (EPIC-1 §1.5, #342)."""

from __future__ import annotations

import pytest

from app.schemas.deterministic_taxonomy import (
    DeterministicTaxonomyConcept,
    DeterministicTaxonomyForChunk,
)
from app.services.knowledge.corpus_emerging import aggregate_emerging_taxonomy


def _chunk(chunk_id: str, concepts: list[tuple[str, str, float]]) -> DeterministicTaxonomyForChunk:
    """Build one chunk projection from ``(kind, text, confidence)`` tuples.

    Trims test setup to the field that matters for aggregation
    (the ``concepts`` list); identifiers + heading are placeholders.
    """
    return DeterministicTaxonomyForChunk(
        chunk_id=chunk_id,
        section_id=chunk_id,
        heading=f"Section {chunk_id}",
        concepts=[
            DeterministicTaxonomyConcept(kind=k, text=t, confidence=c)  # type: ignore[arg-type]
            for k, t, c in concepts
        ],
    )


# ─── Frequency floor ───────────────────────────────────────────────────


class TestFrequencyFloor:
    def test_singletons_dropped_by_default(self) -> None:
        chunks = [
            _chunk("c1", [("keyword", "alpha", 0.9)]),
            _chunk("c2", [("keyword", "beta", 0.9)]),
        ]
        suggestions = aggregate_emerging_taxonomy(chunks)
        assert suggestions == []  # each appears once → below default min_frequency=2

    def test_recurring_concept_survives(self) -> None:
        chunks = [
            _chunk("c1", [("keyword", "battery", 0.9)]),
            _chunk("c2", [("keyword", "battery", 0.9)]),
            _chunk("c3", [("keyword", "battery", 0.9)]),
        ]
        suggestions = aggregate_emerging_taxonomy(chunks)
        assert len(suggestions) == 1
        assert suggestions[0].label == "battery"
        # Evidence list reflects the three chunks.
        assert suggestions[0].evidence_chunk_ids == ["c1", "c2", "c3"]

    def test_min_frequency_one_includes_singletons(self) -> None:
        chunks = [_chunk("c1", [("keyword", "alpha", 0.9)])]
        suggestions = aggregate_emerging_taxonomy(chunks, min_frequency=1)
        assert [s.label for s in suggestions] == ["alpha"]

    def test_min_frequency_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_frequency must be >= 1"):
            aggregate_emerging_taxonomy([_chunk("c1", [])], min_frequency=0)


# ─── Cross-kind collapsing ─────────────────────────────────────────────


class TestCrossKindCollapse:
    def test_higher_weight_kind_wins(self) -> None:
        """``Battery Thermal`` shows up as both a noun_phrase and a
        keyword across the corpus. The aggregator collapses them into
        one suggestion with the higher-weight kind (noun_phrase >
        keyword)."""
        chunks = [
            _chunk("c1", [("keyword", "battery thermal", 0.85)]),
            _chunk("c2", [("noun_phrase", "Battery Thermal", 0.70)]),
            _chunk("c3", [("keyword", "battery thermal", 0.85)]),
        ]
        suggestions = aggregate_emerging_taxonomy(chunks)
        # 3 distinct chunks → above the floor. The canonical casing
        # comes from the more frequent variant ("battery thermal"
        # appeared twice vs "Battery Thermal" once).
        assert len(suggestions) == 1
        assert suggestions[0].label.lower() == "battery thermal"
        # Description names the winning kind.
        assert "noun_phrase" in suggestions[0].description

    def test_union_evidence_across_kinds(self) -> None:
        """Cross-kind merge widens the evidence chunk set."""
        chunks = [
            _chunk("c1", [("keyword", "iso 9001", 0.85)]),
            _chunk("c2", [("standard", "ISO 9001", 0.95)]),
        ]
        suggestions = aggregate_emerging_taxonomy(chunks)
        # 2 distinct chunks → above the floor; one suggestion.
        assert len(suggestions) == 1
        assert sorted(suggestions[0].evidence_chunk_ids) == ["c1", "c2"]
        # ``standard`` outweighs ``keyword`` → wins.
        assert "standard" in suggestions[0].description


# ─── Ranking + cap ─────────────────────────────────────────────────────


class TestRanking:
    def test_higher_frequency_ranks_first(self) -> None:
        chunks = [
            _chunk("c1", [("keyword", "alpha", 0.9), ("keyword", "beta", 0.9)]),
            _chunk("c2", [("keyword", "alpha", 0.9), ("keyword", "beta", 0.9)]),
            _chunk("c3", [("keyword", "alpha", 0.9)]),  # alpha: 3 chunks
            # beta: 2 chunks
        ]
        suggestions = aggregate_emerging_taxonomy(chunks)
        assert [s.label for s in suggestions] == ["alpha", "beta"]

    def test_higher_kind_weight_breaks_frequency_ties(self) -> None:
        """Same frequency → higher-weight kind wins (noun_phrase >
        keyword)."""
        chunks = [
            _chunk("c1", [("keyword", "alpha", 0.85), ("noun_phrase", "Beta Gamma", 0.7)]),
            _chunk("c2", [("keyword", "alpha", 0.85), ("noun_phrase", "Beta Gamma", 0.7)]),
        ]
        suggestions = aggregate_emerging_taxonomy(chunks)
        # Both have frequency 2 → noun_phrase wins the tie.
        assert suggestions[0].label == "Beta Gamma"
        assert suggestions[1].label == "alpha"

    def test_top_n_caps_result_count(self) -> None:
        chunks = [
            _chunk("c1", [("keyword", f"t{i}", 0.9) for i in range(5)]),
            _chunk("c2", [("keyword", f"t{i}", 0.9) for i in range(5)]),
        ]
        suggestions = aggregate_emerging_taxonomy(chunks, top_n=3)
        assert len(suggestions) == 3

    def test_top_n_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="top_n must be >= 1"):
            aggregate_emerging_taxonomy([_chunk("c1", [])], top_n=0)


# ─── Confidence + evidence ─────────────────────────────────────────────


class TestSuggestionShape:
    def test_confidence_is_average_across_observations(self) -> None:
        chunks = [
            _chunk("c1", [("keyword", "x", 0.5)]),
            _chunk("c2", [("keyword", "x", 0.9)]),
            _chunk("c3", [("keyword", "x", 0.7)]),
        ]
        suggestions = aggregate_emerging_taxonomy(chunks)
        assert len(suggestions) == 1
        assert suggestions[0].confidence == pytest.approx((0.5 + 0.9 + 0.7) / 3)

    def test_default_source_is_extractor(self) -> None:
        chunks = [
            _chunk("c1", [("keyword", "x", 0.9)]),
            _chunk("c2", [("keyword", "x", 0.9)]),
        ]
        suggestions = aggregate_emerging_taxonomy(chunks)
        assert suggestions[0].source == "extractor"

    def test_explicit_source_override(self) -> None:
        chunks = [
            _chunk("c1", [("keyword", "x", 0.9)]),
            _chunk("c2", [("keyword", "x", 0.9)]),
        ]
        suggestions = aggregate_emerging_taxonomy(chunks, source="llm")
        assert suggestions[0].source == "llm"

    def test_state_starts_at_new(self) -> None:
        chunks = [
            _chunk("c1", [("keyword", "x", 0.9)]),
            _chunk("c2", [("keyword", "x", 0.9)]),
        ]
        suggestions = aggregate_emerging_taxonomy(chunks)
        assert suggestions[0].state == "NEW"


# ─── Determinism ───────────────────────────────────────────────────────


class TestDeterminism:
    def test_repeated_aggregation_byte_identical(self) -> None:
        chunks = [
            _chunk(
                "c1",
                [
                    ("keyword", "battery", 0.85),
                    ("noun_phrase", "Battery Thermal", 0.7),
                ],
            ),
            _chunk("c2", [("keyword", "battery", 0.85), ("standard", "ISO 9001", 0.95)]),
            _chunk("c3", [("standard", "ISO 9001", 0.95)]),
        ]
        first = aggregate_emerging_taxonomy(chunks)
        second = aggregate_emerging_taxonomy(chunks)
        # The ``suggestion_id`` is a fresh uuid per call + so are the
        # timestamps, so compare the deterministic shape fields.
        assert [s.label for s in first] == [s.label for s in second]
        assert [s.source for s in first] == [s.source for s in second]
        assert [s.confidence for s in first] == [s.confidence for s in second]
        assert [s.evidence_chunk_ids for s in first] == [s.evidence_chunk_ids for s in second]


# ─── Composition with the 1.2 store ────────────────────────────────────


class TestComposition:
    """Pin that the aggregator's output drops cleanly into the slice 1.2
    store via :func:`add_suggestions`. Light integration test — the
    store's own behaviour is exercised in ``test_taxonomy_version_store.py``."""

    def test_aggregator_output_lands_on_draft(self) -> None:
        from app.services.taxonomy_version_store import (
            InMemoryTaxonomyVersionStore,
            add_suggestions,
            create_draft,
        )

        store = InMemoryTaxonomyVersionStore()
        draft = create_draft(store)
        chunks = [
            _chunk("c1", [("keyword", "battery", 0.9), ("standard", "ISO 9001", 0.95)]),
            _chunk("c2", [("keyword", "battery", 0.9), ("standard", "ISO 9001", 0.95)]),
        ]
        suggestions = aggregate_emerging_taxonomy(chunks)
        updated = add_suggestions(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            suggestions=suggestions,
        )
        labels = {s.label for s in updated.suggestions}
        assert "battery" in labels
        assert "ISO 9001" in labels
        # All landed as NEW.
        assert all(s.state == "NEW" for s in updated.suggestions)
