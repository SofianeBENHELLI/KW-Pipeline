"""Deterministic chunk-relation extraction for the Demo KG (#141).

Reads the sections of a validated :class:`SemanticDocument` (treated as
chunks 1:1 today) and emits typed
:class:`~app.schemas.knowledge.ChunkRelationEdgeProperties` records that
the projector flattens into ``related_to`` / ``shares_keyword`` /
``same_topic_as`` edges.

The service is intentionally explainable:

- **No LLM, no Anthropic key**, no Neo4j driver — pure stdlib. The
  whole knowledge layer must keep working with
  ``KW_KNOWLEDGE_LAYER_ENABLED=false`` and no network.
- **Deterministic.** Same input ⇒ same output, byte-for-byte. Tests
  rely on this (and so does the demo seed). The pair iteration order
  is sorted, the keyword extraction is lowercased + stop-listed, and
  no random sampling enters the loop.
- **Auditable.** Every emitted relation carries the ``source_chunk_id``
  / ``target_chunk_id`` pair, the ``shared_keywords`` that triggered it,
  a ``score`` in ``[0.0, 1.0]``, and a ``reason`` string surfaced
  verbatim in the inspector — the parallel-to-ADR-012-§4 audit trail
  documented in ``docs/architecture/knowledge_graph_payload.md``.

The relation kinds emitted here are a subset of the v0.2 contract:

- ``shares_keyword`` — at least
  :attr:`ChunkRelationConfig.shared_keyword_min` shared keywords.
- ``related_to`` — Jaccard similarity above
  :attr:`ChunkRelationConfig.related_to_jaccard_min`. Re-emitted at
  the higher ``near_duplicate_jaccard_min`` threshold with a
  "near-duplicate" reason so downstream consumers / topic clustering
  can tell the strong ties from the weak ones.

``same_topic_as`` is **deferred to the topic-clustering service**
(#142). The contract calls this out as an open question; emitting it
here would race with the cluster-aware version, and the projector
(#143/#144) is the natural place to derive ``same_topic_as`` once
``belongs_to`` edges are known. The service signature stays open
to add it later without a breaking change.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass

from app.schemas.knowledge import ChunkRelationEdgeProperties
from app.schemas.semantic_document import SemanticSection

log = logging.getLogger(__name__)


# A short, opinionated English stop list. We deliberately do NOT pull
# nltk / spacy as a dependency for this — see AGENTS.md rule #11 and
# the issue brief. The list is small enough to audit by eye and the
# tests pin its effect on relation scoring.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "aren",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "cannot",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "don",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "let",
        "me",
        "might",
        "more",
        "most",
        "must",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "ought",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "shall",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
    }
)

# Tokenization regex: any run of word characters (letters/digits/_)
# becomes a candidate token. Locale-naive on purpose; the demo input is
# English. Apostrophes split words ("don't" → "don" + "t") which is
# fine for this kind of similarity work.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class ChunkRelationConfig:
    """Tunable thresholds for the deterministic relation service.

    Defaults are picked so the curated demo fixtures (lane C, #145/#146)
    produce a reasonable mix of ``shares_keyword`` and ``related_to``
    edges. Override per-call when the test wants to exercise the edge
    cases.
    """

    # How many keywords per chunk to retain after stop-list / stemming.
    # Higher → more shared overlap candidates, more edges.
    top_n_keywords: int = 20
    # Minimum tokens-after-stem length kept as a keyword. Drops "a",
    # "be", "we", and most function words even if the stop list misses
    # them.
    min_keyword_length: int = 3
    # Emit ``shares_keyword`` when at least this many keywords overlap.
    shared_keyword_min: int = 2
    # Emit ``related_to`` when Jaccard similarity ≥ this.
    related_to_jaccard_min: float = 0.5
    # Re-emit ``related_to`` with a "near-duplicate" reason at this
    # higher threshold so downstream consumers can distinguish strong
    # ties.
    near_duplicate_jaccard_min: float = 0.85


class ChunkRelationService:
    """Pairwise deterministic relation extractor over chunk text.

    The service is stateless; the constructor only takes a
    :class:`ChunkRelationConfig`. Callers that need different thresholds
    construct a second instance.
    """

    def __init__(self, config: ChunkRelationConfig | None = None) -> None:
        self._config = config or ChunkRelationConfig()

    @property
    def config(self) -> ChunkRelationConfig:
        return self._config

    # ------------------------------------------------------------------
    # Keyword extraction
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> list[str]:
        """Lowercase, split on non-word, drop stop-words and short tokens.

        Returns tokens in input order, including duplicates — the caller
        decides whether to count or set-ify.
        """
        cfg = self._config
        out: list[str] = []
        for raw in _TOKEN_RE.findall(text):
            tok = raw.lower()
            if len(tok) < cfg.min_keyword_length:
                continue
            if tok in _STOPWORDS:
                continue
            # Very simple stemming: trailing 's' for plurals, 'es' for a
            # subset, 'ing' / 'ed'. Real stemming (Porter) is overkill
            # for the pairwise overlap signal and would add a dep.
            stemmed = _stem(tok)
            if len(stemmed) < cfg.min_keyword_length:
                continue
            out.append(stemmed)
        return out

    def keywords_for(self, section: SemanticSection) -> list[str]:
        """Top-N keywords for a chunk, ordered by frequency then alpha.

        Public for testability and so the projector can attach the
        per-chunk keyword list to ``ChunkNodeProperties.keywords``
        without re-tokenizing.
        """
        tokens = self._tokenize(section.text or "")
        if not tokens:
            return []
        counts = Counter(tokens)
        # Sort by (-count, token) to make the order deterministic when
        # frequencies tie. Everything downstream depends on this.
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [tok for tok, _ in ordered[: self._config.top_n_keywords]]

    # ------------------------------------------------------------------
    # Relation extraction
    # ------------------------------------------------------------------

    def extract_relations(
        self,
        sections: list[SemanticSection],
        *,
        document_id: str = "",
        version_id: str = "",
    ) -> list[ChunkRelationEdgeProperties]:
        """Emit all pairwise relations between the given chunks.

        Args:
            sections: validated semantic sections (chunks 1:1 today).
            document_id: passed through into each emitted record's
                ``document_id`` property. The relation service has no
                way to derive this on its own; the projector or test
                supplies it. Defaults to ``""`` so unit tests that only
                care about the relation shape can omit it.
            version_id: see ``document_id``.

        Returns:
            A deterministic list of typed property records. The
            projector flattens each via ``record.model_dump()`` to fill
            :attr:`GraphEdge.properties`. Order: ``(source_chunk_id,
            target_chunk_id)`` lex-sorted, with ``shares_keyword``
            emitted before ``related_to`` for any given pair so callers
            iterating naïvely see the lighter-weight edge first.
        """
        if len(sections) < 2:
            return []

        # Build per-chunk keyword sets once. Sets, not lists: relation
        # logic is overlap/Jaccard, frequencies don't matter past the
        # top-N truncation already applied in keywords_for.
        keyword_sets: dict[str, set[str]] = {}
        # Stable order over chunks. Sorted by id so the loop output is
        # deterministic regardless of caller's section order.
        sorted_sections = sorted(sections, key=lambda s: s.id)
        for section in sorted_sections:
            keyword_sets[section.id] = set(self.keywords_for(section))

        emitted: list[ChunkRelationEdgeProperties] = []
        for i, left in enumerate(sorted_sections):
            for right in sorted_sections[i + 1 :]:
                left_kws = keyword_sets[left.id]
                right_kws = keyword_sets[right.id]
                shared = left_kws & right_kws
                if not shared:
                    # Empty overlap: the chunks are unrelated under this
                    # signal. Skip — emitting a zero-score edge would
                    # bloat the graph and confuse the inspector.
                    continue

                jaccard = _jaccard(left_kws, right_kws)
                pair_relations = self._relations_for_pair(
                    source_id=left.id,
                    target_id=right.id,
                    shared=shared,
                    jaccard=jaccard,
                    document_id=document_id,
                    version_id=version_id,
                )
                emitted.extend(pair_relations)

        log.debug(
            "knowledge.chunk_relations.extracted",
            extra={
                "document_id": document_id,
                "version_id": version_id,
                "chunk_count": len(sections),
                "relation_count": len(emitted),
            },
        )
        return emitted

    def _relations_for_pair(
        self,
        *,
        source_id: str,
        target_id: str,
        shared: set[str],
        jaccard: float,
        document_id: str,
        version_id: str,
    ) -> list[ChunkRelationEdgeProperties]:
        cfg = self._config
        # Shared keywords get a deterministic order for ``reason`` /
        # ``shared_keywords`` rendering.
        shared_sorted = sorted(shared)

        relations: list[ChunkRelationEdgeProperties] = []

        # ``shares_keyword`` is the lighter-weight signal. We emit it
        # whenever the overlap clears the floor — even if Jaccard is
        # also above ``related_to_jaccard_min`` — so the inspector can
        # show "these two share these keywords" alongside the heavier
        # relation. Both edges carry the same audit trail.
        if len(shared) >= cfg.shared_keyword_min:
            relations.append(
                _build(
                    document_id=document_id,
                    version_id=version_id,
                    source=source_id,
                    target=target_id,
                    score=_keyword_score(len(shared), cfg),
                    reason=_shares_keyword_reason(shared_sorted),
                    shared=shared_sorted,
                )
            )

        if jaccard >= cfg.related_to_jaccard_min:
            if jaccard >= cfg.near_duplicate_jaccard_min:
                reason = f"near-duplicate keyword overlap (Jaccard {jaccard:.2f})"
            else:
                reason = f"high keyword overlap (Jaccard {jaccard:.2f})"
            relations.append(
                _build(
                    document_id=document_id,
                    version_id=version_id,
                    source=source_id,
                    target=target_id,
                    # The Jaccard already lives in [0, 1]; clamp the
                    # ``score`` field to the same range. We do NOT take
                    # max(jaccard, keyword_score): the related_to score
                    # is meant to *be* the Jaccard so consumers can sort
                    # by similarity directly.
                    score=round(jaccard, 4),
                    reason=reason,
                    shared=shared_sorted,
                    kind_hint="related_to",
                )
            )

        return relations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build(
    *,
    document_id: str,
    version_id: str,
    source: str,
    target: str,
    score: float,
    reason: str,
    shared: list[str],
    kind_hint: str = "shares_keyword",  # noqa: ARG001
) -> ChunkRelationEdgeProperties:
    """Construct a property record. ``kind_hint`` is unused today —
    kept on the signature so the projector can switch on it once the
    edge ``kind`` lives on the property record (currently it's the
    edge's own field, not the property's). Documented for #143."""
    return ChunkRelationEdgeProperties(
        document_id=document_id,
        version_id=version_id,
        source_chunk_id=source,
        target_chunk_id=target,
        score=score,
        reason=reason,
        shared_keywords=shared,
    )


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity over two sets. Empty ∪ empty returns 0.0."""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _keyword_score(shared_count: int, cfg: ChunkRelationConfig) -> float:
    """Map a shared-keyword count to ``[0.0, 1.0]``.

    Linear up to ``top_n_keywords``; saturates after that. Two shared
    keywords against ``top_n_keywords=20`` ⇒ ``0.10``; ten shared ⇒
    ``0.50``; twenty (or more) ⇒ ``1.0``. The function is monotonic so
    consumer sort orders are stable.
    """
    if cfg.top_n_keywords <= 0:
        return 0.0
    score = shared_count / cfg.top_n_keywords
    return round(min(1.0, max(0.0, score)), 4)


def _shares_keyword_reason(shared_sorted: list[str]) -> str:
    """Human-readable reason for ``shares_keyword`` edges.

    Caps the listed keywords at four to keep the inspector tooltip
    readable; the full ``shared_keywords`` array stays on the edge so
    nothing is lost.
    """
    count = len(shared_sorted)
    sample = shared_sorted[:4]
    listed = ", ".join(sample)
    if count > len(sample):
        return f"shares {count} keywords: {listed}, …"
    return f"shares {count} keywords: {listed}"


def _stem(token: str) -> str:
    """Trivial English suffix stripper.

    Far from Porter — but Porter would be the only reason to add a
    dep, and the goal is *explainable* matching for a demo. Tests
    exercise the cases this handles: plural, -es, -ing, -ed.
    """
    if len(token) > 4 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


__all__ = [
    "ChunkRelationConfig",
    "ChunkRelationService",
]
