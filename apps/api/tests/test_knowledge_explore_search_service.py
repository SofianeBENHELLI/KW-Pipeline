"""Service-layer tests for the multi-kind Explorer search (#313).

Drives :class:`KnowledgeExploreSearchService` directly with a stub
search backend so the projection logic (chunks → documents → topics)
is exercised in isolation. The HTTP gates live in
``test_routes_knowledge_explore_search.py``.
"""

from __future__ import annotations

import pytest

from app.schemas.knowledge import (
    ChunkSearchResponse,
    ChunkSearchResult,
    GraphNode,
)
from app.services.document_service import DocumentService
from app.services.knowledge.explore_search import KnowledgeExploreSearchService
from app.services.knowledge.graph_store import InMemoryGraphStore
from app.services.storage_service import InMemoryStorageService


class _StubSearch:
    """Minimal stand-in for :class:`KnowledgeSearchService`."""

    embedding_model = "stub-embed"

    def __init__(self, hits: list[ChunkSearchResult]) -> None:
        self._hits = hits
        self.call_count = 0
        self.last_query: str | None = None
        self.last_limit: int | None = None

    def search(self, query: str, *, limit: int = 10) -> ChunkSearchResponse:
        self.call_count += 1
        self.last_query = query
        self.last_limit = limit
        return ChunkSearchResponse(
            query=query,
            embedding_model=self.embedding_model,
            query_embedding_dim=8,
            results=list(self._hits[:limit]),
        )


def _hit(*, chunk_id: str, document_id: str, score: float) -> ChunkSearchResult:
    return ChunkSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        version_id=f"ver-{document_id}",
        section_id=f"sec-{chunk_id}",
        snippet=f"snippet for {chunk_id}",
        score=score,
    )


def _service(
    *,
    hits: list[ChunkSearchResult],
    nodes: list[GraphNode] | None = None,
) -> KnowledgeExploreSearchService:
    store = InMemoryGraphStore()
    if nodes:
        store.upsert_nodes(nodes)
    documents = DocumentService(storage=InMemoryStorageService())
    return KnowledgeExploreSearchService(
        search=_StubSearch(hits),
        graph_store=store,
        documents=documents,
    )


# ── Empty / validation paths ─────────────────────────────────────────


class TestValidation:
    def test_empty_query_raises(self) -> None:
        service = _service(hits=[])
        with pytest.raises(ValueError):
            service.search(query="   ")

    @pytest.mark.parametrize("bad", [0, -1, 51])
    def test_invalid_chunk_limit_raises(self, bad: int) -> None:
        service = _service(hits=[])
        with pytest.raises(ValueError):
            service.search(query="hello", chunk_limit=bad)

    @pytest.mark.parametrize("bad", [0, 51])
    def test_invalid_document_limit_raises(self, bad: int) -> None:
        service = _service(hits=[])
        with pytest.raises(ValueError):
            service.search(query="hello", document_limit=bad)


# ── Empty hits → empty groups ────────────────────────────────────────


class TestEmptyResults:
    def test_empty_hits_yield_empty_groups(self) -> None:
        service = _service(hits=[])
        out = service.search(query="hello")
        assert out.chunks == []
        assert out.documents == []
        assert out.topics == []
        # Entities + relations are placeholders for v0.2.
        assert out.entities == []
        assert out.relations == []

    def test_schema_version_is_v0_1(self) -> None:
        service = _service(hits=[])
        out = service.search(query="hello")
        assert out.schema_version == "v0.1"


# ── Documents grouping ───────────────────────────────────────────────


class TestDocumentGrouping:
    def test_chunks_aggregate_into_documents(self) -> None:
        # Three hits across two documents — doc-A has two chunks,
        # doc-B has one. Document-group should have two entries
        # ranked by best chunk score.
        service = _service(
            hits=[
                _hit(chunk_id="a1", document_id="doc-A", score=0.9),
                _hit(chunk_id="a2", document_id="doc-A", score=0.7),
                _hit(chunk_id="b1", document_id="doc-B", score=0.8),
            ]
        )
        out = service.search(query="hello", chunk_limit=5)
        assert len(out.documents) == 2
        assert out.documents[0].document_id == "doc-A"  # higher max score
        assert out.documents[0].score == 0.9
        assert {c.chunk_id for c in out.documents[0].contributing_chunks} == {"a1", "a2"}
        assert out.documents[1].document_id == "doc-B"
        assert out.documents[1].score == 0.8

    def test_contributing_chunks_capped(self) -> None:
        # Five chunks on one doc, ``contributing_chunks_per_document=2``
        # → top 2 by score.
        service = _service(
            hits=[
                _hit(chunk_id=f"c{i}", document_id="doc-A", score=0.1 * (10 - i)) for i in range(5)
            ]
        )
        out = service.search(
            query="hello",
            chunk_limit=10,
            contributing_chunks_per_document=2,
        )
        assert len(out.documents) == 1
        chunks = out.documents[0].contributing_chunks
        assert len(chunks) == 2
        # Top scores 1.0, 0.9 (i=0, i=1).
        assert chunks[0].score >= chunks[1].score


# ── Topic grouping ───────────────────────────────────────────────────


class TestTopicGrouping:
    def test_chunks_aggregate_into_topics(self) -> None:
        # Two chunks on different topics. Topic group must have
        # two entries with the right evidence chunks.
        nodes = [
            GraphNode(
                id="c1",
                kind="chunk",
                label="c1",
                properties={
                    "document_id": "doc-A",
                    "version_id": "ver-doc-A",
                    "chunk_id": "c1",
                    "topic_id": "topic-1",
                },
            ),
            GraphNode(
                id="c2",
                kind="chunk",
                label="c2",
                properties={
                    "document_id": "doc-A",
                    "version_id": "ver-doc-A",
                    "chunk_id": "c2",
                    "topic_id": "topic-2",
                },
            ),
            GraphNode(
                id="topic-1",
                kind="topic",
                label="Safety",
                properties={
                    "document_id": "doc-A",
                    "version_id": "ver-doc-A",
                    "topic_id": "topic-1",
                    "label": "Safety",
                    "keywords": ["safety", "risk"],
                },
            ),
            GraphNode(
                id="topic-2",
                kind="topic",
                label="Compliance",
                properties={
                    "document_id": "doc-A",
                    "version_id": "ver-doc-A",
                    "topic_id": "topic-2",
                    "label": "Compliance",
                    "keywords": ["compliance", "audit"],
                },
            ),
        ]
        service = _service(
            hits=[
                _hit(chunk_id="c1", document_id="doc-A", score=0.9),
                _hit(chunk_id="c2", document_id="doc-A", score=0.6),
            ],
            nodes=nodes,
        )
        out = service.search(query="hello", chunk_limit=5)
        topic_ids = {t.topic_id for t in out.topics}
        assert topic_ids == {"topic-1", "topic-2"}
        # Topics ranked by best evidence-chunk score.
        assert out.topics[0].topic_id == "topic-1"
        assert out.topics[0].score == 0.9
        assert out.topics[0].label == "Safety"
        assert out.topics[0].keywords == ["safety", "risk"]
        assert {c.chunk_id for c in out.topics[0].evidence_chunks} == {"c1"}

    def test_chunks_without_topic_id_are_skipped(self) -> None:
        # A chunk with no ``topic_id`` property is correctly excluded
        # from the topic group (it can't anchor a topic) but stays
        # in the chunks group.
        nodes = [
            GraphNode(
                id="c1",
                kind="chunk",
                label="c1",
                properties={
                    "document_id": "doc-A",
                    "version_id": "ver-doc-A",
                    "chunk_id": "c1",
                    # topic_id intentionally absent
                },
            )
        ]
        service = _service(
            hits=[_hit(chunk_id="c1", document_id="doc-A", score=0.9)],
            nodes=nodes,
        )
        out = service.search(query="hello", chunk_limit=5)
        assert len(out.chunks) == 1
        assert out.topics == []


# ── Scope filter callback ────────────────────────────────────────────


class TestScopeFilter:
    def test_callback_drops_inaccessible_chunks(self) -> None:
        # Two hits: doc-A and doc-B. Callback returns True for doc-A
        # only — doc-B chunks must be dropped from every group.
        service = _service(
            hits=[
                _hit(chunk_id="a1", document_id="doc-A", score=0.9),
                _hit(chunk_id="b1", document_id="doc-B", score=0.8),
            ]
        )

        def can_see(doc_id: str) -> bool:
            return doc_id == "doc-A"

        out = service.search(query="hello", chunk_limit=5, can_see_document=can_see)
        assert len(out.chunks) == 1
        assert out.chunks[0].chunk_id == "a1"
        assert {d.document_id for d in out.documents} == {"doc-A"}

    def test_no_callback_keeps_everything(self) -> None:
        service = _service(
            hits=[
                _hit(chunk_id="a1", document_id="doc-A", score=0.9),
                _hit(chunk_id="b1", document_id="doc-B", score=0.8),
            ]
        )
        out = service.search(query="hello", chunk_limit=5, can_see_document=None)
        assert {c.chunk_id for c in out.chunks} == {"a1", "b1"}


# ── Determinism ──────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        # Three deterministically-ordered hits; two service calls must
        # produce byte-identical responses.
        hits = [
            _hit(chunk_id="b", document_id="doc-A", score=0.5),
            _hit(chunk_id="a", document_id="doc-A", score=0.5),  # tied score
            _hit(chunk_id="c", document_id="doc-B", score=0.7),
        ]
        s = _service(hits=hits)
        first = s.search(query="hello", chunk_limit=5)
        second = s.search(query="hello", chunk_limit=5)
        # Documents ordered by score desc, ties by document_id asc.
        assert [d.document_id for d in first.documents] == [d.document_id for d in second.documents]
