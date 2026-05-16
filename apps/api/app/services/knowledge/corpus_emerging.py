"""Corpus-level emerging taxonomy aggregator (EPIC-1 slice 1.5, issue #342).

Rolls per-chunk :class:`DeterministicTaxonomyForChunk` outputs (slice
1.1, #338) up into corpus-level candidate concepts. Survivors of the
frequency floor become :class:`ConceptSuggestion` rows ready to land
on a fresh DRAFT :class:`TaxonomyVersion` (slice 1.2, #339).

Pipeline shape:

```
chunks → extract_deterministic_taxonomy(...)
           → list[DeterministicTaxonomyForChunk]
              → aggregate_emerging_taxonomy(...)
                 → list[ConceptSuggestion]  (state=NEW)
                    → add_suggestions(store, draft, ...)
```

What's a "candidate"
--------------------

A concept that appears in at least ``min_frequency`` distinct chunks
(default 2) becomes a candidate. Single-chunk concepts are dropped —
they're often noise from one-off terms or names, and the corpus
signal we care about is "this recurs". Operators tune ``min_frequency``
when their corpus is small (raise to 1 for "include singletons") or
large (raise to 5+ to surface only strong signals).

``top_n`` caps the result list ranked by ``(frequency, kind weight,
text length)``: high frequency wins first, then more-structural
kinds (heading_anchor > standard > acronym > noun_phrase > ner_candidate
> keyword), then longer texts (multi-word terms are usually more
informative than single tokens).

The output ``ConceptSuggestion`` rows are deterministic — repeated
runs on the same input produce byte-identical results — so a future
caching layer can dedupe across drafts without surprises.

Kind handling
-------------

The aggregator preserves the *first-observed* :attr:`kind` per
concept (sorted by ``(kind weight desc, chunk_id asc)``). When the
same text surfaces as both ``keyword`` and ``noun_phrase``, the
noun_phrase wins because it's the more structural signal. A future
revision could emit one suggestion per (kind, text) pair instead of
collapsing — slice 1.3 (LLM allocation) reads ``ConceptSuggestion``
verbatim, so the choice locks in what the prompt sees.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final

from app.schemas.deterministic_taxonomy import (
    DeterministicTaxonomyConcept,
    DeterministicTaxonomyForChunk,
)
from app.schemas.taxonomy_version import (
    ConceptSuggestion,
    ConceptSuggestionSource,
)

# Per-kind weight used when ranking candidates. Higher = more
# structural / decision-load-bearing for taxonomy authorship. The
# deterministic extractor (slice 1.1) emits these kinds; the
# weights here govern which one wins when a concept surfaces under
# multiple kinds. Operators can override via ``aggregate_emerging_taxonomy(kind_weights=…)``.
_DEFAULT_KIND_WEIGHTS: Final[dict[str, int]] = {
    "heading_anchor": 6,
    "standard": 5,
    "acronym": 4,
    "noun_phrase": 3,
    "ner_candidate": 2,
    "keyword": 1,
}

_DEFAULT_MIN_FREQUENCY: Final[int] = 2
_DEFAULT_TOP_N: Final[int | None] = None


# ─── Internal accumulator ──────────────────────────────────────────────


@dataclass
class _CandidateAccumulator:
    """Running aggregate for one normalized (kind, text) bucket.

    Tracks every chunk that contributed to the bucket so the eventual
    :class:`ConceptSuggestion` carries the evidence chunk ids the
    chunk-inspector (slice 1.13) renders.
    """

    kind: str
    canonical_text: str  # The casing variant we'll surface on the suggestion
    evidence_chunk_ids: set[str] = field(default_factory=set)
    confidences: list[float] = field(default_factory=list)
    # Per-casing-variant frequency so we can pick the most-common
    # casing as the canonical display form when concepts surface in
    # mixed case across chunks.
    _casing_counts: dict[str, int] = field(default_factory=dict)

    def absorb(self, concept: DeterministicTaxonomyConcept, *, chunk_id: str) -> None:
        self.evidence_chunk_ids.add(chunk_id)
        self.confidences.append(concept.confidence)
        self._casing_counts[concept.text] = self._casing_counts.get(concept.text, 0) + 1
        # Re-elect the canonical casing: most-frequent first; ties
        # break alphabetically so the result is deterministic across
        # PYTHONHASHSEED variations.
        best = max(self._casing_counts.items(), key=lambda kv: (kv[1], -ord(kv[0][0:1] or "\x7f")))
        self.canonical_text = best[0]

    @property
    def frequency(self) -> int:
        return len(self.evidence_chunk_ids)

    @property
    def average_confidence(self) -> float:
        if not self.confidences:
            return 0.0
        return sum(self.confidences) / len(self.confidences)


# ─── Public surface ────────────────────────────────────────────────────


def aggregate_emerging_taxonomy(
    chunks: Iterable[DeterministicTaxonomyForChunk],
    *,
    min_frequency: int = _DEFAULT_MIN_FREQUENCY,
    top_n: int | None = _DEFAULT_TOP_N,
    source: ConceptSuggestionSource = "extractor",
    kind_weights: dict[str, int] | None = None,
) -> list[ConceptSuggestion]:
    """Aggregate per-chunk deterministic taxonomies into candidate :class:`ConceptSuggestion` rows.

    Parameters
    ----------
    chunks
        One or more per-chunk projections from
        :func:`extract_deterministic_taxonomy`. Order doesn't matter;
        deduplication is by ``(kind, text.lower())`` across the whole
        iterable.
    min_frequency
        A concept must appear in at least this many distinct chunks to
        become a candidate. Defaults to 2 (drops singletons).
    top_n
        When set, keep only the top-N candidates ranked by
        ``(frequency desc, kind_weight desc, len(text) desc, text asc)``.
        ``None`` means "return everything that passed the frequency
        floor".
    source
        Tagged onto every emitted :class:`ConceptSuggestion`. Defaults
        to ``"extractor"``; corpus aggregators that LATER replay these
        as fresh suggestions in a different context can override.
    kind_weights
        Override the per-kind weights used to break ties when a
        concept surfaces under multiple kinds. Falls back to
        :data:`_DEFAULT_KIND_WEIGHTS`.

    Returns
    -------
    Ordered list of :class:`ConceptSuggestion` rows in their default
    construction state (``NEW``). Caller threads them onto a DRAFT
    :class:`TaxonomyVersion` via
    :func:`app.services.taxonomy_version_store.add_suggestions`.
    """
    if min_frequency < 1:
        raise ValueError(f"min_frequency must be >= 1; got {min_frequency}.")
    if top_n is not None and top_n < 1:
        raise ValueError(f"top_n must be >= 1 when set; got {top_n}.")
    weights = dict(kind_weights or _DEFAULT_KIND_WEIGHTS)

    # Pass 1 — for each (kind, normalized text), build one
    # accumulator absorbing every observation across the corpus.
    accumulators: dict[tuple[str, str], _CandidateAccumulator] = {}
    for chunk in chunks:
        for concept in chunk.concepts:
            normalized = concept.text.strip().lower()
            if not normalized:
                continue
            key = (concept.kind, normalized)
            if key not in accumulators:
                accumulators[key] = _CandidateAccumulator(
                    kind=concept.kind,
                    canonical_text=concept.text,
                )
            accumulators[key].absorb(concept, chunk_id=chunk.chunk_id)

    # Pass 2 — collapse cross-kind duplicates. ``Battery Thermal`` as
    # both keyword and noun_phrase should produce ONE suggestion with
    # the higher-weight kind winning.
    by_normalized: dict[str, list[_CandidateAccumulator]] = defaultdict(list)
    for (_, normalized), acc in accumulators.items():
        by_normalized[normalized].append(acc)

    survivors: list[_CandidateAccumulator] = []
    for candidates in by_normalized.values():
        # Sum frequency across kinds: if it's a keyword in 2 chunks
        # AND a noun_phrase in 1 different chunk, that's 3 distinct
        # evidence chunks (most of the time — overlap is rare in
        # practice, but the set union below handles it correctly).
        union_evidence: set[str] = set()
        for c in candidates:
            union_evidence |= c.evidence_chunk_ids
        if len(union_evidence) < min_frequency:
            continue
        # Promote the kind with the highest weight; tie-break alpha.
        winning = max(
            candidates,
            key=lambda c: (weights.get(c.kind, 0), c.kind),
        )
        # Re-seed the winning accumulator with the union evidence +
        # union confidences so the emitted suggestion reflects the
        # full corpus signal, not just the winning kind's bucket.
        winning.evidence_chunk_ids = union_evidence
        winning.confidences = [conf for c in candidates for conf in c.confidences]
        survivors.append(winning)

    # Rank: (frequency desc, kind_weight desc, len(text) desc, text asc).
    survivors.sort(
        key=lambda c: (
            -c.frequency,
            -weights.get(c.kind, 0),
            -len(c.canonical_text),
            c.canonical_text.lower(),
        )
    )
    if top_n is not None:
        survivors = survivors[:top_n]

    return [
        ConceptSuggestion(
            label=acc.canonical_text,
            description=(
                f"Emerging candidate detected by the deterministic extractor "
                f"as a ``{acc.kind}`` in {acc.frequency} chunks "
                f"(avg confidence {acc.average_confidence:.2f}). Promote, "
                f"merge into an existing category, or reject."
            ),
            source=source,
            confidence=acc.average_confidence,
            evidence_chunk_ids=sorted(acc.evidence_chunk_ids),
        )
        for acc in survivors
    ]


__all__ = [
    "aggregate_emerging_taxonomy",
]
