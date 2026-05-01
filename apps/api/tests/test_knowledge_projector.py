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


def test_project_writes_document_version_section_nodes_and_part_of_edges():
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
    assert ("section", "s1") in kinds
    assert ("section", "s2") in kinds

    # Three PART_OF edges: each section → version, version → document.
    edge_pairs = {(e.source_id, e.target_id) for e in proj.edges}
    assert edge_pairs == {
        (version.id, document.id),
        ("s1", version.id),
        ("s2", version.id),
    }
    for edge in proj.edges:
        assert edge.kind == "part_of"


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
    # 1 document + 1 version + 1 section = 3 nodes; no duplicates.
    assert len(page.nodes) == 3
    assert len(page.edges) == 2


def test_project_removes_orphan_section_on_rename():
    """A version that drops a section between projections should not
    leave the old section node behind."""
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
    section_ids = {n.id for n in proj.nodes if n.kind == "section"}
    assert section_ids == {"s1"}, "Old section s2 must be deleted on re-projection"


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
