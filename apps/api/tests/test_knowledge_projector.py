"""Tests for ``KnowledgeProjector`` against a fake ``GraphStore``.

These tests are the contract for Phase 1: given a validated
``SemanticDocument``, the projector must produce a deterministic
node/edge skeleton, must be safe to re-run (idempotent), and must
clean up its own prior version's nodes before re-projecting (so
section renames don't orphan).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.schemas.knowledge import GraphEdge, GraphNode
from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticDocument,
    SemanticSection,
)
from app.services.knowledge.graph_store import GraphStore, InMemoryGraphStore
from app.services.knowledge.projector import KnowledgeProjector


def _make_document(*, version: DocumentVersion) -> Document:
    return Document(
        id=version.document_id,
        original_filename=version.filename,
        latest_version_id=version.id,
        versions=[version],
    )


def _make_version(*, document_id="doc-1", version_id="ver-1") -> DocumentVersion:
    return DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=1,
        filename="policy.txt",
        content_type="text/plain",
        file_size=42,
        sha256="0" * 64,
        storage_uri="file://fake",
        status=DocumentVersionStatus.VALIDATED,
    )


def _make_semantic(
    *,
    version: DocumentVersion,
    sections: list[SemanticSection],
) -> SemanticDocument:
    return SemanticDocument(
        id=f"sem-{version.id}",
        document_version_id=version.id,
        document_profile=DocumentProfile(title="Test Doc"),
        sections=sections,
        validation_status="validated",
        markdown="# Test\n",
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


def test_project_writes_document_version_chunk_nodes_and_part_of_edges():
    """v0.2 baseline: Document, Version, Chunk nodes + PART_OF skeleton.

    Sections-as-nodes were dropped in #144 — chunks (1:1 with
    ``SemanticSection`` today) take their place.
    """
    store: GraphStore = cast(GraphStore, InMemoryGraphStore())
    projector = KnowledgeProjector(graph_store=store)

    version = _make_version()
    document = _make_document(version=version)
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(id="s1", heading="Intro", text="hello"),
            SemanticSection(id="s2", heading="Body", text="world"),
        ],
    )

    projector.project(document=document, version=version, semantic=semantic)

    proj = store.find_subgraph_for_document(document.id)
    kinds = {(n.kind, n.id) for n in proj.nodes}
    assert ("document", document.id) in kinds
    assert ("version", version.id) in kinds
    assert ("chunk", "s1") in kinds
    assert ("chunk", "s2") in kinds
    assert all(n.kind != "section" for n in proj.nodes)

    # Skeleton PART_OF edges: each chunk → version, version → document.
    part_of_pairs = {(e.source_id, e.target_id) for e in proj.edges if e.kind == "part_of"}
    assert part_of_pairs == {
        (version.id, document.id),
        ("s1", version.id),
        ("s2", version.id),
    }


def test_project_is_idempotent():
    store = InMemoryGraphStore()
    projector = KnowledgeProjector(graph_store=store)

    version = _make_version()
    document = _make_document(version=version)
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="x")],
    )

    projector.project(document=document, version=version, semantic=semantic)
    projector.project(document=document, version=version, semantic=semantic)

    page = store.find_subgraph(limit=20)
    # 1 document + 1 version + 1 chunk = 3 nodes; no duplicates.
    assert len(page.nodes) == 3
    # 1 PART_OF (version → document) + 1 PART_OF (chunk → version).
    # Single chunk → no chunk-relation edges, no topic.
    assert len(page.edges) == 2


def test_project_removes_orphan_chunk_on_rename():
    """A version that drops a section between projections should not
    leave the old chunk node behind."""
    store = InMemoryGraphStore()
    projector = KnowledgeProjector(graph_store=store)

    version = _make_version()
    document = _make_document(version=version)

    first = _make_semantic(
        version=version,
        sections=[
            SemanticSection(id="s1", heading="A", text="x"),
            SemanticSection(id="s2", heading="B", text="y"),
        ],
    )
    projector.project(document=document, version=version, semantic=first)

    # Re-project after dropping s2 (e.g. the reviewer reorganised the
    # semantic JSON before validating again).
    second = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A revised", text="x")],
    )
    projector.project(document=document, version=version, semantic=second)

    proj = store.find_subgraph_for_document(document.id)
    chunk_ids = {n.id for n in proj.nodes if n.kind == "chunk"}
    assert chunk_ids == {"s1"}, "Old chunk s2 must be deleted on re-projection"


def test_project_rejects_mismatched_semantic_doc():
    store = InMemoryGraphStore()
    projector = KnowledgeProjector(graph_store=store)

    version = _make_version(version_id="ver-A")
    document = _make_document(version=version)
    other_version = _make_version(version_id="ver-B")
    semantic_for_other = _make_semantic(version=other_version, sections=[])

    try:
        projector.project(document=document, version=version, semantic=semantic_for_other)
    except ValueError as exc:
        assert "ver-A" in str(exc) and "ver-B" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for mismatched version")


def test_in_memory_graph_store_implements_protocol():
    """``GraphStore`` is ``@runtime_checkable``; verify the in-memory
    impl actually satisfies the Protocol so Phase 2 implementations
    can use ``isinstance`` to assert conformance in tests."""
    store = InMemoryGraphStore()
    assert isinstance(store, GraphStore)


def test_v0_2_projection_emits_chunks_topics_and_semantic_edges():
    """End-to-end v0.2 contract (#144): a multi-section document with
    overlapping content produces chunks, a topic, ``belongs_to``
    membership edges, and chunk-to-chunk semantic edges. No section
    nodes, no LLM dependency.
    """
    store: GraphStore = cast(GraphStore, InMemoryGraphStore())
    projector = KnowledgeProjector(graph_store=store)

    version = _make_version()
    document = _make_document(version=version)
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="alpha",
                heading="Audit Plan",
                text=(
                    "Quality audit programmes evaluate supplier "
                    "performance. The audit team reviews supplier "
                    "records and supplier deliverables every quarter."
                ),
            ),
            SemanticSection(
                id="beta",
                heading="Audit Findings",
                text=(
                    "Audit findings categorise supplier performance "
                    "gaps. Each supplier programme tracks supplier "
                    "corrective actions to closure."
                ),
            ),
            SemanticSection(
                id="gamma",
                heading="Audit Closure",
                text=(
                    "Audit closure requires supplier corrective actions "
                    "to be approved by the audit team."
                ),
            ),
        ],
    )

    projector.project(document=document, version=version, semantic=semantic)
    proj = store.find_subgraph_for_document(document.id)

    kinds_by_id = {n.id: n.kind for n in proj.nodes}
    assert kinds_by_id[document.id] == "document"
    assert kinds_by_id[version.id] == "version"
    assert kinds_by_id["alpha"] == "chunk"
    assert kinds_by_id["beta"] == "chunk"
    assert kinds_by_id["gamma"] == "chunk"

    topic_nodes = [n for n in proj.nodes if n.kind == "topic"]
    assert len(topic_nodes) == 1
    topic = topic_nodes[0]
    assert topic.id.startswith("topic-")
    assert sorted(topic.properties["chunk_ids"]) == ["alpha", "beta", "gamma"]

    edge_kinds = {e.kind for e in proj.edges}
    assert "part_of" in edge_kinds
    assert "belongs_to" in edge_kinds
    # At least one chunk-to-chunk semantic edge (these are heavily
    # overlapping audit paragraphs — the relation service produces
    # ``same_topic_as`` edges between them).
    assert edge_kinds & {"related_to", "shares_keyword", "same_topic_as"}

    # Every belongs_to edge points from a chunk to the topic node.
    membership_edges = [e for e in proj.edges if e.kind == "belongs_to"]
    assert {e.source_id for e in membership_edges} == {"alpha", "beta", "gamma"}
    assert {e.target_id for e in membership_edges} == {topic.id}

    # Every chunk-relation edge carries the audit trail required by
    # lane C's smoke contract (#146).
    for edge in proj.edges:
        if edge.kind in {"related_to", "shares_keyword", "same_topic_as"}:
            assert edge.properties["reason"]
            assert edge.properties["shared_keywords"]
            assert 0.0 <= float(edge.properties["score"]) <= 1.0


def test_v0_2_projection_works_without_anthropic_api_key(monkeypatch):
    """Acceptance criterion: graph works after validation without
    ``ANTHROPIC_API_KEY``. The deterministic path used by #144 has no
    LLM dependency, so we just delete the env var and re-run.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store: GraphStore = cast(GraphStore, InMemoryGraphStore())
    projector = KnowledgeProjector(graph_store=store)

    version = _make_version()
    document = _make_document(version=version)
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(id="s1", heading="A", text="hello world"),
            SemanticSection(id="s2", heading="B", text="goodbye world"),
        ],
    )

    # Should not raise.
    projector.project(document=document, version=version, semantic=semantic)
    proj = store.find_subgraph_for_document(document.id)
    assert any(n.kind == "chunk" for n in proj.nodes)


def test_graph_node_and_edge_round_trip_through_store():
    store = InMemoryGraphStore()
    node = GraphNode(id="x", kind="entity", label="test", properties={"k": "v"})
    edge = GraphEdge(
        id="x->y",
        kind="has_entity",
        source_id="x",
        target_id="y",
        properties={"source_reference_id": "src-1"},
    )
    other = GraphNode(id="y", kind="entity", label="other", properties={})
    store.upsert_nodes([node, other])
    store.upsert_edges([edge])

    page = store.find_subgraph(limit=20)
    assert {n.id for n in page.nodes} == {"x", "y"}
    assert page.edges[0].properties["source_reference_id"] == "src-1"


# ─── Phase 3 embedding write path (ADR-015 / #186) ───────────────────────


def test_project_writes_chunk_embeddings_when_client_set():
    from app.services.knowledge import FakeEmbeddingClient

    store = InMemoryGraphStore()
    embedder = FakeEmbeddingClient(dim=16)
    projector = KnowledgeProjector(graph_store=store, embedding_client=embedder)

    version = _make_version()
    document = _make_document(version=version)
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(id="s1", heading="Intro", text="hello world"),
            SemanticSection(id="s2", heading="Body", text="goodbye world"),
        ],
    )

    projector.project(document=document, version=version, semantic=semantic)

    # Both chunks must be retrievable via the vector index.
    hits = store.find_chunks_by_similarity(embedder.embed_query("hello world"), limit=10)
    assert {h.chunk_id for h in hits} == {"s1", "s2"}


def test_project_skips_embeddings_when_client_unset():
    """No embedding client ⇒ projection still works, search index empty."""
    store = InMemoryGraphStore()
    projector = KnowledgeProjector(graph_store=store, embedding_client=None)

    version = _make_version()
    document = _make_document(version=version)
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="x")],
    )
    projector.project(document=document, version=version, semantic=semantic)

    # No vectors written; the chunk node still exists.
    assert "s1" in {n.id for n in store.find_subgraph(limit=50).nodes}
    hits = store.find_chunks_by_similarity([1.0] * 16, limit=10)
    assert hits == []


def test_project_caches_embeddings_by_text_sha256():
    """Re-projecting the same chunk text reuses the cached vector."""
    from app.services.knowledge import FakeEmbeddingClient

    store = InMemoryGraphStore()
    embedder = FakeEmbeddingClient(dim=16)
    projector = KnowledgeProjector(graph_store=store, embedding_client=embedder)

    version = _make_version()
    document = _make_document(version=version)
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(id="s1", heading="A", text="boilerplate"),
            SemanticSection(id="s2", heading="B", text="boilerplate"),  # same text
        ],
    )
    projector.project(document=document, version=version, semantic=semantic)

    # Re-project the same content; the cache should hold and only one
    # embed_documents call should be issued at most across the second
    # invocation.
    embedder.calls.clear()
    projector.project(document=document, version=version, semantic=semantic)

    embed_calls = [c for c in embedder.calls if c["method"] == "embed_documents"]
    # Either zero (full cache hit) or one with empty inputs — never a
    # call with two unique texts, since both sections share the digest.
    if embed_calls:
        for c in embed_calls:
            assert c["texts"] == []


def test_project_embedding_failure_does_not_break_projection():
    """A flaky embedding provider must not roll back the structural
    projection — the catalog stays the source of truth."""

    class FlakyEmbedder:
        name = "flaky"
        dim = 16

        def embed_documents(self, texts):
            raise RuntimeError("voyage 503")

        def embed_query(self, query):
            raise RuntimeError("voyage 503")

    store = InMemoryGraphStore()
    projector = KnowledgeProjector(graph_store=store, embedding_client=FlakyEmbedder())

    version = _make_version()
    document = _make_document(version=version)
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="x")],
    )

    # Must not raise.
    projector.project(document=document, version=version, semantic=semantic)

    proj = store.find_subgraph_for_document(document.id)
    assert "s1" in {n.id for n in proj.nodes if n.kind == "chunk"}


def test_project_handles_embedding_provider_count_mismatch():
    """Provider returns the wrong number of vectors → log warning, no
    crash. The structural projection must still land."""

    class WrongCountEmbedder:
        name = "wrong-count"
        dim = 16

        def embed_documents(self, texts):
            return []  # one too few

        def embed_query(self, query):
            return [0.0] * 16

    store = InMemoryGraphStore()
    projector = KnowledgeProjector(graph_store=store, embedding_client=WrongCountEmbedder())

    version = _make_version()
    document = _make_document(version=version)
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="x")],
    )

    projector.project(document=document, version=version, semantic=semantic)

    proj = store.find_subgraph_for_document(document.id)
    assert "s1" in {n.id for n in proj.nodes if n.kind == "chunk"}


def test_project_uses_bulk_embedding_write_path():
    """The projector must write all chunk embeddings in ONE bulk call,
    not N single-chunk calls (audit #225). This guarantees one Cypher
    UNWIND transaction on Neo4j instead of N round-trips."""
    from app.services.knowledge import FakeEmbeddingClient

    store = InMemoryGraphStore()
    embedder = FakeEmbeddingClient(dim=16)
    projector = KnowledgeProjector(graph_store=store, embedding_client=embedder)

    # Recording wrappers around the two write paths so the assertion
    # is on the call shape, not on side-effects.
    bulk_calls: list[dict[str, list[float]]] = []
    single_calls: list[str] = []
    real_bulk = store.bulk_set_chunk_embeddings
    real_single = store.set_chunk_embedding

    def recording_bulk(mapping):
        bulk_calls.append(dict(mapping))
        real_bulk(mapping)

    def recording_single(*, chunk_id, embedding):
        single_calls.append(chunk_id)
        real_single(chunk_id=chunk_id, embedding=embedding)

    store.bulk_set_chunk_embeddings = recording_bulk  # type: ignore[method-assign]
    store.set_chunk_embedding = recording_single  # type: ignore[method-assign]

    version = _make_version()
    document = _make_document(version=version)
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id=f"s{i}", heading=f"H{i}", text=f"text {i}") for i in range(5)],
    )

    projector.project(document=document, version=version, semantic=semantic)

    # Exactly one bulk call covering all 5 chunks; zero single calls.
    assert len(bulk_calls) == 1, f"expected one bulk call, got {len(bulk_calls)}"
    assert set(bulk_calls[0].keys()) == {f"s{i}" for i in range(5)}
    assert single_calls == [], (
        f"projector should not fall back to per-chunk writes; got {single_calls}"
    )
