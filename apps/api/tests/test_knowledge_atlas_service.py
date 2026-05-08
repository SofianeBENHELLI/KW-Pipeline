"""Service-layer tests for the corpus atlas summary (#312).

Drives :class:`KnowledgeAtlasService` directly with an in-memory graph
store + document service so the projection logic (top topics /
validation coverage / recent / bridge / outlier) is exercised in
isolation. Route-level gates live in
``test_routes_knowledge_atlas.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.schemas.knowledge import GraphEdge, GraphNode
from app.services.document_service import DocumentService
from app.services.knowledge.atlas import KnowledgeAtlasService
from app.services.knowledge.graph_store import InMemoryGraphStore
from app.services.storage_service import InMemoryStorageService


def _doc(
    *,
    document_id: str,
    title: str,
    status: DocumentVersionStatus = DocumentVersionStatus.VALIDATED,
    created_at: datetime | None = None,
) -> Document:
    when = created_at or datetime(2026, 1, 1, tzinfo=UTC)
    version = DocumentVersion(
        id=f"ver-{document_id}",
        document_id=document_id,
        version_number=1,
        filename=title,
        content_type="text/markdown",
        file_size=10,
        sha256="0" * 64,
        storage_uri=f"memory://{document_id}",
        status=status,
        created_at=when,
    )
    return Document(
        id=document_id,
        original_filename=title,
        latest_version_id=version.id,
        versions=[version],
        created_at=when,
    )


def _service(
    *,
    documents: list[Document] | None = None,
    nodes: list[GraphNode] | None = None,
    edges: list[GraphEdge] | None = None,
) -> tuple[KnowledgeAtlasService, DocumentService, InMemoryGraphStore]:
    graph_store = InMemoryGraphStore()
    if nodes:
        graph_store.upsert_nodes(nodes)
    if edges:
        graph_store.upsert_edges(edges)
    document_service = DocumentService(storage=InMemoryStorageService())
    if documents:
        for doc in documents:
            document_service.catalog.save_document_with_version(
                document=doc, version=doc.versions[0]
            )
    return (
        KnowledgeAtlasService(graph_store=graph_store, documents=document_service),
        document_service,
        graph_store,
    )


# ── Empty corpus ─────────────────────────────────────────────────────


class TestEmptyCorpus:
    def test_empty_corpus_returns_zero_counts(self) -> None:
        service, _, _ = _service()
        out = service.build()
        assert out.schema_version == "v0.1"
        assert out.top_topics == []
        assert out.validation_coverage.total_documents == 0
        assert out.validation_coverage.validated_count == 0
        assert out.recent_documents == []
        assert out.bridge_documents == []
        assert out.outlier_relations == []


# ── Validation coverage ──────────────────────────────────────────────


class TestValidationCoverage:
    def test_counts_split_by_status(self) -> None:
        docs = [
            _doc(document_id="d1", title="A", status=DocumentVersionStatus.VALIDATED),
            _doc(document_id="d2", title="B", status=DocumentVersionStatus.VALIDATED),
            _doc(document_id="d3", title="C", status=DocumentVersionStatus.NEEDS_REVIEW),
            _doc(document_id="d4", title="D", status=DocumentVersionStatus.REJECTED),
            _doc(document_id="d5", title="E", status=DocumentVersionStatus.UPLOADED),
        ]
        service, _, _ = _service(documents=docs)
        out = service.build()
        assert out.validation_coverage.total_documents == 5
        assert out.validation_coverage.validated_count == 2
        assert out.validation_coverage.needs_review_count == 1
        assert out.validation_coverage.rejected_count == 1
        assert out.validation_coverage.other_count == 1


# ── Recent documents ─────────────────────────────────────────────────


class TestRecentDocuments:
    def test_sorted_by_created_at_descending(self) -> None:
        docs = [
            _doc(
                document_id="old",
                title="Old",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            _doc(
                document_id="mid",
                title="Mid",
                created_at=datetime(2026, 3, 1, tzinfo=UTC),
            ),
            _doc(
                document_id="new",
                title="New",
                created_at=datetime(2026, 5, 1, tzinfo=UTC),
            ),
        ]
        service, _, _ = _service(documents=docs)
        out = service.build()
        assert [d.document_id for d in out.recent_documents] == ["new", "mid", "old"]
        assert out.recent_documents[0].title == "New"
        assert out.recent_documents[0].validation_status == "VALIDATED"

    def test_limit_caps_results(self) -> None:
        docs = [
            _doc(
                document_id=f"d{i}",
                title=f"Doc {i}",
                created_at=datetime(2026, 1, i + 1, tzinfo=UTC),
            )
            for i in range(5)
        ]
        service, _, _ = _service(documents=docs)
        out = service.build(recent_documents_limit=2)
        assert len(out.recent_documents) == 2


# ── Top topics ───────────────────────────────────────────────────────


class TestTopTopics:
    def test_topics_ranked_by_chunk_count_desc(self) -> None:
        docs = [_doc(document_id="d1", title="A")]
        nodes = [
            GraphNode(
                id="topic-1",
                kind="topic",
                label="Safety",
                properties={"keywords": ["safety", "risk"]},
            ),
            GraphNode(
                id="topic-2",
                kind="topic",
                label="Compliance",
                properties={"keywords": ["compliance"]},
            ),
            # 3 chunks on topic-1, 1 chunk on topic-2.
            GraphNode(
                id="c1",
                kind="chunk",
                label="c1",
                properties={"document_id": "d1", "topic_id": "topic-1"},
            ),
            GraphNode(
                id="c2",
                kind="chunk",
                label="c2",
                properties={"document_id": "d1", "topic_id": "topic-1"},
            ),
            GraphNode(
                id="c3",
                kind="chunk",
                label="c3",
                properties={"document_id": "d1", "topic_id": "topic-1"},
            ),
            GraphNode(
                id="c4",
                kind="chunk",
                label="c4",
                properties={"document_id": "d1", "topic_id": "topic-2"},
            ),
        ]
        service, _, _ = _service(documents=docs, nodes=nodes)
        out = service.build()
        assert [t.topic_id for t in out.top_topics] == ["topic-1", "topic-2"]
        assert out.top_topics[0].chunk_count == 3
        assert out.top_topics[0].keywords == ["safety", "risk"]

    def test_topics_with_no_chunks_omitted(self) -> None:
        nodes = [
            GraphNode(
                id="topic-empty",
                kind="topic",
                label="Empty",
                properties={"keywords": []},
            )
        ]
        service, _, _ = _service(nodes=nodes)
        out = service.build()
        assert out.top_topics == []

    def test_topic_chunks_in_hidden_documents_excluded(self) -> None:
        docs = [_doc(document_id="d1", title="A")]
        nodes = [
            GraphNode(
                id="topic-1",
                kind="topic",
                label="Safety",
                properties={"keywords": ["s"]},
            ),
            GraphNode(
                id="c1",
                kind="chunk",
                label="c1",
                properties={"document_id": "d1", "topic_id": "topic-1"},
            ),
            # Chunk on a doc not in catalog → out of visible set.
            GraphNode(
                id="c2",
                kind="chunk",
                label="c2",
                properties={"document_id": "d-hidden", "topic_id": "topic-1"},
            ),
        ]
        service, _, _ = _service(documents=docs, nodes=nodes)
        out = service.build()
        assert out.top_topics[0].chunk_count == 1
        assert out.top_topics[0].document_count == 1


# ── Bridge documents ─────────────────────────────────────────────────


class TestBridgeDocuments:
    def test_doc_spanning_distant_topics_scores_high(self) -> None:
        docs = [_doc(document_id="bridge-doc", title="Bridge")]
        nodes = [
            GraphNode(
                id="topic-1",
                kind="topic",
                label="Safety",
                properties={"keywords": ["safety", "risk"]},
            ),
            GraphNode(
                id="topic-2",
                kind="topic",
                label="Finance",
                properties={"keywords": ["finance", "audit"]},
            ),
            GraphNode(
                id="c1",
                kind="chunk",
                label="c1",
                properties={"document_id": "bridge-doc", "topic_id": "topic-1"},
            ),
            GraphNode(
                id="c2",
                kind="chunk",
                label="c2",
                properties={"document_id": "bridge-doc", "topic_id": "topic-2"},
            ),
        ]
        service, _, _ = _service(documents=docs, nodes=nodes)
        out = service.build()
        assert len(out.bridge_documents) == 1
        assert out.bridge_documents[0].document_id == "bridge-doc"
        assert out.bridge_documents[0].topic_count == 2
        assert out.bridge_documents[0].score > 0.5

    def test_doc_with_one_topic_excluded(self) -> None:
        docs = [_doc(document_id="d1", title="Single")]
        nodes = [
            GraphNode(
                id="topic-1",
                kind="topic",
                label="Safety",
                properties={"keywords": ["safety"]},
            ),
            GraphNode(
                id="c1",
                kind="chunk",
                label="c1",
                properties={"document_id": "d1", "topic_id": "topic-1"},
            ),
        ]
        service, _, _ = _service(documents=docs, nodes=nodes)
        out = service.build()
        assert out.bridge_documents == []


# ── Outlier relations ────────────────────────────────────────────────


class TestOutlierRelations:
    def test_strong_bridge_edge_classified_as_outlier(self) -> None:
        docs = [_doc(document_id="d1", title="A")]
        nodes = [
            GraphNode(
                id="topic-A",
                kind="topic",
                label="A",
                properties={"keywords": ["alpha", "beta"]},
            ),
            GraphNode(
                id="topic-B",
                kind="topic",
                label="B",
                properties={"keywords": ["gamma", "delta"]},
            ),
            GraphNode(
                id="c1",
                kind="chunk",
                label="c1",
                properties={"document_id": "d1", "topic_id": "topic-A"},
            ),
            GraphNode(
                id="c2",
                kind="chunk",
                label="c2",
                properties={"document_id": "d1", "topic_id": "topic-B"},
            ),
        ]
        edges = [
            GraphEdge(
                id="ver-d1:c1->related_to->c2",
                kind="related_to",
                source_id="c1",
                target_id="c2",
                properties={
                    "score": 0.85,
                    "reason": "shared phrase",
                    "shared_keywords": ["x"],
                },
            )
        ]
        service, _, _ = _service(documents=docs, nodes=nodes, edges=edges)
        out = service.build()
        assert len(out.outlier_relations) == 1
        rel = out.outlier_relations[0]
        assert rel.relation_id == "ver-d1:c1->related_to->c2"
        assert rel.kind == "related_to"
        assert rel.score >= 0.85
        assert rel.reason == "shared phrase"
        assert rel.shared_keywords == ["x"]

    def test_weak_edge_not_outlier(self) -> None:
        docs = [_doc(document_id="d1", title="A")]
        nodes = [
            GraphNode(
                id="topic-A",
                kind="topic",
                label="A",
                properties={"keywords": ["a"]},
            ),
            GraphNode(
                id="topic-B",
                kind="topic",
                label="B",
                properties={"keywords": ["b"]},
            ),
            GraphNode(
                id="c1",
                kind="chunk",
                label="c1",
                properties={"document_id": "d1", "topic_id": "topic-A"},
            ),
            GraphNode(
                id="c2",
                kind="chunk",
                label="c2",
                properties={"document_id": "d1", "topic_id": "topic-B"},
            ),
        ]
        edges = [
            GraphEdge(
                id="ver-d1:c1->related_to->c2",
                kind="related_to",
                source_id="c1",
                target_id="c2",
                properties={"score": 0.20, "shared_keywords": []},
            )
        ]
        service, _, _ = _service(documents=docs, nodes=nodes, edges=edges)
        out = service.build()
        assert out.outlier_relations == []

    def test_strong_edge_within_same_topic_not_outlier(self) -> None:
        docs = [_doc(document_id="d1", title="A")]
        nodes = [
            GraphNode(
                id="topic-A",
                kind="topic",
                label="A",
                properties={"keywords": ["a", "b"]},
            ),
            GraphNode(
                id="c1",
                kind="chunk",
                label="c1",
                properties={"document_id": "d1", "topic_id": "topic-A"},
            ),
            GraphNode(
                id="c2",
                kind="chunk",
                label="c2",
                properties={"document_id": "d1", "topic_id": "topic-A"},
            ),
        ]
        edges = [
            GraphEdge(
                id="ver-d1:c1->related_to->c2",
                kind="related_to",
                source_id="c1",
                target_id="c2",
                properties={"score": 0.9, "shared_keywords": []},
            )
        ]
        service, _, _ = _service(documents=docs, nodes=nodes, edges=edges)
        out = service.build()
        assert out.outlier_relations == []

    def test_edge_to_hidden_document_excluded(self) -> None:
        docs = [_doc(document_id="d1", title="A")]
        nodes = [
            GraphNode(
                id="topic-A",
                kind="topic",
                label="A",
                properties={"keywords": ["a"]},
            ),
            GraphNode(
                id="topic-B",
                kind="topic",
                label="B",
                properties={"keywords": ["b"]},
            ),
            GraphNode(
                id="c1",
                kind="chunk",
                label="c1",
                properties={"document_id": "d1", "topic_id": "topic-A"},
            ),
            GraphNode(
                id="c-hidden",
                kind="chunk",
                label="c-hidden",
                properties={"document_id": "d-hidden", "topic_id": "topic-B"},
            ),
        ]
        edges = [
            GraphEdge(
                id="cross-doc",
                kind="related_to",
                source_id="c1",
                target_id="c-hidden",
                properties={"score": 0.9, "shared_keywords": []},
            )
        ]
        service, _, _ = _service(documents=docs, nodes=nodes, edges=edges)
        out = service.build()
        assert out.outlier_relations == []


# ── Scope filter callback ────────────────────────────────────────────


class TestScopeFilter:
    def test_callback_drops_inaccessible_documents(self) -> None:
        docs = [
            _doc(document_id="d-public", title="Public"),
            _doc(document_id="d-private", title="Private"),
        ]
        service, _, _ = _service(documents=docs)
        out = service.build(can_see_document=lambda d: d == "d-public")
        assert out.validation_coverage.total_documents == 1
        assert out.recent_documents == [
            r for r in out.recent_documents if r.document_id == "d-public"
        ]
        assert {r.document_id for r in out.recent_documents} == {"d-public"}


# ── Validation ───────────────────────────────────────────────────────


class TestValidation:
    @pytest.mark.parametrize(
        "kwarg",
        [
            "top_topics_limit",
            "recent_documents_limit",
            "bridge_documents_limit",
            "outlier_relations_limit",
        ],
    )
    def test_zero_limit_raises(self, kwarg: str) -> None:
        service, _, _ = _service()
        with pytest.raises(ValueError):
            service.build(**{kwarg: 0})

    @pytest.mark.parametrize(
        "kwarg",
        [
            "top_topics_limit",
            "recent_documents_limit",
            "bridge_documents_limit",
            "outlier_relations_limit",
        ],
    )
    def test_above_max_raises(self, kwarg: str) -> None:
        service, _, _ = _service()
        with pytest.raises(ValueError):
            service.build(**{kwarg: 100})


# ── Determinism ──────────────────────────────────────────────────────


class TestDeterminism:
    def test_two_calls_yield_byte_identical_output(self) -> None:
        docs = [
            _doc(
                document_id="d1",
                title="A",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            _doc(
                document_id="d2",
                title="B",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ]
        service, _, _ = _service(documents=docs)
        first = service.build()
        second = service.build()
        assert first.model_dump_json() == second.model_dump_json()
