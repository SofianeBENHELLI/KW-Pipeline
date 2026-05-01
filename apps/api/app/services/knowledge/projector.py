"""Project a validated ``SemanticDocument`` into the knowledge graph.

Phase 1 builds a deterministic node-and-edge skeleton: one
``Document`` node per family, one ``Version`` node per validated
version, one ``Section`` node per ``SemanticSection`` of that
version, and ``PART_OF`` edges connecting them. No LLM, no
entities — those land in Phase 2 (ADR-013).

The projector is invoked as a fire-and-log side-effect of the
``NEEDS_REVIEW → VALIDATED`` route handler in
``apps/api/app/routes.py``. Failures are logged but do not roll back
validation: the catalog stays correct, the graph catches up later via
re-projection or out-of-band reconciliation.

Re-projecting the same version is safe — :meth:`KnowledgeProjector.project`
deletes the version's prior subgraph before upserting the new one, so
section renames or removals don't leave orphans.
"""

from __future__ import annotations

import logging

from app.schemas.document import Document, DocumentVersion
from app.schemas.knowledge import GraphEdge, GraphNode
from app.schemas.semantic_document import SemanticDocument, SemanticSection
from app.services.knowledge.graph_store import GraphStore

log = logging.getLogger(__name__)


class KnowledgeProjector:
    """Stateless projector — holds only a :class:`GraphStore` reference.

    Construct one per ``PipelineServices`` container and reuse across
    requests; it carries no per-request state.
    """

    def __init__(self, graph_store: GraphStore) -> None:
        self._graph_store = graph_store

    def project(
        self,
        *,
        document: Document,
        version: DocumentVersion,
        semantic: SemanticDocument,
    ) -> None:
        """Idempotently write the projection for one validated version.

        Existing nodes/edges for ``version.id`` are removed first, so
        re-projecting after a section rename does not leave orphan
        section nodes.
        """
        if version.id != semantic.document_version_id:
            raise ValueError(
                f"Semantic document {semantic.id} is not for version "
                f"{version.id} (got {semantic.document_version_id})."
            )

        nodes = self._build_nodes(document=document, version=version, semantic=semantic)
        edges = self._build_edges(document=document, version=version, semantic=semantic)

        self._graph_store.delete_subgraph_for_version(
            document_id=document.id,
            version_id=version.id,
        )
        self._graph_store.upsert_nodes(nodes)
        self._graph_store.upsert_edges(edges)

        log.info(
            "knowledge.projection.written",
            extra={
                "document_id": document.id,
                "version_id": version.id,
                "store": getattr(self._graph_store, "name", "unknown"),
                "node_count": len(nodes),
                "edge_count": len(edges),
            },
        )

    @staticmethod
    def _build_nodes(
        *,
        document: Document,
        version: DocumentVersion,
        semantic: SemanticDocument,
    ) -> list[GraphNode]:
        document_node = GraphNode(
            id=document.id,
            kind="document",
            label=document.original_filename,
            properties={
                "document_id": document.id,
                "original_filename": document.original_filename,
                "latest_version_id": document.latest_version_id,
            },
        )
        version_node = GraphNode(
            id=version.id,
            kind="version",
            label=f"v{version.version_number} — {version.filename}",
            properties={
                "document_id": document.id,
                "version_id": version.id,
                "version_number": version.version_number,
                "filename": version.filename,
                "sha256": version.sha256,
                "validation_status": semantic.validation_status,
            },
        )
        section_nodes = [
            _section_node(version=version, document=document, section=section)
            for section in semantic.sections
        ]
        return [document_node, version_node, *section_nodes]

    @staticmethod
    def _build_edges(
        *,
        document: Document,
        version: DocumentVersion,
        semantic: SemanticDocument,
    ) -> list[GraphEdge]:
        version_edge = GraphEdge(
            id=f"{version.id}->part_of->{document.id}",
            kind="part_of",
            source_id=version.id,
            target_id=document.id,
            properties={
                "document_id": document.id,
                "version_id": version.id,
            },
        )
        section_edges = [
            GraphEdge(
                id=f"{section.id}->part_of->{version.id}",
                kind="part_of",
                source_id=section.id,
                target_id=version.id,
                properties={
                    "document_id": document.id,
                    "version_id": version.id,
                    "section_id": section.id,
                },
            )
            for section in semantic.sections
        ]
        return [version_edge, *section_edges]


def _section_node(
    *,
    version: DocumentVersion,
    document: Document,
    section: SemanticSection,
) -> GraphNode:
    return GraphNode(
        id=section.id,
        kind="section",
        label=section.heading or "Untitled section",
        properties={
            "document_id": document.id,
            "version_id": version.id,
            "section_id": section.id,
            "heading": section.heading,
            "source_reference_count": len(section.source_reference_ids),
        },
    )
