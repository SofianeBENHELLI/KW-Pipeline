"""Targeted unit tests for ``_GraphStoreTopicProvider``.

The adapter is exercised end-to-end by the similar-documents route
suite, but those tests don't pin down its exact contract — empty-set
fallback semantics, chunk-only filter, malformed topic_id rejection.
This file locks the contract down without spinning up the full
similarity service.
"""

from __future__ import annotations

from app.dependencies import _GraphStoreTopicProvider
from app.schemas.knowledge import GraphNode, KnowledgeGraphProjection
from app.services.document_service import DocumentService
from app.services.knowledge import InMemoryGraphStore
from app.services.storage_service import InMemoryStorageService


def _provider(graph_store: InMemoryGraphStore) -> _GraphStoreTopicProvider:
    return _GraphStoreTopicProvider(
        documents=DocumentService(storage=InMemoryStorageService()),
        graph_store=graph_store,
    )


def test_topic_ids_for_document_returns_empty_when_no_chunks() -> None:
    """No projected chunks → empty set (cold-start contract)."""
    provider = _provider(InMemoryGraphStore())
    assert provider.topic_ids_for_document("doc-id") == set()


def test_topic_ids_for_document_extracts_distinct_chunk_topics(
    monkeypatch,
) -> None:
    """Walks the projection's chunk nodes and folds their ``topic_id``s into a set."""
    store = InMemoryGraphStore()
    projection = KnowledgeGraphProjection(
        document_id="d1",
        version_id="v1",
        nodes=[
            GraphNode(id="d1", kind="document", label="d", properties={}),
            GraphNode(
                id="c1",
                kind="chunk",
                label="chunk-1",
                properties={"topic_id": "topic-A"},
            ),
            GraphNode(
                id="c2",
                kind="chunk",
                label="chunk-2",
                properties={"topic_id": "topic-A"},  # dup
            ),
            GraphNode(
                id="c3",
                kind="chunk",
                label="chunk-3",
                properties={"topic_id": "topic-B"},
            ),
        ],
        edges=[],
    )
    monkeypatch.setattr(store, "find_subgraph_for_document", lambda _id: projection)

    provider = _provider(store)
    assert provider.topic_ids_for_document("d1") == {"topic-A", "topic-B"}


def test_topic_ids_for_document_skips_non_chunk_nodes(monkeypatch) -> None:
    """Document/version/topic nodes are not counted; only chunks contribute."""
    store = InMemoryGraphStore()
    projection = KnowledgeGraphProjection(
        document_id="d1",
        version_id="v1",
        nodes=[
            GraphNode(
                id="t1",
                kind="topic",
                label="topic-A",
                properties={"topic_id": "topic-A"},
            ),
            GraphNode(
                id="c1",
                kind="chunk",
                label="chunk-1",
                properties={"topic_id": "topic-X"},
            ),
        ],
        edges=[],
    )
    monkeypatch.setattr(store, "find_subgraph_for_document", lambda _id: projection)

    provider = _provider(store)
    # Only the chunk's topic-X lands in the set, not the topic node's.
    assert provider.topic_ids_for_document("d1") == {"topic-X"}


def test_topic_ids_for_document_drops_missing_or_empty_ids(monkeypatch) -> None:
    """Chunks without a topic_id (or empty/non-string) are silently dropped."""
    store = InMemoryGraphStore()
    projection = KnowledgeGraphProjection(
        document_id="d1",
        version_id="v1",
        nodes=[
            GraphNode(id="c1", kind="chunk", label="c1", properties={}),  # missing
            GraphNode(
                id="c2",
                kind="chunk",
                label="c2",
                properties={"topic_id": ""},  # empty
            ),
            GraphNode(
                id="c3",
                kind="chunk",
                label="c3",
                properties={"topic_id": "kept"},
            ),
        ],
        edges=[],
    )
    monkeypatch.setattr(store, "find_subgraph_for_document", lambda _id: projection)

    provider = _provider(store)
    assert provider.topic_ids_for_document("d1") == {"kept"}


def test_known_document_ids_returns_catalog_ids() -> None:
    """``known_document_ids`` reads from the catalog, not the graph."""
    documents = DocumentService(storage=InMemoryStorageService())
    provider = _GraphStoreTopicProvider(documents=documents, graph_store=InMemoryGraphStore())
    # Empty catalog → empty list.
    assert provider.known_document_ids() == []
