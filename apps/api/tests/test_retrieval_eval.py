"""Tests for the retrieval quality evaluation harness (EPIC-4 item 4.5).

Three layers:

1. **Metric primitives** — direct unit tests on ``recall_at_k`` and
   ``reciprocal_rank`` so the math is pinned independently from any
   storage backend.
2. **Aggregate** — a stub search that returns hand-rolled hit lists
   exercises the ``evaluate(...)`` top-level so the per-query →
   aggregate aggregation is verified without depending on the real
   embedding stack.
3. **Synthetic-corpus baseline** — seeds a tiny in-memory corpus
   (chunks + fake embeddings) and runs the harness against the real
   :class:`KnowledgeSearchService`. Pins that the wiring works
   end-to-end and reports a baseline number that the 4.3 BM25 hybrid
   follow-up will measure improvement against.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.dependencies import build_services
from app.schemas.knowledge import ChunkSearchResponse, ChunkSearchResult, GraphNode
from app.services.knowledge.embedding_client import FakeEmbeddingClient
from app.services.knowledge.retrieval_eval import (
    EvalResult,
    GoldenQuery,
    evaluate,
    recall_at_k,
    reciprocal_rank,
)

# ─── Layer 1 — metric primitives ──────────────────────────────────────


class TestRecallAtK:
    def test_full_hit_in_top_k(self) -> None:
        assert recall_at_k(["a", "b", "c"], {"a", "b"}, 3) == 1.0

    def test_partial_hit(self) -> None:
        # 1 of 2 relevant chunks present in top-3 → recall 0.5
        assert recall_at_k(["a", "x", "y"], {"a", "b"}, 3) == 0.5

    def test_no_hit(self) -> None:
        assert recall_at_k(["x", "y", "z"], {"a", "b"}, 3) == 0.0

    def test_k_clamps_results(self) -> None:
        # Recall@1 sees only the first; chunk "a" misses the top.
        assert recall_at_k(["x", "a", "b"], {"a", "b"}, 1) == 0.0
        # Recall@3 sees both.
        assert recall_at_k(["x", "a", "b"], {"a", "b"}, 3) == 1.0

    def test_empty_relevant_set_returns_one(self) -> None:
        # Undefined-but-common case: no ground truth means we don't
        # penalise the search. Operator should drop these entries but
        # we don't crash on them.
        assert recall_at_k(["a", "b"], set(), 5) == 1.0


class TestReciprocalRank:
    def test_first_position_is_one(self) -> None:
        assert reciprocal_rank(["a", "b", "c"], {"a"}) == 1.0

    def test_second_position_is_half(self) -> None:
        assert reciprocal_rank(["x", "a", "b"], {"a"}) == 0.5

    def test_third_position_is_one_third(self) -> None:
        assert reciprocal_rank(["x", "y", "a"], {"a"}) == 1.0 / 3.0

    def test_no_relevant_returns_zero(self) -> None:
        assert reciprocal_rank(["x", "y", "z"], {"a"}) == 0.0

    def test_empty_relevant_set_returns_zero(self) -> None:
        # MRR requires a target. Empty set is treated as a miss for
        # rank-quality purposes; ``recall_at_k`` handles the "no
        # ground truth" case in its own way.
        assert reciprocal_rank(["a", "b"], set()) == 0.0


# ─── Layer 2 — evaluate() aggregation on a stub search ────────────────


@dataclass
class _StubSearch:
    """Search adapter that returns hand-coded hit lists per query.

    Used to exercise ``evaluate(...)``'s per-query → aggregate
    arithmetic without depending on real embeddings or the in-memory
    graph store. The stub's ``search`` method returns the first set
    of ids that matches the query string — anything else returns an
    empty result.
    """

    fixtures: dict[str, list[str]]

    def search(self, query: str, *, limit: int = 5) -> ChunkSearchResponse:
        ids = self.fixtures.get(query, [])[:limit]
        results = [
            ChunkSearchResult(
                chunk_id=cid,
                document_id="doc-1",
                version_id="ver-1",
                section_id=cid,
                snippet=None,
                score=1.0 - (i * 0.1),
            )
            for i, cid in enumerate(ids)
        ]
        return ChunkSearchResponse(
            query=query,
            embedding_model="stub",
            query_embedding_dim=0,
            results=results,
        )


def test_evaluate_aggregates_per_query_into_means() -> None:
    search = _StubSearch(
        fixtures={
            "alpha": ["chunk-a", "chunk-b", "chunk-c"],  # first match at rank 1
            "beta": ["chunk-x", "chunk-y", "chunk-z"],  # no match → RR=0, recall=0
            "gamma": ["chunk-x", "chunk-g", "chunk-y"],  # first match at rank 2
        }
    )
    golden = [
        GoldenQuery(query="alpha", relevant_chunk_ids=frozenset({"chunk-a"})),
        GoldenQuery(query="beta", relevant_chunk_ids=frozenset({"chunk-b"})),
        GoldenQuery(query="gamma", relevant_chunk_ids=frozenset({"chunk-g"})),
    ]
    result = evaluate(search=search, golden=golden, limit=5)
    assert isinstance(result, EvalResult)
    assert result.queries_evaluated == 3
    # Recall@1: 1 hit + 0 + 0 = 1/3
    assert result.recall_at_1 == 1.0 / 3.0
    # Recall@5: 1 (alpha) + 0 (beta) + 1 (gamma) = 2/3
    assert result.recall_at_k == 2.0 / 3.0
    # MRR: 1.0 (alpha rank 1) + 0.0 (beta miss) + 0.5 (gamma rank 2)
    # = 1.5 / 3 = 0.5
    assert result.mrr == 0.5
    # Per-query breakdown carries the diagnostics.
    assert len(result.per_query) == 3
    assert result.per_query[0].query == "alpha"
    assert result.per_query[0].reciprocal_rank == 1.0


def test_evaluate_rejects_empty_golden_set() -> None:
    search = _StubSearch(fixtures={})
    import pytest as _pytest

    with _pytest.raises(ValueError, match="at least one query"):
        evaluate(search=search, golden=[], limit=5)


def test_evaluate_rejects_invalid_limit() -> None:
    search = _StubSearch(fixtures={})
    import pytest as _pytest

    with _pytest.raises(ValueError, match="limit must be"):
        evaluate(
            search=search,
            golden=[GoldenQuery(query="q", relevant_chunk_ids=frozenset({"x"}))],
            limit=0,
        )


# ─── Layer 3 — end-to-end baseline against a synthetic corpus ─────────


def _seed_chunk(graph_store, *, chunk_id: str, text: str, embedding: list[float]) -> None:
    """Seed one ``Chunk`` node + its embedding into the graph store."""
    graph_store.upsert_nodes(
        [
            GraphNode(
                id=chunk_id,
                kind="chunk",
                label=f"chunk:{chunk_id}",
                properties={
                    "document_id": "doc-eval",
                    "version_id": "ver-eval",
                    "section_id": chunk_id,
                    "text_preview": text,
                },
            )
        ]
    )
    graph_store.set_chunk_embedding(chunk_id=chunk_id, embedding=embedding)


def test_eval_harness_runs_against_synthetic_corpus_with_fake_embeddings() -> None:
    """End-to-end pin: the harness drives the real
    :class:`KnowledgeSearchService` (with :class:`FakeEmbeddingClient`)
    against a tiny synthetic corpus and returns a usable
    :class:`EvalResult`.

    The fake-embedding cosine similarity is essentially a deterministic
    permutation, so the absolute numbers here are *not* a quality
    benchmark — they're a baseline that future PRs (4.3 BM25 hybrid,
    4.4 rerank) will measure improvement against."""
    services = build_services()
    # ``build_services`` wires a search service only when the
    # knowledge layer is enabled. For this test we build one
    # locally against the in-memory graph store.
    from app.services.knowledge.graph_store import InMemoryGraphStore
    from app.services.knowledge.search import KnowledgeSearchService

    graph_store = InMemoryGraphStore()
    embedding_client = FakeEmbeddingClient(dim=16)

    chunks: list[tuple[str, str]] = [
        ("chunk-thermal-a", "Battery thermal management is critical for EV safety."),
        ("chunk-thermal-b", "Cooling loops use glycol to prevent thermal runaway."),
        ("chunk-revenue-a", "Sales revenue grew 15% in Q3 2026."),
        ("chunk-revenue-b", "Quarterly earnings call focused on AI-driven products."),
        ("chunk-ml-a", "Transformer models dominate the natural-language landscape."),
        ("chunk-ml-b", "Attention mechanisms scale quadratically with sequence length."),
    ]
    doc_vectors = embedding_client.embed_documents([text for _, text in chunks])
    for (chunk_id, text), vector in zip(chunks, doc_vectors, strict=True):
        _seed_chunk(graph_store, chunk_id=chunk_id, text=text, embedding=vector)

    search = KnowledgeSearchService(
        embedding_client=embedding_client,
        graph_store=graph_store,
    )
    # Golden set — the relevant_chunk_ids reflect what a human
    # operator would label as "relevant" for each query.
    golden = [
        GoldenQuery(
            query="Battery thermal management is critical for EV safety.",
            relevant_chunk_ids=frozenset({"chunk-thermal-a", "chunk-thermal-b"}),
        ),
        GoldenQuery(
            query="Sales revenue grew 15% in Q3 2026.",
            relevant_chunk_ids=frozenset({"chunk-revenue-a", "chunk-revenue-b"}),
        ),
        GoldenQuery(
            query="Transformer models dominate the natural-language landscape.",
            relevant_chunk_ids=frozenset({"chunk-ml-a", "chunk-ml-b"}),
        ),
    ]
    result = evaluate(search=search, golden=golden, limit=5)

    # The harness ran successfully and returned a structured result.
    assert isinstance(result, EvalResult)
    assert result.queries_evaluated == 3
    assert result.limit == 5
    # Range checks — fake embeddings give us a deterministic but
    # non-trivial number. We don't pin the exact baseline value
    # because the fake's vectors are sensitive to the asymmetric
    # salt; the assertion is that the harness produced numbers in
    # the valid range and that the per-query breakdown has the right
    # shape.
    assert 0.0 <= result.recall_at_1 <= 1.0
    assert 0.0 <= result.recall_at_k <= 1.0
    assert 0.0 <= result.mrr <= 1.0
    assert len(result.per_query) == 3
    # Every per-query record carries the query verbatim + the
    # relevant set the caller passed in.
    assert result.per_query[0].query == golden[0].query
    assert result.per_query[0].relevant_chunk_ids == golden[0].relevant_chunk_ids
    # Avoid touching unused `services` to keep mypy quiet.
    del services
