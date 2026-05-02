"""Deterministic chunk relation service (Demo KG #141).

Turns the validated ``SemanticSection`` list of a ``SemanticDocument``
into explainable chunk-to-chunk relations without touching an LLM or a
graph database. Relations land as ``ChunkRelation`` records that the
projector (lane A, #144) wraps into ``GraphEdge`` instances of kind
``related_to`` / ``shares_keyword`` / ``same_topic_as``, populating
``ChunkRelationEdgeProperties`` from the values here plus the
``document_id`` / ``version_id`` it knows from its caller.

Determinism is the load-bearing property: the same input always
produces the same output, in the same order, with the same scores. The
graph-quality smoke assertions in lane C (#146) rely on this — the
seed document set must produce the exact relation set the demo
expects.

Algorithm
---------

1. **Tokenize** each chunk's ``text`` into lowercase alphanumeric
   tokens of length ≥ 3, dropping a small bilingual stopword list.
2. **Keywords** are the top ``_KEYWORD_LIMIT`` tokens by frequency,
   ties broken alphabetically.
3. **Standards** are matched by regex against the original lowercased
   text — ``ISO 9001``, ``IEC 61508``, etc. — and normalized to a
   canonical ``"<body>-<number>"`` form so ``ISO 9001`` and ``iso-9001``
   collapse to the same key.
4. For every chunk pair ``(i, j)`` with ``i.id < j.id`` we compute:
   - ``token_jaccard`` over the full tokenset (dedup catches
     near-duplicates),
   - ``keyword_jaccard`` over the keyword set,
   - ``shared_keywords`` and ``shared_standards``.
5. The first matching rule wins:
   - ``token_jaccard ≥ _NEAR_DUPLICATE_THRESHOLD`` (0.8) →
     ``related_to``.
   - any shared standard → ``shares_keyword`` (standards-flavoured).
   - ``≥ _SAME_TOPIC_MIN_KEYWORDS`` shared keywords AND
     ``keyword_jaccard ≥ _SAME_TOPIC_MIN_JACCARD`` → ``same_topic_as``.
   - any shared keyword → ``shares_keyword`` (generic).
   - otherwise no edge.

Every emitted relation carries non-empty ``shared_keywords`` and a
non-empty ``reason`` — the v0.2 audit-trail contract from
``docs/architecture/knowledge_graph_payload.md`` (lane C asserts this).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel
from app.schemas.semantic_document import SemanticDocument, SemanticSection

ChunkRelationKind = Literal["related_to", "shares_keyword", "same_topic_as"]


# Tunables. Module-level so tests can monkeypatch and lane C fixtures
# can reason about expected output without reading the algorithm.
_KEYWORD_LIMIT = 20
_NEAR_DUPLICATE_THRESHOLD = 0.8
_SAME_TOPIC_MIN_KEYWORDS = 3
# Calibrated against realistic paragraph-length sections: with ~15
# distinct keywords each, three shared keywords lands around 0.2–0.3
# Jaccard. Below that floor the two chunks are "incidentally adjacent",
# above it they're talking about the same thing.
_SAME_TOPIC_MIN_JACCARD = 0.3
_MIN_TOKEN_LENGTH = 3
_REASON_KEYWORD_PREVIEW = 5

# A small bilingual stopword list. The demo dataset is mixed FR/EN, so
# we cover both. Keep the list short; over-aggressive stopwording hides
# meaningful overlap (e.g. "process", "system" are domain-bearing in
# this codebase).
_STOPWORDS: frozenset[str] = frozenset(
    {
        # English
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "with",
        "from",
        "this",
        "that",
        "have",
        "has",
        "had",
        "was",
        "were",
        "been",
        "being",
        "into",
        "than",
        "then",
        "they",
        "their",
        "there",
        "these",
        "those",
        "such",
        "shall",
        "must",
        "should",
        "may",
        "can",
        "will",
        "would",
        "could",
        "also",
        "any",
        "all",
        "each",
        "more",
        "most",
        "some",
        "other",
        "where",
        "when",
        "what",
        "which",
        "while",
        "after",
        "before",
        "between",
        "within",
        "without",
        "upon",
        "about",
        "above",
        "below",
        "over",
        "under",
        "section",
        "chapter",
        "page",
        "table",
        "figure",
        # French
        "les",
        "des",
        "une",
        "aux",
        "ces",
        "ses",
        "leur",
        "leurs",
        "dans",
        "pour",
        "par",
        "avec",
        "sans",
        "sur",
        "sous",
        "entre",
        "vers",
        "mais",
        "donc",
        "puis",
        "ainsi",
        "selon",
        "afin",
        "lors",
        "dont",
        "quand",
        "aussi",
        "comme",
        "tout",
        "tous",
        "toute",
        "toutes",
        "cette",
        "celui",
        "celle",
        "ceux",
        "celles",
        "elle",
        "elles",
        "nous",
        "vous",
        "ils",
        "qui",
        "que",
        "quoi",
        "est",
        "sont",
        "etait",
        "ete",
        "avoir",
        "etre",
        "fait",
        "faire",
        "doit",
        "doivent",
    }
)

# Standards bodies whose numbered references are domain-load-bearing
# overlap signals. Match e.g. ``ISO 9001``, ``ISO-9001``, ``iso 9001:2015``.
# We keep the suffix (``:2015``) when present; "ISO 9001" and "ISO 9001:2015"
# stay distinct because they often refer to different revisions.
_STANDARDS_PATTERN = re.compile(
    r"\b(iso|iec|astm|en|nf|din|cei|api|ansi|ieee)[\s\-]*(\d+(?:[-:]\d+)?)\b",
    flags=re.IGNORECASE,
)

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ChunkRecord:
    """Derived form of a :class:`SemanticSection` used by the relation
    service. ``chunk_id`` matches the section id 1:1 today (per the
    Demo-KG contract, lane A's ``ChunkNodeProperties.section_id``); a
    future split-section-into-chunks pass would relax that.
    """

    chunk_id: str
    section_id: str
    heading: str
    text: str
    char_count: int
    keywords: tuple[str, ...]
    tokens: frozenset[str] = field(repr=False)
    standards: frozenset[str] = field(repr=False)


class ChunkRelation(BaseModel):
    """Output of :meth:`ChunkRelationService.relations_for`.

    Carries the edge kind plus the deterministic-edge audit trail
    fields from :class:`ChunkRelationEdgeProperties` (sans
    ``document_id`` / ``version_id``, which lane A's projector splices
    in at edge-construction time). Order of fields matches the property
    model so downstream code can ``relation.model_dump()`` and merge.
    """

    kind: ChunkRelationKind
    source_chunk_id: str
    target_chunk_id: str
    score: float = Field(ge=0.0, le=1.0)
    reason: str
    shared_keywords: list[str]


class ChunkRelationService:
    """Stateless, deterministic local service. Construction is cheap;
    the projector instantiates one per call to keep the wiring simple.
    """

    def chunks_for(self, semantic: SemanticDocument) -> list[ChunkRecord]:
        """Project every :class:`SemanticSection` in ``semantic`` to a
        :class:`ChunkRecord`. Order matches ``semantic.sections`` so
        downstream consumers can rely on it.
        """
        return [self._record_for(section) for section in semantic.sections]

    def relations_for(self, chunks: Sequence[ChunkRecord]) -> list[ChunkRelation]:
        """Pairwise scan over ``chunks`` emitting one relation per pair
        that crosses a rule's threshold. Output is sorted by
        ``(source_chunk_id, target_chunk_id, kind)`` so the result is
        byte-stable across runs.
        """
        relations: list[ChunkRelation] = []
        for left, right in _ordered_pairs(chunks):
            relation = self._classify_pair(left, right)
            if relation is not None:
                relations.append(relation)
        relations.sort(key=lambda r: (r.source_chunk_id, r.target_chunk_id, r.kind))
        return relations

    def _record_for(self, section: SemanticSection) -> ChunkRecord:
        text = section.text or ""
        tokens = _tokenize(text)
        keywords = _top_keywords(tokens, limit=_KEYWORD_LIMIT)
        standards = _extract_standards(text)
        return ChunkRecord(
            chunk_id=section.id,
            section_id=section.id,
            heading=section.heading,
            text=text,
            char_count=len(text),
            keywords=keywords,
            tokens=frozenset(tokens),
            standards=standards,
        )

    def _classify_pair(self, left: ChunkRecord, right: ChunkRecord) -> ChunkRelation | None:
        # Canonicalize ordering — lower id wins ``source`` so the same
        # unordered pair always produces the same directed edge id.
        if left.chunk_id > right.chunk_id:
            left, right = right, left

        left_kw = set(left.keywords)
        right_kw = set(right.keywords)
        shared_keywords = sorted(left_kw & right_kw)
        shared_standards = sorted(left.standards & right.standards)

        token_jaccard = _jaccard(left.tokens, right.tokens)
        keyword_jaccard = _jaccard(left_kw, right_kw)

        # Near-duplicate. We still require at least one shared keyword
        # so the audit-trail contract (non-empty ``shared_keywords``)
        # holds; in practice near-duplicate texts always share keywords.
        if token_jaccard >= _NEAR_DUPLICATE_THRESHOLD and shared_keywords:
            return ChunkRelation(
                kind="related_to",
                source_chunk_id=left.chunk_id,
                target_chunk_id=right.chunk_id,
                score=round(token_jaccard, 4),
                reason=f"Near-duplicate text (token Jaccard={token_jaccard:.2f}).",
                shared_keywords=shared_keywords,
            )

        # Shared standard reference is a strong, explainable signal —
        # surface it before generic keyword overlap so the inspector can
        # render "both cite ISO 9001" verbatim.
        if shared_standards:
            label = ", ".join(_format_standard(s) for s in shared_standards)
            audit_keywords = sorted({*shared_standards, *shared_keywords})
            return ChunkRelation(
                kind="shares_keyword",
                source_chunk_id=left.chunk_id,
                target_chunk_id=right.chunk_id,
                score=min(1.0, 0.5 + 0.1 * len(shared_standards)),
                reason=f"Both reference standard(s): {label}.",
                shared_keywords=audit_keywords,
            )

        # Same topic — multiple shared keywords with substantial
        # overlap. Reason previews the first few shared keywords so the
        # inspector explanation is concrete.
        if (
            len(shared_keywords) >= _SAME_TOPIC_MIN_KEYWORDS
            and keyword_jaccard >= _SAME_TOPIC_MIN_JACCARD
        ):
            preview = ", ".join(shared_keywords[:_REASON_KEYWORD_PREVIEW])
            return ChunkRelation(
                kind="same_topic_as",
                source_chunk_id=left.chunk_id,
                target_chunk_id=right.chunk_id,
                score=round(keyword_jaccard, 4),
                reason=f"Share {len(shared_keywords)} topic keywords: {preview}.",
                shared_keywords=shared_keywords,
            )

        if shared_keywords:
            preview = ", ".join(shared_keywords[:_REASON_KEYWORD_PREVIEW])
            return ChunkRelation(
                kind="shares_keyword",
                source_chunk_id=left.chunk_id,
                target_chunk_id=right.chunk_id,
                score=round(keyword_jaccard, 4),
                reason=f"Share keyword(s): {preview}.",
                shared_keywords=shared_keywords,
            )

        return None


def _tokenize(text: str) -> list[str]:
    """Lowercase, alphanumeric-only, length ≥ ``_MIN_TOKEN_LENGTH``,
    stopwords removed. Returns a list (keeps duplicates) so callers can
    pass it to ``Counter`` for keyword ranking.
    """
    return [
        token
        for token in _TOKEN_PATTERN.findall(text.lower())
        if len(token) >= _MIN_TOKEN_LENGTH and token not in _STOPWORDS
    ]


def _top_keywords(tokens: Iterable[str], *, limit: int) -> tuple[str, ...]:
    """Most frequent tokens, ties broken alphabetically so the result
    is deterministic across Python hash seeds.
    """
    counts = Counter(tokens)
    if not counts:
        return ()
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(token for token, _ in ranked[:limit])


def _extract_standards(text: str) -> frozenset[str]:
    """Pull ``ISO 9001``-style references out of ``text`` and return
    them as a canonical ``"<body>-<number>"`` set.
    """
    matches = _STANDARDS_PATTERN.findall(text)
    return frozenset(f"{body.lower()}-{number}" for body, number in matches)


def _format_standard(canonical: str) -> str:
    """Pretty-print a canonical ``iso-9001`` form back as ``ISO 9001``
    for the human-readable ``reason`` field.
    """
    body, _, number = canonical.partition("-")
    return f"{body.upper()} {number}" if number else canonical.upper()


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 0.0
    intersection = len(left_set & right_set)
    union = len(left_set | right_set)
    return intersection / union if union else 0.0


def _ordered_pairs(
    chunks: Sequence[ChunkRecord],
) -> Iterable[tuple[ChunkRecord, ChunkRecord]]:
    for i, left in enumerate(chunks):
        for right in chunks[i + 1 :]:
            yield left, right
