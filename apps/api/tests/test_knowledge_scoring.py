"""Tests for the Explorer scoring policy (ADR-028, #314).

Pins the deterministic-and-stable contract the frontend relies on:

- pure functions (same input → same output);
- bounded outputs (every score in ``[0, 1]``, classes in three buckets);
- tie-breaking is on ``edge_id`` ascending (so paginated walks across
  the same edges land them in the same order each call);
- bridge detection sees through identical / disjoint topic-keyword
  sets cleanly, and treats both-empty as maximally distant;
- outlier classification needs strong-or-medium score + bridge — never
  on its own.

The test layout mirrors the public-API order of
:mod:`app.services.knowledge.scoring` so a reviewer reading the
service file top-to-bottom can locate the matching test in the same
position.
"""

from __future__ import annotations

import pytest

from app.services.knowledge.scoring import (
    MAX_CORROBORATION_BONUS,
    OUTLIER_STRENGTH_FLOOR,
    STRONG_SCORE_THRESHOLD,
    WEAK_SCORE_THRESHOLD,
    ScoredEdge,
    StrengthClass,
    bridge_document_score,
    classify_strength,
    is_bridge_edge,
    is_outlier,
    partition_visible_and_weak,
    rank_edges,
    relation_strength_score,
    score_edge,
    topic_distance,
)

# ── relation_strength_score ───────────────────────────────────────────


class TestRelationStrengthScore:
    def test_pure_function_same_input_yields_same_output(self) -> None:
        a = relation_strength_score(raw_score=0.5, shared_keyword_count=2, source_chunk_count=2)
        b = relation_strength_score(raw_score=0.5, shared_keyword_count=2, source_chunk_count=2)
        assert a == b

    def test_zero_inputs_pass_through(self) -> None:
        assert relation_strength_score(raw_score=0.0) == 0.0

    def test_score_clipped_at_one(self) -> None:
        # Big bonuses on top of a near-1 raw score must not exceed 1.
        score = relation_strength_score(
            raw_score=0.99,
            shared_keyword_count=10,  # capped at 4 internally
            source_chunk_count=10,  # capped at 3 internally
            validation_bonus=0.20,
        )
        assert score == pytest.approx(1.0)

    def test_total_bonus_capped_at_constant(self) -> None:
        # raw_score=0 + every bonus maxed → total cannot exceed
        # the documented MAX_CORROBORATION_BONUS.
        score = relation_strength_score(
            raw_score=0.0,
            shared_keyword_count=4,
            source_chunk_count=4,
            validation_bonus=0.20,
        )
        assert score == pytest.approx(MAX_CORROBORATION_BONUS)

    def test_bonuses_are_monotone(self) -> None:
        # More shared keywords / source chunks / validation bonus must
        # never *decrease* the combined score, holding others fixed.
        base = relation_strength_score(raw_score=0.4)
        more_keywords = relation_strength_score(raw_score=0.4, shared_keyword_count=3)
        more_chunks = relation_strength_score(raw_score=0.4, source_chunk_count=3)
        more_validation = relation_strength_score(raw_score=0.4, validation_bonus=0.10)
        assert more_keywords >= base
        assert more_chunks >= base
        assert more_validation >= base

    def test_invalid_raw_score_raises(self) -> None:
        with pytest.raises(ValueError):
            relation_strength_score(raw_score=1.5)
        with pytest.raises(ValueError):
            relation_strength_score(raw_score=-0.1)

    def test_invalid_validation_bonus_raises(self) -> None:
        with pytest.raises(ValueError):
            relation_strength_score(raw_score=0.5, validation_bonus=0.5)


# ── classify_strength ─────────────────────────────────────────────────


class TestClassifyStrength:
    def test_score_at_strong_threshold_is_strong_inclusive(self) -> None:
        assert classify_strength(STRONG_SCORE_THRESHOLD) is StrengthClass.STRONG

    def test_score_at_weak_threshold_is_medium_strict(self) -> None:
        # Boundary-strict: a score equal to the weak threshold lands
        # in MEDIUM, not WEAK. Documented in the function's docstring.
        assert classify_strength(WEAK_SCORE_THRESHOLD) is StrengthClass.MEDIUM

    def test_below_weak_is_weak(self) -> None:
        assert classify_strength(WEAK_SCORE_THRESHOLD - 1e-9) is StrengthClass.WEAK

    def test_three_buckets_cover_unit_interval(self) -> None:
        # Spot-check that 0, 0.5, 1 each land in one of the three
        # buckets without overlap.
        for score in (0.0, 0.5, 1.0):
            cls = classify_strength(score)
            assert cls in (
                StrengthClass.STRONG,
                StrengthClass.MEDIUM,
                StrengthClass.WEAK,
            )

    def test_invalid_threshold_pair_raises(self) -> None:
        with pytest.raises(ValueError):
            classify_strength(0.5, strong_threshold=0.4, weak_threshold=0.6)


# ── topic_distance + is_bridge_edge ───────────────────────────────────


class TestTopicDistance:
    def test_identical_sets_have_zero_distance(self) -> None:
        assert topic_distance(["a", "b", "c"], ["a", "b", "c"]) == 0.0

    def test_disjoint_sets_have_distance_one(self) -> None:
        assert topic_distance(["a", "b"], ["c", "d"]) == 1.0

    def test_both_empty_treated_as_max_distance(self) -> None:
        # Documented semantic: two empty-keyword topics carry no
        # signal of similarity, so we report max distance. Keeps
        # is_bridge_edge from accidentally bonding two empties.
        assert topic_distance([], []) == 1.0

    def test_partial_overlap_uses_jaccard(self) -> None:
        # |intersection| = 1 ("b"); |union| = 3 ("a", "b", "c").
        # Jaccard = 1/3, distance = 2/3.
        assert topic_distance(["a", "b"], ["b", "c"]) == pytest.approx(2 / 3)

    def test_symmetric(self) -> None:
        assert topic_distance(["a", "b"], ["b", "c"]) == topic_distance(["b", "c"], ["a", "b"])


class TestIsBridgeEdge:
    def test_same_topic_is_not_a_bridge(self) -> None:
        kw = ["a", "b", "c"]
        assert is_bridge_edge(source_topic_keywords=kw, target_topic_keywords=kw) is False

    def test_disjoint_topics_are_a_bridge(self) -> None:
        assert is_bridge_edge(source_topic_keywords=["x"], target_topic_keywords=["y"]) is True

    def test_threshold_is_inclusive(self) -> None:
        # Exactly at the threshold counts as a bridge. Hand-craft an
        # input that hits exactly BRIDGE_TOPIC_DISTANCE_THRESHOLD.
        # 0.6 = 1 - 2/5 → intersection size 2, union size 5.
        assert (
            is_bridge_edge(
                source_topic_keywords=["a", "b", "c"],
                target_topic_keywords=["a", "b", "d", "e"],
            )
            is True
        )


# ── bridge_document_score ─────────────────────────────────────────────


class TestBridgeDocumentScore:
    def test_zero_or_one_topic_scores_zero(self) -> None:
        assert bridge_document_score([]) == 0.0
        assert bridge_document_score([["a", "b"]]) == 0.0

    def test_two_disjoint_topics_score_one(self) -> None:
        assert bridge_document_score([["a"], ["b"]]) == 1.0

    def test_two_identical_topics_score_zero(self) -> None:
        assert bridge_document_score([["a", "b"], ["a", "b"]]) == 0.0

    def test_three_topics_take_mean_pairwise_distance(self) -> None:
        # Distances: (A,B)=1, (A,C)=1, (B,C)=1 → mean 1.0.
        assert bridge_document_score([["a"], ["b"], ["c"]]) == 1.0
        # All same → all distances 0.
        assert bridge_document_score([["a"], ["a"], ["a"]]) == 0.0


# ── is_outlier ────────────────────────────────────────────────────────


class TestIsOutlier:
    def test_strong_and_bridge_is_outlier(self) -> None:
        assert is_outlier(score=0.9, is_bridge=True) is True

    def test_strong_but_same_topic_is_not_outlier(self) -> None:
        assert is_outlier(score=0.9, is_bridge=False) is False

    def test_weak_bridge_is_not_outlier(self) -> None:
        # A weak edge across distant topics is just a weak edge.
        assert is_outlier(score=0.1, is_bridge=True) is False

    def test_score_at_floor_is_outlier_inclusive(self) -> None:
        # OUTLIER_STRENGTH_FLOOR is the lower bound (inclusive).
        assert is_outlier(score=OUTLIER_STRENGTH_FLOOR, is_bridge=True) is True


# ── score_edge composition ────────────────────────────────────────────


class TestScoreEdge:
    def test_returns_fully_populated_scored_edge(self) -> None:
        edge = score_edge(
            edge_id="e1",
            raw_score=0.8,
            shared_keyword_count=2,
            source_chunk_count=2,
            validation_bonus=0.0,
            source_topic_keywords=["alpha", "beta"],
            target_topic_keywords=["alpha", "beta"],
        )
        assert isinstance(edge, ScoredEdge)
        assert edge.edge_id == "e1"
        # Strong-and-same-topic → not a bridge → not an outlier.
        assert edge.strength_class is StrengthClass.STRONG
        assert edge.is_bridge is False
        assert edge.is_outlier is False

    def test_strong_bridge_emits_outlier(self) -> None:
        edge = score_edge(
            edge_id="e2",
            raw_score=0.85,
            source_topic_keywords=["alpha"],
            target_topic_keywords=["beta"],
        )
        assert edge.is_bridge is True
        assert edge.is_outlier is True

    def test_low_confidence_relation_is_weak(self) -> None:
        # raw_score=0.1 + no bonuses → final score 0.1, classed WEAK.
        edge = score_edge(edge_id="e3", raw_score=0.1)
        assert edge.strength_class is StrengthClass.WEAK
        # WEAK + same-topic → not an outlier.
        assert edge.is_outlier is False

    def test_contributing_factors_are_present_for_audit(self) -> None:
        edge = score_edge(
            edge_id="e4",
            raw_score=0.5,
            shared_keyword_count=2,
            source_chunk_count=2,
            validation_bonus=0.10,
            source_topic_keywords=["alpha"],
            target_topic_keywords=["beta"],
        )
        for key in (
            "raw_score",
            "shared_keyword_bonus",
            "source_chunk_bonus",
            "validation_bonus",
            "topic_distance",
        ):
            assert key in edge.contributing_factors


# ── rank_edges ────────────────────────────────────────────────────────


def _edge(
    edge_id: str,
    *,
    score: float = 0.5,
    is_bridge: bool = False,
    is_outlier: bool = False,
    strength_class: StrengthClass = StrengthClass.MEDIUM,
) -> ScoredEdge:
    return ScoredEdge(
        edge_id=edge_id,
        score=score,
        strength_class=strength_class,
        is_bridge=is_bridge,
        is_outlier=is_outlier,
    )


class TestRankEdges:
    def test_strength_orders_high_score_first(self) -> None:
        edges = [
            _edge("low", score=0.2),
            _edge("high", score=0.9),
            _edge("mid", score=0.5),
        ]
        ranked = rank_edges(edges, by="strength")
        assert [e.edge_id for e in ranked] == ["high", "mid", "low"]

    def test_strength_tie_break_is_edge_id_ascending(self) -> None:
        # Two edges at exactly the same score must always emerge in
        # the same order across calls — paginated walks rely on it.
        edges = [
            _edge("zeta", score=0.5),
            _edge("alpha", score=0.5),
            _edge("mu", score=0.5),
        ]
        ranked_a = [e.edge_id for e in rank_edges(edges, by="strength")]
        ranked_b = [e.edge_id for e in rank_edges(edges, by="strength")]
        assert ranked_a == ["alpha", "mu", "zeta"]
        assert ranked_a == ranked_b

    def test_outlier_floats_to_top(self) -> None:
        edges = [
            _edge("a", score=0.95, is_bridge=False, is_outlier=False),
            _edge("b", score=0.7, is_bridge=True, is_outlier=True),
            _edge("c", score=0.5),
        ]
        ranked = rank_edges(edges, by="outlier")
        # Outlier first, then non-outlier by score desc.
        assert ranked[0].edge_id == "b"
        assert ranked[1].edge_id == "a"
        assert ranked[2].edge_id == "c"

    def test_bridge_floats_to_top(self) -> None:
        edges = [
            _edge("a", score=0.9, is_bridge=False),
            _edge("b", score=0.5, is_bridge=True),
            _edge("c", score=0.7, is_bridge=True),
        ]
        ranked = rank_edges(edges, by="bridge")
        assert ranked[0].edge_id == "c"
        assert ranked[1].edge_id == "b"
        assert ranked[2].edge_id == "a"

    def test_unknown_key_raises(self) -> None:
        with pytest.raises(ValueError):
            rank_edges([], by="bogus")  # type: ignore[arg-type]


# ── partition_visible_and_weak (#314 AC-4) ────────────────────────────


class TestPartition:
    def test_weak_edges_separate_from_visible(self) -> None:
        edges = [
            _edge("strong-a", score=0.9, strength_class=StrengthClass.STRONG),
            _edge("weak-b", score=0.1, strength_class=StrengthClass.WEAK),
            _edge("medium-c", score=0.5, strength_class=StrengthClass.MEDIUM),
            _edge("weak-d", score=0.05, strength_class=StrengthClass.WEAK),
        ]
        visible, weak = partition_visible_and_weak(edges)
        assert {e.edge_id for e in visible} == {"strong-a", "medium-c"}
        assert {e.edge_id for e in weak} == {"weak-b", "weak-d"}

    def test_input_iteration_order_preserved(self) -> None:
        # The route layer pairs the visible list with the weak count
        # ("+ N weak links") — order must be stable so the count
        # always lines up.
        edges = [
            _edge("e1", score=0.9, strength_class=StrengthClass.STRONG),
            _edge("e2", score=0.05, strength_class=StrengthClass.WEAK),
            _edge("e3", score=0.5, strength_class=StrengthClass.MEDIUM),
        ]
        visible, weak = partition_visible_and_weak(edges)
        assert [e.edge_id for e in visible] == ["e1", "e3"]
        assert [e.edge_id for e in weak] == ["e2"]
