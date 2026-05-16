"""Hybrid retrieval — vector + BM25 via reciprocal-rank fusion (EPIC-4 item 4.3).

Combines the dense-vector ``KnowledgeSearchService`` and the
keyword-based :class:`BM25Index` into a single
``HybridSearchService.search(query, *, limit=...)`` surface that
matches :class:`KnowledgeSearchService`'s contract. Drop-in: the
:class:`app.services.knowledge.retrieval_eval._SearchLike` Protocol
the eval harness uses accepts both interchangeably, so 4.5's golden
sets can measure the spread without code changes.

Fusion strategy: **Reciprocal Rank Fusion** (RRF, Cormack 2009). For
each ranked list ``L`` we contribute ``1 / (rrf_k + rank_L(chunk))``
to the chunk's fused score, then sort by descending fused score. RRF
is parameter-light (one constant ``rrf_k``, default 60), order-only
(no need to normalise the score magnitudes across two different
ranking systems), and well-documented as a strong baseline for
hybrid retrieval. It composes more strategies later without code
churn.

Trade-offs vs the obvious alternatives:

- **Weighted sum of normalised scores** — requires a per-corpus
  calibration step (BM25 raw scores are unbounded; cosine scores are
  bounded in ``[-1, 1]``). RRF skips the calibration entirely.
- **Cross-encoder reranking** — strictly stronger but slow + needs an
  LLM call per query. That's EPIC-4 item 4.4 ("rerank step"); the
  2026-05-14 plan keeps it for S+5 because it's only worth doing
  once 4.3 + 4.5 tell us where retrieval is actually wrong.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.schemas.knowledge import ChunkSearchResponse, ChunkSearchResult
from app.services.knowledge.bm25 import BM25Index
from app.services.knowledge.search import KnowledgeSearchService

# Default ``rrf_k`` per Cormack 2009. Larger values flatten the
# fusion (rank differences matter less); smaller values amplify
# top-of-list dominance. 60 is the value most published papers
# evaluate against and matches Elasticsearch's RRF default.
_DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    *,
    rrf_k: int = _DEFAULT_RRF_K,
) -> list[tuple[str, float]]:
    """Fuse multiple ranked id lists via RRF and return ``(id, fused_score)``.

    Each list contributes ``1 / (rrf_k + 1-based-rank)`` per chunk. The
    same chunk appearing in N lists accumulates N contributions. Ties
    on the fused score break on ``chunk_id`` ascending so the result
    is deterministic.
    """
    if rrf_k < 0:
        raise ValueError(f"rrf_k must be >= 0; got {rrf_k}.")
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
    return sorted(fused.items(), key=lambda pair: (-pair[1], pair[0]))


@dataclass(frozen=True)
class _VectorHit:
    """Minimal projection of a vector hit used inside the fusion step.

    Keeping the per-vector metadata (``document_id``, ``version_id``,
    ``section_id``, ``snippet``, raw ``score``) on a side dict so the
    fusion logic only juggles ids; the metadata is folded back in when
    we build the final response.
    """

    chunk_id: str
    document_id: str
    version_id: str
    section_id: str
    snippet: str | None
    score: float


class HybridSearchService:
    """Vector + BM25 hybrid retrieval (EPIC-4 item 4.3).

    Stateless beyond the injected dependencies; the underlying
    :class:`KnowledgeSearchService` and :class:`BM25Index` are the
    only state owners. Construction does no work; ``search`` runs
    one vector retrieval + one BM25 scan per call.

    The candidate pool size is bounded by ``candidate_limit`` per
    side: we ask each retriever for that many hits, then RRF the
    combined set, then return the top ``limit``. Default
    ``candidate_limit = max(20, 4 * limit)`` keeps the per-side
    fan-out small while still giving RRF room to promote a chunk
    that ranked moderately on both lists over one that ranked top
    only on a single list.
    """

    def __init__(
        self,
        *,
        vector: KnowledgeSearchService,
        bm25: BM25Index,
        rrf_k: int = _DEFAULT_RRF_K,
        candidate_multiplier: int = 4,
        min_candidate_pool: int = 20,
    ) -> None:
        if candidate_multiplier < 1:
            raise ValueError(f"candidate_multiplier must be >= 1; got {candidate_multiplier}.")
        if min_candidate_pool < 1:
            raise ValueError(f"min_candidate_pool must be >= 1; got {min_candidate_pool}.")
        self._vector = vector
        self._bm25 = bm25
        self._rrf_k = rrf_k
        self._candidate_multiplier = candidate_multiplier
        self._min_candidate_pool = min_candidate_pool

    @property
    def embedding_model(self) -> str:
        """Mirrors :class:`KnowledgeSearchService` so the chat service +
        eval harness see a consistent label on the fused response."""
        return self._vector.embedding_model

    def search(self, query: str, *, limit: int = 5) -> ChunkSearchResponse:
        """Run both retrievers, fuse, return the top-``limit`` hits.

        Empty / whitespace-only queries raise :class:`ValueError`
        (consistent with the vector service's contract). The fused
        response carries the *vector* embedding-model label because
        the vector path is the canonical retrieval (BM25 is the
        keyword recall complement). The per-hit ``score`` field is
        the RRF fused score, not the raw vector cosine — operators
        comparing scores across hybrid vs vector-only runs should
        rely on ranks rather than score magnitudes.
        """
        if not query or not query.strip():
            raise ValueError("query must not be empty.")
        if limit < 1:
            raise ValueError(f"limit must be >= 1; got {limit}.")

        candidate_limit = max(self._min_candidate_pool, self._candidate_multiplier * limit)

        vector_response = self._vector.search(query, limit=candidate_limit)
        bm25_hits = self._bm25.search(query, limit=candidate_limit)
        vector_ids = [hit.chunk_id for hit in vector_response.results]
        bm25_ids = [hit.chunk_id for hit in bm25_hits]
        fused = reciprocal_rank_fusion([vector_ids, bm25_ids], rrf_k=self._rrf_k)

        # Build a side map so the final response can carry the
        # original vector metadata. BM25-only hits (chunks that the
        # vector retriever missed) fall back to whatever the BM25
        # hit knows — for now just the chunk_id; the route layer
        # can re-fetch the chunk row to fill in document / version
        # / section fields if it needs them. The eval harness only
        # reads ``chunk_id``, so this is enough for measurement.
        vector_metadata: dict[str, _VectorHit] = {
            hit.chunk_id: _VectorHit(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                version_id=hit.version_id,
                section_id=hit.section_id,
                snippet=hit.snippet,
                score=hit.score,
            )
            for hit in vector_response.results
        }
        results: list[ChunkSearchResult] = []
        for chunk_id, fused_score in fused[:limit]:
            v = vector_metadata.get(chunk_id)
            if v is not None:
                results.append(
                    ChunkSearchResult(
                        chunk_id=chunk_id,
                        document_id=v.document_id,
                        version_id=v.version_id,
                        section_id=v.section_id,
                        snippet=v.snippet,
                        score=fused_score,
                    )
                )
            else:
                # Pure BM25 hit — the vector retriever missed it but
                # keyword scoring promoted it. BM25 doesn't carry
                # per-chunk document / version context (it's just the
                # keyword index), so the metadata fields stay empty
                # for now. The route layer can re-resolve from the
                # graph store if it needs the full context; the eval
                # harness only reads chunk_id.
                results.append(
                    ChunkSearchResult(
                        chunk_id=chunk_id,
                        document_id="",
                        version_id="",
                        section_id=chunk_id,
                        snippet=None,
                        score=fused_score,
                    )
                )

        return ChunkSearchResponse(
            query=query,
            embedding_model=self._vector.embedding_model,
            query_embedding_dim=vector_response.query_embedding_dim,
            results=results,
        )


__all__ = ["HybridSearchService", "reciprocal_rank_fusion"]
