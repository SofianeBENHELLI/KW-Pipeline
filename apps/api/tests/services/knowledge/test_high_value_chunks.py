"""Unit tests for the high-value-chunks ranker (converged plan §C.2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.schemas.claim import Claim
from app.schemas.process import Process, ProcessStep
from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticDocument,
    SemanticSection,
)
from app.services.knowledge.high_value_chunks import (
    DEFAULT_WEIGHTS,
    HighValueChunksService,
)


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)


def _section(section_id: str, *, heading: str = "Heading", text: str = "Lorem ipsum.") -> SemanticSection:
    return SemanticSection(id=section_id, heading=heading, text=text)


def _semantic(*sections: SemanticSection) -> SemanticDocument:
    return SemanticDocument(
        document_version_id="ver-1",
        document_profile=DocumentProfile(title="Demo"),
        sections=list(sections),
    )


def _claim(
    claim_id: str,
    *,
    provenance_chunk_ids: list[str],
    subject_entity_id: str = "entity-aaa",
    object_entity_id: str | None = None,
    object_value: str | None = "v",
) -> Claim:
    if object_entity_id is not None:
        object_value = None
    return Claim(
        id=claim_id,
        document_id="doc-1",
        version_id="ver-1",
        subject_entity_id=subject_entity_id,
        predicate="mentions",
        object_value=object_value,
        object_entity_id=object_entity_id,
        confidence=0.9,
        extracted_at=_NOW,
        provenance_chunk_ids=provenance_chunk_ids,
    )


def _process(steps: list[tuple[str, list[str]]]) -> Process:
    """Build a Process from a list of ``(title, source_refs)`` tuples."""
    return Process(
        id="proc-1",
        title="SOP",
        document_id="doc-1",
        version_id="ver-1",
        created_at=_NOW,
        steps=[
            ProcessStep(
                step_number=i + 1,
                title=title,
                body="b",
                source_reference_ids=refs,
            )
            for i, (title, refs) in enumerate(steps)
        ],
    )


# ─── Sorting / shape ──────────────────────────────────────────────────


def test_rank_returns_chunks_sorted_by_score_desc() -> None:
    """The densest chunk lands at position 0."""
    semantic = _semantic(
        _section("c-1", text="Short."),
        _section("c-2", text="A second chunk."),
        _section("c-3", text="A third."),
    )
    # c-2 has many claims; c-1 and c-3 have none.
    claims = [
        _claim(f"claim-{i}", provenance_chunk_ids=["c-2"])
        for i in range(4)
    ]
    service = HighValueChunksService()
    ranked = service.rank(
        semantic=semantic, claims=claims, processes=[], limit=10,
    )
    assert ranked[0].chunk_id == "c-2"
    assert ranked[0].claim_count == 4
    assert ranked[0].score > ranked[-1].score


def test_rank_breaks_ties_by_chunk_id_asc() -> None:
    """Two chunks with identical scores tie-break deterministically."""
    semantic = _semantic(_section("c-b"), _section("c-a"))
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=[], processes=[], limit=10,
    )
    # Both have zero counts → tie → chunk_id ASC.
    assert [r.chunk_id for r in ranked] == ["c-a", "c-b"]


def test_rank_honours_limit() -> None:
    semantic = _semantic(*(_section(f"c-{i}") for i in range(5)))
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=[], processes=[], limit=3,
    )
    assert len(ranked) == 3


def test_rank_returns_empty_for_empty_semantic_document() -> None:
    semantic = _semantic()
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=[], processes=[], limit=10,
    )
    assert ranked == []


def test_rank_returns_empty_when_limit_is_zero() -> None:
    semantic = _semantic(_section("c-1"))
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=[], processes=[], limit=0,
    )
    assert ranked == []


# ─── Signal counting ──────────────────────────────────────────────────


def test_rank_counts_claims_per_chunk() -> None:
    semantic = _semantic(_section("c-1"), _section("c-2"))
    claims = [
        _claim("k1", provenance_chunk_ids=["c-1"]),
        _claim("k2", provenance_chunk_ids=["c-1", "c-2"]),
        _claim("k3", provenance_chunk_ids=["c-2"]),
    ]
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=claims, processes=[], limit=10,
    )
    by_id = {r.chunk_id: r for r in ranked}
    assert by_id["c-1"].claim_count == 2
    assert by_id["c-2"].claim_count == 2


def test_rank_ignores_stale_chunk_ids_in_claims() -> None:
    """Claims pointing at a chunk that's no longer in the semantic
    document (e.g. after a re-extract) don't contribute to anything."""
    semantic = _semantic(_section("c-1"))
    claims = [
        _claim("k1", provenance_chunk_ids=["c-1"]),
        _claim("k-stale", provenance_chunk_ids=["c-stale"]),
    ]
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=claims, processes=[], limit=10,
    )
    assert ranked[0].claim_count == 1


def test_rank_counts_process_steps_per_chunk() -> None:
    semantic = _semantic(_section("c-1"), _section("c-2"))
    process = _process(
        [
            ("step 1", ["c-1"]),
            ("step 2", ["c-1", "c-2"]),
            ("step 3", []),
        ]
    )
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=[], processes=[process], limit=10,
    )
    by_id = {r.chunk_id: r for r in ranked}
    assert by_id["c-1"].process_step_count == 2
    assert by_id["c-2"].process_step_count == 1


def test_rank_counts_entity_mentions_via_claim_subjects_and_objects() -> None:
    """Distinct subject + object entity ids in a chunk's claims count
    as entity mentions."""
    semantic = _semantic(_section("c-1"))
    claims = [
        _claim(
            "k1",
            provenance_chunk_ids=["c-1"],
            subject_entity_id="entity-alpha",
        ),
        _claim(
            "k2",
            provenance_chunk_ids=["c-1"],
            subject_entity_id="entity-beta",
            object_entity_id="entity-gamma",
            object_value=None,
        ),
        _claim(
            "k3",
            provenance_chunk_ids=["c-1"],
            subject_entity_id="entity-alpha",  # duplicate; should not double-count
        ),
    ]
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=claims, processes=[], limit=10,
    )
    assert ranked[0].entity_mention_count == 3  # alpha, beta, gamma


def test_rank_includes_graph_degree_from_chunk_relations() -> None:
    """Chunks that share keywords get edges; the ranker reflects degree."""
    semantic = _semantic(
        _section("c-1", text="ISO 9001 quality management system audit"),
        _section("c-2", text="ISO 9001 quality management system review"),
        _section("c-3", text="Completely unrelated content about cooking pasta"),
    )
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=[], processes=[], limit=10,
    )
    by_id = {r.chunk_id: r for r in ranked}
    # c-1 and c-2 must share at least one edge (shared standard / keywords).
    assert by_id["c-1"].graph_degree >= 1
    assert by_id["c-2"].graph_degree >= 1
    # c-3 has no overlap so it stays isolated.
    assert by_id["c-3"].graph_degree == 0


# ─── Composite score ──────────────────────────────────────────────────


def test_score_is_weighted_sum_of_normalised_signals() -> None:
    """A chunk that maxes only the claims signal scores
    ``weights.claims + weights.entity_density`` (an entity is implied
    by the claim's subject)."""
    # Disjoint tokens so the chunk-relations service emits no edges
    # between the two chunks; graph_degree stays 0 on both sides.
    semantic = _semantic(
        _section("c-1", text="aaaaa bbbbb ccccc ddddd."),
        _section("c-2", text="vvvvv wwwww xxxxx yyyyy."),
    )
    # Only c-1 has any claims; c-1 normalised claim signal = 1.0.
    # The claim's subject_entity_id contributes 1.0 to entity_density
    # too. process_steps + graph_degree are 0 across the doc.
    claim = _claim("k1", provenance_chunk_ids=["c-1"])
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=[claim], processes=[], limit=10,
    )
    top = ranked[0]
    assert top.chunk_id == "c-1"
    expected = DEFAULT_WEIGHTS.claims + DEFAULT_WEIGHTS.entity_density
    assert top.score == pytest.approx(expected, abs=1e-3)


def test_response_carries_per_signal_normalised_contribution() -> None:
    semantic = _semantic(
        _section("c-1", text="aaaaa bbbbb ccccc ddddd."),
        _section("c-2", text="vvvvv wwwww xxxxx yyyyy."),
    )
    claims = [_claim("k1", provenance_chunk_ids=["c-1"])]
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=claims, processes=[], limit=10,
    )
    top = next(r for r in ranked if r.chunk_id == "c-1")
    assert top.signals.claims == pytest.approx(1.0)
    assert top.signals.process_steps == 0.0
    assert top.signals.entity_density == pytest.approx(1.0)


# ─── Snippet ──────────────────────────────────────────────────────────


def test_snippet_is_capped_with_ellipsis() -> None:
    long_text = "alpha " * 200  # > 240 chars
    semantic = _semantic(_section("c-1", text=long_text))
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=[], processes=[], limit=10,
    )
    assert ranked[0].snippet.endswith("…")
    assert len(ranked[0].snippet) <= 240


def test_snippet_collapses_whitespace() -> None:
    semantic = _semantic(_section("c-1", text="alpha\n\n  beta   gamma"))
    ranked = HighValueChunksService().rank(
        semantic=semantic, claims=[], processes=[], limit=10,
    )
    assert ranked[0].snippet == "alpha beta gamma"
