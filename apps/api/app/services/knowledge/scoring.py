"""Relevance / bridge / outlier scoring for the Explorer (ADR-028, #314).

Pure-function policy layer consumed by the neighborhood (#310), relation
evidence (#311), atlas summary (#312), and ranking controls (#320)
endpoints. No state, no side effects, no route-layer concerns — the
routes call these functions and project the results onto their wire
shapes.

## Stability guarantees (#314 acceptance criterion 2)

- **All thresholds are module-level constants.** Changing them is a
  deliberate-version-bump act with frontend visual implications.
- **All scoring functions are pure.** Same input → same output,
  every invocation. This is what lets the frontend cache visual
  weights without re-querying.
- **Tie-breaking is deterministic.** When two edges land at exactly
  the same combined score, ``rank_edges`` breaks ties on ``edge_id``
  ascending so paginated walks across multiple requests yield a
  stable order.

## Scope language

- **Strong / medium / weak** — a single-edge classification driven by
  the combined ``relation_strength_score``.
- **Bridge** — an edge whose endpoints belong to topics with a high
  Jaccard distance. Bridges are interesting candidate connections
  across otherwise unrelated content.
- **Outlier** — a strong-or-medium edge that *also* qualifies as a
  bridge: a candidate "surprising connection." Always carry
  *candidate* / *suggested* copy on the frontend per #314 notes —
  the policy never claims an outlier is a fact.
- **Bridge document** — a document whose chunks span multiple
  topics that are mutually distant; surfaced on the atlas (#312).

## Why deterministic, not learned

The Phase-1 graph payload doesn't carry click-through telemetry, so
there's nothing to train against today. A handcrafted policy keeps
the ranking explainable (every score has a per-factor breakdown
in :class:`ScoredEdge.contributing_factors`) and deterministic
(reviewers see the same "strongest 10" today and tomorrow). When
we have a labelled signal, the policy can swap to a learned model
behind the same Protocol.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel

# ── Thresholds (module-level constants) ──────────────────────────────

#: Combined-score floor a relation must clear to be classed
#: :data:`StrengthClass.STRONG`.
STRONG_SCORE_THRESHOLD: float = 0.70

#: Combined-score ceiling below which a relation is classed
#: :data:`StrengthClass.WEAK` and may be bundled out of the default
#: visible canvas (#314 acceptance criterion 4).
WEAK_SCORE_THRESHOLD: float = 0.30

#: Topic-Jaccard distance a relation must clear to be classed a
#: *bridge*. 0.6 means at least 60% of the union of keywords is
#: unique to one side — interpretation: the topics are mostly
#: disjoint.
BRIDGE_TOPIC_DISTANCE_THRESHOLD: float = 0.60

#: Combined-score floor an edge must clear to qualify as a candidate
#: outlier. Set just below ``STRONG_SCORE_THRESHOLD`` so a "high
#: medium" bridge edge can still surface as a surprising candidate
#: connection — the goal is to draw the operator's eye to potentially
#: interesting findings, not exclusively to deafeningly strong ones.
OUTLIER_STRENGTH_FLOOR: float = 0.60

#: Maximum bonus added to the raw deterministic relation score for
#: corroboration signals (shared-keyword count + source-chunk
#: corroboration + validation status). Caps the bonus so a
#: low-quality raw score can't be elevated to "strong" purely on
#: secondary signals.
MAX_CORROBORATION_BONUS: float = 0.30


class StrengthClass(StrEnum):
    """Three-bucket classification of a relation's combined score.

    The bucket labels are stable across versions of this module —
    changing them is a wire-shape change. Threshold values may shift
    (with deliberate version bumps); the labels stay.
    """

    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"


class ScoredEdge(BaseModel):
    """Per-edge scoring output consumed by the Explorer's read paths.

    Wire-shape model: ``apps/web/src/api/generated/schema.ts`` reflects
    this exactly when a route returns it. ``contributing_factors``
    is a transparency dump — each entry is a per-input partial that
    summed to ``score`` — so a reviewer / debugger can trace why an
    edge ended up where it did without re-running the algorithm.
    """

    edge_id: str
    score: float = Field(ge=0.0, le=1.0)
    strength_class: StrengthClass
    is_bridge: bool
    is_outlier: bool
    contributing_factors: dict[str, float] = Field(default_factory=dict)


# ── Combined relation strength ───────────────────────────────────────


def relation_strength_score(
    *,
    raw_score: float,
    shared_keyword_count: int = 0,
    source_chunk_count: int = 1,
    validation_bonus: float = 0.0,
) -> float:
    """Combine the deterministic edge score with corroboration signals.

    Inputs:

    - ``raw_score`` — :attr:`ChunkRelationEdgeProperties.score`, a
      Jaccard / TF-IDF-derived similarity already clipped to ``[0, 1]``.
    - ``shared_keyword_count`` — count of overlapping keywords; each
      additional shared keyword (up to 4) adds a small bonus, modelling
      the intuition that more keyword agreement strengthens the relation
      independent of the raw similarity score.
    - ``source_chunk_count`` — for relations aggregated from multiple
      contributing chunk pairs (e.g. document-level edges aggregated
      from per-chunk relations). One pair adds nothing; each additional
      pair (up to 3) adds a small bonus.
    - ``validation_bonus`` — caller-supplied bonus in ``[0, 0.2]`` for
      validation / source-backed status. Routes pass ``0.2`` when both
      endpoints are source-backed, ``0.1`` when one is, ``0`` otherwise.

    The total bonus is capped at :data:`MAX_CORROBORATION_BONUS` so
    secondary signals can't elevate a low-quality raw score to
    "strong" on their own.

    Returns a deterministic value in ``[0, 1]``. Same input → same
    output (acceptance criterion: tie-breaking).
    """
    if not 0.0 <= raw_score <= 1.0:
        raise ValueError(f"raw_score must be in [0, 1], got {raw_score}")
    if not 0.0 <= validation_bonus <= 0.20:
        raise ValueError(f"validation_bonus must be in [0, 0.2], got {validation_bonus}")

    keyword_bonus = 0.05 * min(max(shared_keyword_count, 0), 4)
    corroboration_bonus = 0.05 * min(max(source_chunk_count - 1, 0), 3)
    total_bonus = min(
        keyword_bonus + corroboration_bonus + validation_bonus,
        MAX_CORROBORATION_BONUS,
    )
    return min(raw_score + total_bonus, 1.0)


def classify_strength(
    score: float,
    *,
    strong_threshold: float = STRONG_SCORE_THRESHOLD,
    weak_threshold: float = WEAK_SCORE_THRESHOLD,
) -> StrengthClass:
    """Bucket a combined score into ``strong`` / ``medium`` / ``weak``.

    Boundary semantics:

    - ``score >= strong_threshold`` → :data:`StrengthClass.STRONG`.
    - ``score < weak_threshold`` → :data:`StrengthClass.WEAK`.
    - Otherwise → :data:`StrengthClass.MEDIUM`.

    The strong-threshold check is inclusive (a score exactly at the
    threshold is *strong*); the weak-threshold check is strict (a
    score exactly at the weak threshold is *medium*, not *weak*) so
    the three buckets are mutually exclusive and cover ``[0, 1]``.
    """
    if not 0.0 <= weak_threshold <= strong_threshold <= 1.0:
        raise ValueError("thresholds must satisfy 0 <= weak_threshold <= strong_threshold <= 1")
    if score >= strong_threshold:
        return StrengthClass.STRONG
    if score < weak_threshold:
        return StrengthClass.WEAK
    return StrengthClass.MEDIUM


# ── Topic distance + bridge detection ─────────────────────────────────


def topic_distance(
    topic_a_keywords: Iterable[str],
    topic_b_keywords: Iterable[str],
) -> float:
    """Jaccard distance between two keyword sets: ``1 - |A∩B| / |A∪B|``.

    - Identical sets → ``0.0`` (no distance).
    - Disjoint sets → ``1.0`` (max distance).
    - **Both sets empty** → ``1.0``: there's no shared signal, so we
      treat the pair as maximally distant. Keeps ``is_bridge_edge``
      from accidentally classifying two empty-keyword topics as
      "same topic".

    Symmetric: ``topic_distance(a, b) == topic_distance(b, a)``.
    """
    a = set(topic_a_keywords)
    b = set(topic_b_keywords)
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    intersection = a & b
    return 1.0 - len(intersection) / len(union)


def is_bridge_edge(
    *,
    source_topic_keywords: Iterable[str],
    target_topic_keywords: Iterable[str],
    threshold: float = BRIDGE_TOPIC_DISTANCE_THRESHOLD,
) -> bool:
    """An edge is a *bridge* when its endpoints' topics are distant.

    Distance is :func:`topic_distance` between the two endpoint topics'
    keyword sets. Edges with both endpoints in the same topic always
    return ``False`` (distance is ``0``).
    """
    return topic_distance(source_topic_keywords, target_topic_keywords) >= threshold


def bridge_document_score(topic_keyword_sets: list[list[str]]) -> float:
    """Mean pairwise topic distance among a document's topics.

    A document whose chunks span 5 mutually-distant topics scores
    high; a document with chunks in a single topic scores ``0``. The
    output is the *mean* (not the max) so a single far-out-lier
    topic in a doc dominated by one cluster doesn't drag the score
    up by itself.

    Returns ``0.0`` for documents with 0 or 1 topics — they can't
    bridge anything.
    """
    if len(topic_keyword_sets) < 2:
        return 0.0
    distances: list[float] = []
    for i in range(len(topic_keyword_sets)):
        for j in range(i + 1, len(topic_keyword_sets)):
            distances.append(topic_distance(topic_keyword_sets[i], topic_keyword_sets[j]))
    if not distances:
        return 0.0
    return sum(distances) / len(distances)


def is_outlier(
    *,
    score: float,
    is_bridge: bool,
    strength_floor: float = OUTLIER_STRENGTH_FLOOR,
) -> bool:
    """A *candidate outlier* is a strong-or-medium-strong relation
    that also qualifies as a bridge — a high-strength edge across a
    wide topic gap. The terminology is **candidate** (not "fact") on
    purpose; consumers should render outliers as suggestions per the
    #314 notes.
    """
    return is_bridge and score >= strength_floor


# ── Aggregate scoring helper ──────────────────────────────────────────


def score_edge(
    *,
    edge_id: str,
    raw_score: float,
    shared_keyword_count: int = 0,
    source_chunk_count: int = 1,
    validation_bonus: float = 0.0,
    source_topic_keywords: Iterable[str] = (),
    target_topic_keywords: Iterable[str] = (),
) -> ScoredEdge:
    """Convenience composition: combine score, classify, detect bridge,
    detect outlier — return a fully-populated :class:`ScoredEdge`.

    The ``contributing_factors`` dict carries the per-input partial
    so reviewers can audit *why* an edge ended up with its score
    without re-running the math. Useful for the relation inspector
    (#311) and any future debug surface.
    """
    score = relation_strength_score(
        raw_score=raw_score,
        shared_keyword_count=shared_keyword_count,
        source_chunk_count=source_chunk_count,
        validation_bonus=validation_bonus,
    )
    bridge = is_bridge_edge(
        source_topic_keywords=source_topic_keywords,
        target_topic_keywords=target_topic_keywords,
    )
    contributing = {
        "raw_score": raw_score,
        "shared_keyword_bonus": 0.05 * min(max(shared_keyword_count, 0), 4),
        "source_chunk_bonus": 0.05 * min(max(source_chunk_count - 1, 0), 3),
        "validation_bonus": validation_bonus,
        "topic_distance": topic_distance(source_topic_keywords, target_topic_keywords),
    }
    return ScoredEdge(
        edge_id=edge_id,
        score=score,
        strength_class=classify_strength(score),
        is_bridge=bridge,
        is_outlier=is_outlier(score=score, is_bridge=bridge),
        contributing_factors=contributing,
    )


# ── Deterministic ranking ─────────────────────────────────────────────


_RankBy = Literal["strength", "outlier", "bridge"]


def rank_edges(
    edges: Iterable[ScoredEdge],
    *,
    by: _RankBy = "strength",
) -> list[ScoredEdge]:
    """Deterministic sort for canvas ordering / paginated walks.

    Ordering keys (descending unless noted):

    - ``strength`` — by ``score``; tie-breaker on ``edge_id`` ascending.
    - ``outlier`` — outliers first (``is_outlier=True``), then by
      ``score`` descending; tie-breaker on ``edge_id`` ascending.
    - ``bridge`` — bridges first (``is_bridge=True``), then by
      ``score`` descending; tie-breaker on ``edge_id`` ascending.

    Tie-breaker uses the lexicographic ``edge_id`` so two edges with
    identical scores land in the same position across requests — the
    cursor-based walks on top of the Explorer endpoints (#310,
    #312) need this to paginate cleanly.
    """
    edges_list = list(edges)
    if by == "strength":
        return sorted(edges_list, key=lambda e: (-e.score, e.edge_id))
    if by == "outlier":
        return sorted(edges_list, key=lambda e: (not e.is_outlier, -e.score, e.edge_id))
    if by == "bridge":
        return sorted(edges_list, key=lambda e: (not e.is_bridge, -e.score, e.edge_id))
    raise ValueError(f"unknown ranking key: {by!r}")


# ── Weak-link bundling helper (#314 AC-4) ─────────────────────────────


def partition_visible_and_weak(
    edges: Iterable[ScoredEdge],
) -> tuple[list[ScoredEdge], list[ScoredEdge]]:
    """Split edges into ``(visible, weak)`` lists.

    Visible edges are those classified ``STRONG`` or ``MEDIUM``;
    weak edges are bundled out of the default canvas but routes
    can still surface their **count** in a "+ N weak links"
    indicator (acceptance criterion 4).

    Both lists preserve the input iteration order — sort separately
    via :func:`rank_edges` if a deterministic order is needed.
    """
    visible: list[ScoredEdge] = []
    weak: list[ScoredEdge] = []
    for edge in edges:
        if edge.strength_class is StrengthClass.WEAK:
            weak.append(edge)
        else:
            visible.append(edge)
    return visible, weak


__all__ = [
    "BRIDGE_TOPIC_DISTANCE_THRESHOLD",
    "MAX_CORROBORATION_BONUS",
    "OUTLIER_STRENGTH_FLOOR",
    "STRONG_SCORE_THRESHOLD",
    "WEAK_SCORE_THRESHOLD",
    "ScoredEdge",
    "StrengthClass",
    "bridge_document_score",
    "classify_strength",
    "is_bridge_edge",
    "is_outlier",
    "partition_visible_and_weak",
    "rank_edges",
    "relation_strength_score",
    "score_edge",
    "topic_distance",
]
