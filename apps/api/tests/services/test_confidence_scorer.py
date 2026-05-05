"""Unit tests for the 5-signal HITL confidence scorer (ADR-023, #215).

Pure unit tests with hand-built fixtures — no FastAPI, no TestClient,
no projector wiring. The contracts under test are:

1. Each signal returns ``[0.0, 1.0]`` with 1.0 = best, computed
   correctly on a high-confidence vs low-confidence fixture.
2. The OCR override forces ``overall = 0.0`` regardless of the other
   signals.
3. Weights normalise (any positive scale → unit-sum), reject negatives,
   reject all-zero, and require every canonical signal name.
4. Round-trip: a scored ``ConfidenceScore`` carries every weight key
   the scorer was built with.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.schemas.document import DocumentVersion
from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticAsset,
    SemanticDocument,
    SemanticSection,
)
from app.services.confidence_scorer import (
    ALL_SIGNALS,
    LENGTH_Z_THRESHOLD,
    SIGNAL_CITATION_COVERAGE,
    SIGNAL_LENGTH_Z,
    SIGNAL_OCR,
    SIGNAL_ORPHAN_RATIO,
    SIGNAL_TOPIC_INCOHERENCE,
    ConfidenceScorer,
    EntityCitationStat,
    default_ocr_flag,
)
from app.services.corpus_norms import (
    METRIC_ASSET_COUNT,
    METRIC_SECTION_LENGTH,
    CorpusNorm,
    InMemoryCorpusNormsStore,
)

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _version(content_type: str = "text/plain") -> DocumentVersion:
    return DocumentVersion(
        id="ver-1",
        document_id="doc-1",
        version_number=1,
        filename="fixture.txt",
        content_type=content_type,
        file_size=10,
        sha256="0" * 64,
        storage_uri="memory://0",
        status="STORED",  # type: ignore[arg-type]
    )


def _semantic_high_quality() -> SemanticDocument:
    """5 sections with shared keywords (good clustering), 5 assets."""
    return SemanticDocument(
        document_version_id="ver-1",
        document_profile=DocumentProfile(title="Compliance Policy"),
        sections=[
            SemanticSection(
                id=f"sec-{i}",
                heading=f"ISO 9001 compliance section {i}",
                text=(
                    "This document outlines the ISO 9001 compliance "
                    "framework requirements. The compliance framework "
                    "specifies process control standards across the "
                    "organisation."
                ),
            )
            for i in range(5)
        ],
        assets=[
            SemanticAsset(type="requirement", text=f"Asset {i}", confidence=0.9) for i in range(5)
        ],
    )


def _semantic_low_quality() -> SemanticDocument:
    """5 sections with no shared content (orphan-heavy + topic-incoherent)."""
    diverse_texts = [
        "Quantum entanglement requires careful experimental setup with photons.",
        "Roman cuisine emphasises olive oil tomatoes and garlic in pasta.",
        "Polar bears hunt seals on Arctic sea ice during winter months.",
        "The piano sonata in C major begins with a graceful melodic theme.",
        "Bicycle gear ratios determine pedaling cadence on mountain trails.",
    ]
    return SemanticDocument(
        document_version_id="ver-1",
        document_profile=DocumentProfile(title="Mixed Bag"),
        sections=[
            SemanticSection(
                id=f"sec-{i}",
                heading=f"Section {i}",
                text=text,
            )
            for i, text in enumerate(diverse_texts)
        ],
        assets=[],
    )


def _build_scorer(weights: dict[str, float] | None = None) -> ConfidenceScorer:
    """Construct a scorer with an empty in-memory norms store."""
    return ConfidenceScorer(
        weights=weights,
        corpus_norms=InMemoryCorpusNormsStore(),
    )


# ---------------------------------------------------------------------------
# Per-signal contracts
# ---------------------------------------------------------------------------


def test_orphan_signal_high_quality_doc_scores_high():
    """Same-topic sections cluster together — no orphans."""
    scorer = _build_scorer()
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    assert score.signals[SIGNAL_ORPHAN_RATIO] == pytest.approx(1.0)


def test_orphan_signal_low_quality_doc_scores_low():
    """Diverse-content sections share no keywords — every chunk an orphan."""
    scorer = _build_scorer()
    score = scorer.score(version=_version(), semantic=_semantic_low_quality())
    assert score.signals[SIGNAL_ORPHAN_RATIO] < 0.5


def test_orphan_signal_empty_doc_scores_one():
    """A doc with zero chunks has nothing to be orphan of (benign case)."""
    scorer = _build_scorer()
    empty = SemanticDocument(
        document_version_id="ver-1",
        document_profile=DocumentProfile(title="Empty"),
    )
    score = scorer.score(version=_version(), semantic=empty)
    assert score.signals[SIGNAL_ORPHAN_RATIO] == 1.0


def test_topic_incoherence_signal_high_quality_doc_scores_high():
    """All chunks land in the dominant topic when they share keywords."""
    scorer = _build_scorer()
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    assert score.signals[SIGNAL_TOPIC_INCOHERENCE] == pytest.approx(1.0)


def test_topic_incoherence_signal_no_clustering_scores_one():
    """If no chunk has a topic id, the orphan signal absorbs the penalty."""
    scorer = _build_scorer()
    score = scorer.score(version=_version(), semantic=_semantic_low_quality())
    # No clusters formed → signal_value = 1.0 by the empty-cluster rule.
    assert score.signals[SIGNAL_TOPIC_INCOHERENCE] == 1.0


def test_length_z_signal_unknown_bucket_scores_one():
    """Cold-start tolerance: missing norm → 1.0 (ADR-023 §1)."""
    scorer = _build_scorer()
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    assert score.signals[SIGNAL_LENGTH_Z] == 1.0


def test_length_z_signal_within_threshold_scores_one():
    """Sections within ±2.5σ of the mean are length-normal."""
    norms = InMemoryCorpusNormsStore()
    # Mean 200 / stddev 50 — every section in the high-quality fixture
    # has text length ~155 which is within (200 - 50*2.5) = 75.
    norms.upsert(
        CorpusNorm(
            content_type="text/plain",
            topic_cluster="",
            metric_name=METRIC_SECTION_LENGTH,
            sample_count=10,
            mean=200.0,
            stddev=50.0,
        )
    )
    scorer = ConfidenceScorer(corpus_norms=norms)
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    assert score.signals[SIGNAL_LENGTH_Z] == pytest.approx(1.0)


def test_length_z_signal_outside_threshold_scores_low():
    """Sections far from the mean lower the signal."""
    norms = InMemoryCorpusNormsStore()
    # Tiny mean / stddev → every 155-char section is way outside.
    norms.upsert(
        CorpusNorm(
            content_type="text/plain",
            topic_cluster="",
            metric_name=METRIC_SECTION_LENGTH,
            sample_count=10,
            mean=10.0,
            stddev=1.0,
        )
    )
    scorer = ConfidenceScorer(corpus_norms=norms)
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    assert score.signals[SIGNAL_LENGTH_Z] == 0.0


def test_citation_coverage_signal_phase2_on_full_coverage():
    scorer = _build_scorer()
    score = scorer.score(
        version=_version(),
        semantic=_semantic_high_quality(),
        entity_stat=EntityCitationStat(total_entities=10, cited_entities=10),
    )
    assert score.signals[SIGNAL_CITATION_COVERAGE] == 1.0


def test_citation_coverage_signal_phase2_on_partial_coverage():
    scorer = _build_scorer()
    score = scorer.score(
        version=_version(),
        semantic=_semantic_high_quality(),
        entity_stat=EntityCitationStat(total_entities=10, cited_entities=4),
    )
    assert score.signals[SIGNAL_CITATION_COVERAGE] == pytest.approx(0.4)


def test_citation_coverage_signal_phase2_off_falls_back_to_asset_z():
    """When entity_stat is None, the asset-count z fallback runs."""
    norms = InMemoryCorpusNormsStore()
    # 5 assets is far from a corpus mean of 100 with stddev 10 — outside threshold.
    norms.upsert(
        CorpusNorm(
            content_type="text/plain",
            topic_cluster="",
            metric_name=METRIC_ASSET_COUNT,
            sample_count=10,
            mean=100.0,
            stddev=10.0,
        )
    )
    scorer = ConfidenceScorer(corpus_norms=norms)
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    assert score.signals[SIGNAL_CITATION_COVERAGE] == 0.0


def test_citation_coverage_signal_phase2_off_unknown_bucket_scores_one():
    """Cold-start: no norm available → 1.0."""
    scorer = _build_scorer()
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    # No norm seeded, no entity_stat → fallback hits cold-start.
    assert score.signals[SIGNAL_CITATION_COVERAGE] == 1.0


# ---------------------------------------------------------------------------
# OCR override
# ---------------------------------------------------------------------------


def test_ocr_override_forces_overall_to_zero():
    """ADR-023 §1: OCR'd version → overall=0.0 + ocr_override_active=True."""
    scorer = ConfidenceScorer(
        corpus_norms=InMemoryCorpusNormsStore(),
        ocr_flag_fn=lambda _v: True,
    )
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    assert score.overall == 0.0
    assert score.ocr_override_active is True
    # The OCR signal itself goes to 0.0 too — the override path doesn't
    # short-circuit per-signal computation, only the overall.
    assert score.signals[SIGNAL_OCR] == 0.0


def test_default_ocr_flag_returns_false():
    """Until the OCR parser path lands (#47), every version is non-OCR."""
    assert default_ocr_flag(_version()) is False
    scorer = _build_scorer()
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    assert score.ocr_override_active is False


# ---------------------------------------------------------------------------
# Weight normalisation
# ---------------------------------------------------------------------------


def test_default_weights_are_equal():
    scorer = _build_scorer()
    assert scorer.weights == {name: pytest.approx(0.2) for name in ALL_SIGNALS}


def test_weights_normalise_to_unit_sum():
    """Operators may pass any positive scale; the scorer divides through."""
    scaled = dict.fromkeys(ALL_SIGNALS, 2.0)
    scorer = _build_scorer(weights=scaled)
    assert sum(scorer.weights.values()) == pytest.approx(1.0)
    for value in scorer.weights.values():
        assert value == pytest.approx(0.2)


def test_negative_weight_raises():
    bad = dict.fromkeys(ALL_SIGNALS, 0.2)
    bad[SIGNAL_OCR] = -0.1
    with pytest.raises(ValueError, match="non-negative"):
        _build_scorer(weights=bad)


def test_all_zero_weights_raises():
    zeros = dict.fromkeys(ALL_SIGNALS, 0.0)
    with pytest.raises(ValueError, match="positive value"):
        _build_scorer(weights=zeros)


def test_missing_signal_raises():
    incomplete = {name: 0.2 for name in ALL_SIGNALS if name != SIGNAL_OCR}
    with pytest.raises(ValueError, match="missing required signal"):
        _build_scorer(weights=incomplete)


# ---------------------------------------------------------------------------
# Persisted shape
# ---------------------------------------------------------------------------


def test_score_carries_every_signal_key():
    scorer = _build_scorer()
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    assert set(score.signals.keys()) == set(ALL_SIGNALS)
    assert set(score.weights.keys()) == set(ALL_SIGNALS)


def test_score_overall_is_in_unit_interval():
    scorer = _build_scorer()
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    assert 0.0 <= score.overall <= 1.0


def test_score_computed_at_is_utc():
    scorer = _build_scorer()
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    assert score.computed_at.tzinfo is not None
    # ``utcoffset()`` is the canonical "is this UTC?" check; UTC carries
    # offset 0 regardless of which tz object (UTC vs ``timezone.utc``)
    # was used.
    assert score.computed_at.utcoffset() == datetime.now(UTC).utcoffset()


def test_score_carries_scorer_version():
    scorer = _build_scorer()
    score = scorer.score(version=_version(), semantic=_semantic_high_quality())
    assert score.computed_by_version == "v1"


def test_signal_threshold_constant_is_documented_in_adr():
    """Pin the documented constant so an accidental retune surfaces in CI."""
    assert LENGTH_Z_THRESHOLD == 2.5
