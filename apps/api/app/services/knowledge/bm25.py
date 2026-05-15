"""In-memory BM25 keyword index for hybrid retrieval (EPIC-4 item 4.3).

BM25 is the standard keyword-relevance scorer for IR. It complements
the vector retrieval shipped in Phase 3 (ADR-015) by giving
keyword-heavy queries — acronyms, exact phrases, named entities — a
direct path to the chunks that contain those tokens. Dense embeddings
collapse synonyms onto similar vectors but underweight rare-but-exact
tokens; BM25 has the inverse trade-off.

Scope:

- Pure-Python implementation, no external dependency. ~80 lines + tests.
- In-memory index — rebuild on each ``KnowledgeProjector.project_chunks``
  cycle when wired (out of scope for this module; this is the
  primitive a future ``HybridSearchService`` composes with).
- No stemming, no stopword removal, no language detection. The
  IDF term naturally downweights frequent tokens; stemming /
  stopwords are language-specific and out of MVP scope.

Default parameters follow Robertson 2009's recommendations:

- ``k1 = 1.5`` — term-frequency saturation
- ``b  = 0.75`` — length-normalisation weight

These are the same defaults Elasticsearch and Lucene ship with;
operators who want to retune can pass them through the constructor.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

# Match runs of alphanumerics (Unicode aware). Anything else is a
# separator: punctuation, whitespace, control chars. Single-char
# tokens are kept by default because BM25's IDF naturally downweights
# them; callers who want strict 2+ filtering can override via
# ``min_token_length``.
_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str, *, min_token_length: int = 1) -> list[str]:
    """Lowercase + split a query / document into a list of tokens.

    Pure function — exposed at module level so the hybrid retrieval
    layer can tokenize a query the same way the index does without
    constructing a temporary :class:`BM25Index`.
    """
    return [
        m.group(0).lower()
        for m in _TOKEN_PATTERN.finditer(text)
        if len(m.group(0)) >= min_token_length
    ]


@dataclass(frozen=True)
class BM25Hit:
    """One hit from :meth:`BM25Index.search`.

    Mirrors the shape of :class:`ChunkSearchHit` so a hybrid retriever
    can merge BM25 + vector results without translating types. ``score``
    is the raw BM25 score (not normalised); rank fusion strategies
    that need ``[0, 1]`` should normalise themselves.
    """

    chunk_id: str
    score: float


class BM25Index:
    """In-memory BM25 keyword index over a chunk corpus.

    Construction takes one pass over the corpus to build:

    - ``doc_lengths``      — token count per chunk
    - ``doc_term_freqs``   — Counter of token → freq, per chunk
    - ``doc_frequency``    — global Counter of token → number of
                              chunks the token appears in
    - ``avg_doc_length``   — mean ``doc_lengths`` value

    Query-time cost is ``O(|tokens in query|)`` per scored chunk
    because the scoring loop walks query tokens, not corpus tokens.
    Empty corpora produce an empty result; an empty query raises
    :class:`ValueError` so the caller's bug doesn't silently return
    nothing.
    """

    def __init__(
        self,
        chunks: Iterable[tuple[str, str]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
        min_token_length: int = 1,
    ) -> None:
        if k1 < 0:
            raise ValueError(f"k1 must be >= 0; got {k1}.")
        if not 0.0 <= b <= 1.0:
            raise ValueError(f"b must be in [0, 1]; got {b}.")
        self._k1 = k1
        self._b = b
        self._min_token_length = min_token_length

        self._doc_lengths: dict[str, int] = {}
        self._doc_term_freqs: dict[str, Counter[str]] = {}
        self._doc_frequency: Counter[str] = Counter()
        for chunk_id, text in chunks:
            if chunk_id in self._doc_term_freqs:
                raise ValueError(
                    f"duplicate chunk_id in index input: {chunk_id!r}"
                )
            tokens = tokenize(text, min_token_length=min_token_length)
            self._doc_lengths[chunk_id] = len(tokens)
            tf = Counter(tokens)
            self._doc_term_freqs[chunk_id] = tf
            # Document frequency: the number of *chunks* the token
            # appears in, not the total occurrences.
            for term in tf:
                self._doc_frequency[term] += 1

        self._num_docs = len(self._doc_lengths)
        self._avg_doc_length = (
            sum(self._doc_lengths.values()) / self._num_docs
            if self._num_docs
            else 0.0
        )

    @property
    def num_docs(self) -> int:
        return self._num_docs

    @property
    def avg_doc_length(self) -> float:
        return self._avg_doc_length

    def _idf(self, term: str) -> float:
        """Inverse document frequency for one term (Robertson smoothed).

        Returns ``log((N - df + 0.5) / (df + 0.5) + 1)``. The ``+1``
        inside ``log`` keeps the IDF non-negative even when ``df > N/2``
        (which would otherwise produce a negative weight that pushes
        common terms toward zero rather than just downweighting them).
        Mirrors Lucene/Elasticsearch's BM25Similarity implementation.
        """
        df = self._doc_frequency.get(term, 0)
        return math.log((self._num_docs - df + 0.5) / (df + 0.5) + 1.0)

    def score(self, query: str, chunk_id: str) -> float:
        """Return the BM25 score of ``chunk_id`` against ``query``.

        Returns ``0.0`` when the chunk isn't in the index — the caller
        decides whether to surface that as a miss or filter out.
        """
        tf_doc = self._doc_term_freqs.get(chunk_id)
        if tf_doc is None:
            return 0.0
        if not query or not query.strip():
            raise ValueError("query must not be empty.")
        doc_len = self._doc_lengths[chunk_id]
        denom_norm = 1.0 - self._b + self._b * (
            doc_len / self._avg_doc_length if self._avg_doc_length else 1.0
        )
        total = 0.0
        for term in tokenize(query, min_token_length=self._min_token_length):
            tf = tf_doc.get(term, 0)
            if tf == 0:
                continue
            idf = self._idf(term)
            numerator = tf * (self._k1 + 1.0)
            denominator = tf + self._k1 * denom_norm
            total += idf * (numerator / denominator)
        return total

    def search(self, query: str, *, limit: int = 10) -> list[BM25Hit]:
        """Return the top-``limit`` hits ranked by BM25 score.

        Ties on score are broken by ``chunk_id`` ascending so the order
        is deterministic across runs. Zero-score chunks are filtered
        out — they're guaranteed to share no query token with the
        document, so surfacing them as "ranked" would be misleading.
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1; got {limit}.")
        if not query or not query.strip():
            raise ValueError("query must not be empty.")
        scored: list[tuple[float, str]] = []
        for chunk_id in self._doc_term_freqs:
            s = self.score(query, chunk_id)
            if s > 0.0:
                scored.append((s, chunk_id))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [BM25Hit(chunk_id=cid, score=s) for s, cid in scored[:limit]]


__all__ = ["BM25Hit", "BM25Index", "tokenize"]
