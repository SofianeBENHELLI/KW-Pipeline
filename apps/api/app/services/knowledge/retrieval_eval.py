"""Retrieval quality evaluation harness (EPIC-4 item 4.5).

A small, dependency-free harness for measuring how well
:class:`KnowledgeSearchService` retrieves the chunks an operator
considers relevant for a given question. Used by:

- ``apps/api/tests/test_retrieval_eval.py`` — synthetic-corpus baseline
  pinning. Runs on every CI cycle so a retrieval regression is caught
  at the same gate as unit-test failures.
- (future) a ``scripts/knowledge_eval.py`` runner that exercises the
  harness against a customer's real corpus + golden set. The contract
  this module exposes (a single :func:`evaluate` function +
  :class:`EvalResult`) is intentionally small so an operator can
  ``from app.services.knowledge.retrieval_eval import evaluate`` and
  plug in their own search service / golden set without inheriting
  from anything.

The two retrieval-quality metrics shipped today are the standard
information-retrieval pair:

- **Recall@k** — fraction of *relevant* chunks present in the top-``k``
  results. ``relevant = {expected chunk ids the operator labelled}``.
  Captures "did we find the answer at all?".
- **MRR** (mean reciprocal rank) — average of ``1 / rank`` where
  ``rank`` is the position of the first relevant chunk in the
  retrieval order. Captures "how high did we surface the answer?".

The 4.3 BM25 hybrid follow-up will compare its result against the
baseline this harness reports — the harness is therefore the
gate that lets 4.3 say "we improved X%, here's the proof".
"""

from __future__ import annotations

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Protocol

from app.schemas.knowledge import ChunkSearchResponse

# ─── Public schema ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class GoldenQuery:
    """One golden Q→relevant-chunks pair.

    ``relevant_chunk_ids`` is a *set* — order doesn't matter for the
    metric definitions. An empty set means "no chunk is relevant"
    (rare; the harness treats those as a recall pass and an MRR of 0,
    so adding them doesn't game the score).
    """

    query: str
    relevant_chunk_ids: frozenset[str]


@dataclass(frozen=True)
class PerQueryResult:
    """Per-query diagnostic — kept on :class:`EvalResult.per_query` for
    operators that want to see which queries the system missed."""

    query: str
    retrieved_chunk_ids: tuple[str, ...]
    relevant_chunk_ids: frozenset[str]
    recall_at_1: float
    recall_at_k: float
    reciprocal_rank: float


@dataclass(frozen=True)
class EvalResult:
    """Aggregate result of one ``evaluate()`` pass."""

    queries_evaluated: int
    limit: int
    recall_at_1: float
    recall_at_k: float
    mrr: float
    per_query: tuple[PerQueryResult, ...] = field(default_factory=tuple)


# ─── Search-side protocol (small enough for a custom adapter) ──────────


class _SearchLike(Protocol):
    """Subset of :class:`KnowledgeSearchService` the harness needs.

    Defined inline so an operator running the harness against a
    different retrieval system (BM25-only, hybrid, rerank-on-top) only
    has to expose the ``search(query, limit=…)`` shape — no need to
    subclass the real service.
    """

    def search(self, query: str, *, limit: int = ...) -> ChunkSearchResponse: ...


# ─── Metric primitives ─────────────────────────────────────────────────


def recall_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: AbstractSet[str],
    k: int,
) -> float:
    """Return the Recall@k for one query.

    ``Recall@k = |relevant ∩ top_k| / |relevant|``. When the relevant
    set is empty the metric is undefined; we return 1.0 so empty
    golden entries don't drag the corpus mean down (the operator
    should drop them, but we don't crash).
    """
    if k < 1:
        raise ValueError(f"k must be >= 1; got {k}.")
    if not relevant_ids:
        return 1.0
    top_k = retrieved_ids[:k]
    matched = sum(1 for cid in top_k if cid in relevant_ids)
    return matched / len(relevant_ids)


def reciprocal_rank(
    retrieved_ids: Sequence[str],
    relevant_ids: AbstractSet[str],
) -> float:
    """Return the reciprocal rank of the first relevant chunk.

    ``1 / rank`` of the first relevant id in ``retrieved_ids``, or
    ``0.0`` when no relevant id appears (the standard MRR convention).
    Empty relevant set returns ``0.0`` for the same reason —
    ``recall_at_k`` covers the "no ground truth" case; MRR is a
    rank-quality metric and requires a target.
    """
    if not relevant_ids:
        return 0.0
    for rank, cid in enumerate(retrieved_ids, start=1):
        if cid in relevant_ids:
            return 1.0 / rank
    return 0.0


# ─── Top-level evaluate(...) ───────────────────────────────────────────


def evaluate(
    *,
    search: _SearchLike,
    golden: Sequence[GoldenQuery],
    limit: int = 5,
) -> EvalResult:
    """Run ``search`` over every query in ``golden`` and aggregate.

    The harness assumes the corpus the operator wants to evaluate is
    already loaded into whatever store ``search`` reads from — this
    function is the *measurement* step, not the indexing step.

    ``limit`` is the top-k cutoff applied to retrieval. Recall@1 always
    uses the first hit; Recall@k uses the full ``limit`` window.

    Returns an :class:`EvalResult` with aggregate metrics plus a
    per-query breakdown so operators can see which queries the system
    missed.
    """
    if not golden:
        raise ValueError("golden must contain at least one query.")
    if limit < 1:
        raise ValueError(f"limit must be >= 1; got {limit}.")

    per_query: list[PerQueryResult] = []
    for q in golden:
        response = search.search(q.query, limit=limit)
        retrieved_ids = tuple(hit.chunk_id for hit in response.results)
        per_query.append(
            PerQueryResult(
                query=q.query,
                retrieved_chunk_ids=retrieved_ids,
                relevant_chunk_ids=q.relevant_chunk_ids,
                recall_at_1=recall_at_k(retrieved_ids, q.relevant_chunk_ids, 1),
                recall_at_k=recall_at_k(retrieved_ids, q.relevant_chunk_ids, limit),
                reciprocal_rank=reciprocal_rank(retrieved_ids, q.relevant_chunk_ids),
            )
        )

    queries_evaluated = len(per_query)
    avg_recall_at_1 = sum(r.recall_at_1 for r in per_query) / queries_evaluated
    avg_recall_at_k = sum(r.recall_at_k for r in per_query) / queries_evaluated
    avg_mrr = sum(r.reciprocal_rank for r in per_query) / queries_evaluated

    return EvalResult(
        queries_evaluated=queries_evaluated,
        limit=limit,
        recall_at_1=avg_recall_at_1,
        recall_at_k=avg_recall_at_k,
        mrr=avg_mrr,
        per_query=tuple(per_query),
    )


__all__ = [
    "EvalResult",
    "GoldenQuery",
    "PerQueryResult",
    "evaluate",
    "recall_at_k",
    "reciprocal_rank",
]
