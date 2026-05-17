"""Tests for hybrid retrieval — vector + BM25 via RRF (EPIC-4 item 4.3).

Two layers:

1. **RRF primitive** — the pure ``reciprocal_rank_fusion`` function is
   pinned with hand-coded ranked lists so the math is locked
   independently from any retriever.
2. **End-to-end** — composes ``KnowledgeSearchService`` (with the fake
   embedding client) + a ``BM25Index`` over the same synthetic
   corpus, runs both retrievers via the eval harness, and asserts
   that **hybrid retrieval recall is at least as good as vector-only
   retrieval** on the keyword-friendly fixture. The synthetic queries
   are deliberately keyword-heavy (exact terms appear in the relevant
   chunks) which is the regime BM25 dominates.
"""

from __future__ import annotations

import pytest

from app.schemas.knowledge import GraphNode
from app.services.knowledge.bm25 import BM25Index
from app.services.knowledge.embedding_client import FakeEmbeddingClient
from app.services.knowledge.graph_store import InMemoryGraphStore
from app.services.knowledge.hybrid_search import (
    HybridSearchService,
    reciprocal_rank_fusion,
)
from app.services.knowledge.retrieval_eval import GoldenQuery, evaluate
from app.services.knowledge.search import KnowledgeSearchService

# ─── reciprocal_rank_fusion ────────────────────────────────────────────


class TestRRFPrimitive:
    def test_chunk_present_in_both_lists_outranks_singletons(self) -> None:
        # Chunk "a" ranks 2 in vector, 1 in bm25 → score = 1/(60+2) + 1/(60+1)
        # Chunk "b" ranks 1 in vector only.
        # Chunk "c" ranks 2 in bm25 only.
        fused = reciprocal_rank_fusion([["b", "a"], ["a", "c"]])
        ids = [pair[0] for pair in fused]
        assert ids[0] == "a"  # appears in both
        assert ids[1] == "b"  # ranked higher than c (rank 1 vs rank 2)
        assert ids[2] == "c"

    def test_tie_broken_by_chunk_id_ascending(self) -> None:
        # ``z`` is rank-1 in list A, rank-2 in list B; ``a`` is the mirror.
        # Both accumulate ``1/(60+1) + 1/(60+2)`` → identical fused score →
        # tie-break on chunk_id ascending puts ``a`` ahead of ``z``.
        fused = reciprocal_rank_fusion([["z", "a"], ["a", "z"]])
        assert [pair[0] for pair in fused] == ["a", "z"]

    def test_higher_rank_wins(self) -> None:
        fused = reciprocal_rank_fusion([["a", "b", "c"]])
        assert fused[0][1] > fused[1][1] > fused[2][1]

    def test_rrf_k_affects_dispersion(self) -> None:
        # Smaller rrf_k amplifies the top-1 lead.
        fused_small_k = reciprocal_rank_fusion([["a", "b"]], rrf_k=0)
        fused_large_k = reciprocal_rank_fusion([["a", "b"]], rrf_k=1000)
        ratio_small = fused_small_k[0][1] / fused_small_k[1][1]
        ratio_large = fused_large_k[0][1] / fused_large_k[1][1]
        assert ratio_small > ratio_large

    def test_rejects_negative_rrf_k(self) -> None:
        with pytest.raises(ValueError, match="rrf_k must be"):
            reciprocal_rank_fusion([["a"]], rrf_k=-1)


# ─── End-to-end: hybrid >= vector on keyword-friendly fixture ──────────


def _seed_chunk(graph_store, *, chunk_id: str, text: str, embedding: list[float]) -> None:
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


_CORPUS: list[tuple[str, str]] = [
    ("chunk-mcp-a", "MCP gateway routes external agents to enterprise tools."),
    ("chunk-mcp-b", "The MCP protocol standardises agent-to-tool integration."),
    ("chunk-thermal-a", "Battery thermal management is critical for EV safety."),
    ("chunk-thermal-b", "Cooling loops use glycol to prevent thermal runaway."),
    ("chunk-revenue-a", "Sales revenue grew 15% in Q3 2026."),
    ("chunk-revenue-b", "Quarterly earnings call focused on AI-driven products."),
    ("chunk-ml-a", "Transformer models dominate the natural-language landscape."),
    ("chunk-ml-b", "Attention mechanisms scale quadratically with sequence length."),
]


_GOLDEN: list[GoldenQuery] = [
    GoldenQuery(
        query="MCP gateway protocol",
        relevant_chunk_ids=frozenset({"chunk-mcp-a", "chunk-mcp-b"}),
    ),
    GoldenQuery(
        query="battery cooling thermal runaway",
        relevant_chunk_ids=frozenset({"chunk-thermal-a", "chunk-thermal-b"}),
    ),
    GoldenQuery(
        query="quarterly revenue earnings",
        relevant_chunk_ids=frozenset({"chunk-revenue-a", "chunk-revenue-b"}),
    ),
    GoldenQuery(
        query="transformer attention sequence",
        relevant_chunk_ids=frozenset({"chunk-ml-a", "chunk-ml-b"}),
    ),
]


def _build_services_and_index():
    graph_store = InMemoryGraphStore()
    embedding_client = FakeEmbeddingClient(dim=16)
    doc_vectors = embedding_client.embed_documents([text for _, text in _CORPUS])
    for (chunk_id, text), vector in zip(_CORPUS, doc_vectors, strict=True):
        _seed_chunk(graph_store, chunk_id=chunk_id, text=text, embedding=vector)
    vector_search = KnowledgeSearchService(
        embedding_client=embedding_client, graph_store=graph_store
    )
    bm25 = BM25Index(_CORPUS)
    return vector_search, bm25


def test_hybrid_search_exposes_search_protocol_shape() -> None:
    """Drop-in contract: ``HybridSearchService`` looks like a
    :class:`KnowledgeSearchService` to the eval harness."""
    vector, bm25 = _build_services_and_index()
    hybrid = HybridSearchService(vector=vector, bm25=bm25)
    response = hybrid.search("battery cooling", limit=3)
    assert response.query == "battery cooling"
    assert response.embedding_model == vector.embedding_model
    assert len(response.results) <= 3


def test_hybrid_rejects_empty_query() -> None:
    vector, bm25 = _build_services_and_index()
    hybrid = HybridSearchService(vector=vector, bm25=bm25)
    with pytest.raises(ValueError, match="query must not be empty"):
        hybrid.search("", limit=3)


def test_hybrid_rejects_invalid_limit() -> None:
    vector, bm25 = _build_services_and_index()
    hybrid = HybridSearchService(vector=vector, bm25=bm25)
    with pytest.raises(ValueError, match="limit must be"):
        hybrid.search("battery", limit=0)


def test_hybrid_beats_or_matches_vector_only_on_keyword_corpus() -> None:
    """The keyword-heavy fixture is exactly the regime BM25 dominates
    over the fake embedding client (whose vectors are
    sha256-derived random projections). Hybrid retrieval should
    therefore land Recall@k at least as high as vector-only on this
    fixture, with the strict majority of queries strictly higher.

    This is the 4.3 → 4.5 spread the 2026-05-14 plan named as the
    gate that justifies the BM25 hybrid investment. Pin it as a
    regression test so a future change can't silently degrade
    keyword recall.
    """
    vector, bm25 = _build_services_and_index()
    hybrid = HybridSearchService(vector=vector, bm25=bm25)

    vector_result = evaluate(search=vector, golden=_GOLDEN, limit=5)
    hybrid_result = evaluate(search=hybrid, golden=_GOLDEN, limit=5)

    # Hybrid recall@5 is at least vector recall@5. Strict-better on
    # the keyword corpus is the expected regime, but tying is
    # acceptable when both surface the relevant chunks anyway.
    assert hybrid_result.recall_at_k >= vector_result.recall_at_k, (
        f"Hybrid Recall@5={hybrid_result.recall_at_k:.3f} regressed below "
        f"vector-only Recall@5={vector_result.recall_at_k:.3f}."
    )
    # And the keyword corpus should give hybrid a real lift on at
    # least one of the metrics.
    lifted = (
        hybrid_result.recall_at_1 > vector_result.recall_at_1
        or hybrid_result.recall_at_k > vector_result.recall_at_k
        or hybrid_result.mrr > vector_result.mrr
    )
    assert lifted, (
        "Expected hybrid to improve at least one of Recall@1 / Recall@5 / MRR "
        "on the keyword corpus. "
        f"vector=(R@1={vector_result.recall_at_1:.3f}, R@5={vector_result.recall_at_k:.3f}, "
        f"MRR={vector_result.mrr:.3f}); "
        f"hybrid=(R@1={hybrid_result.recall_at_1:.3f}, R@5={hybrid_result.recall_at_k:.3f}, "
        f"MRR={hybrid_result.mrr:.3f})."
    )
