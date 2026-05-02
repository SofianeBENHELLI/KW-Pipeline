"""Project a validated ``SemanticDocument`` into the knowledge graph.

Phase 1 builds a deterministic node-and-edge skeleton: one
``Document`` node per family, one ``Version`` node per validated
version, one ``Section`` node per ``SemanticSection`` of that
version, and ``PART_OF`` edges connecting them.

Phase 2 (ADR-012 §4 + ADR-013) adds :meth:`KnowledgeProjector.project_entities`:
given an :class:`~app.schemas.knowledge.EntityExtractionResult`, it
upserts ``(:Entity)`` nodes plus ``HAS_ENTITY`` edges that carry a
``source_reference_id`` in their properties. Every edge has a
citation — ADR-009's needs-review gate, applied to graph edges.

Entity-node id choice: the id is a deterministic hash of
``(subject_type, normalized_subject)`` so two sections referencing
"ISO 9001" merge into one canonical node. This makes cross-document
queries possible ("which versions cite ISO 9001?") at the cost of
needing reference-counted cleanup in :meth:`GraphStore.delete_subgraph_for_version`
— see the in-memory implementation for the pattern.

The projector is invoked as a fire-and-log side-effect of the
``NEEDS_REVIEW → VALIDATED`` route handler in
``apps/api/app/routes.py``. Failures are logged but do not roll back
validation: the catalog stays correct, the graph catches up later via
re-projection or out-of-band reconciliation.

Re-projecting the same version is safe — :meth:`KnowledgeProjector.project`
deletes the version's prior subgraph before upserting the new one, so
section renames or removals don't leave orphans.

Projection stages
-----------------

:meth:`KnowledgeProjector.project` is a thin orchestrator that
delegates to a sequence of stage methods. Each stage returns the
``(nodes, edges)`` it contributes; the orchestrator concatenates them
and writes the result in a single delete-then-upsert pass so the
"validated → graph" transition stays atomic from the caller's point of
view.

* :meth:`project_document_structure` — Document/Version/Section nodes
  + PART_OF edges. The Phase 1 baseline.
* :meth:`project_chunks` — chunk nodes (no-op skeleton; lane B fills).
* :meth:`project_chunk_relations` — chunk → section / chunk → chunk
  edges (no-op skeleton; lane B fills).
* :meth:`project_topics` — topic nodes + section/chunk attachments
  (no-op skeleton; lane B fills).

:meth:`project_entities` is *not* part of this orchestration — it is
invoked separately by the route handler after ``project()`` returns,
because it consumes a different upstream artifact
(:class:`EntityExtractionResult`) produced by the entity extractor.
"""

from __future__ import annotations

import hashlib
import logging

from app.schemas.document import Document, DocumentVersion
from app.schemas.knowledge import (
    EntityExtractionResult,
    GraphEdge,
    GraphNode,
)
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

        This method is a thin orchestrator: it validates the
        ``version`` ↔ ``semantic`` pairing, runs each projection stage
        in order, then writes the accumulated nodes/edges in one
        delete-then-upsert pass. New stages plug in by returning their
        ``(nodes, edges)`` from a stage method below — no orchestration
        changes needed.
        """
        if version.id != semantic.document_version_id:
            raise ValueError(
                f"Semantic document {semantic.id} is not for version "
                f"{version.id} (got {semantic.document_version_id})."
            )

        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []

        for stage_nodes, stage_edges in (
            self.project_document_structure(
                document=document, version=version, semantic=semantic
            ),
            self.project_chunks(
                document=document, version=version, semantic=semantic
            ),
            self.project_chunk_relations(
                document=document, version=version, semantic=semantic
            ),
            self.project_topics(
                document=document, version=version, semantic=semantic
            ),
        ):
            nodes.extend(stage_nodes)
            edges.extend(stage_edges)

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

    # ------------------------------------------------------------------
    # Projection stages
    #
    # Each stage is a pure function of its inputs: it returns the
    # ``(nodes, edges)`` it contributes and never touches the graph
    # store directly. The orchestrator above owns the single
    # delete-then-upsert write.
    # ------------------------------------------------------------------

    @staticmethod
    def project_document_structure(
        *,
        document: Document,
        version: DocumentVersion,
        semantic: SemanticDocument,
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Phase 1 skeleton: Document/Version/Section + PART_OF edges.

        This is the byte-for-byte projection that has shipped since
        ADR-012 §3. Anything that depends on a stable Phase 1 wire
        contract (frontend graph panel, validation tests) reads from
        here.
        """
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
        nodes: list[GraphNode] = [document_node, version_node, *section_nodes]

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
        edges: list[GraphEdge] = [version_edge, *section_edges]

        return nodes, edges

    @staticmethod
    def project_chunks(
        *,
        document: Document,  # noqa: ARG004 — placeholder for lane B
        version: DocumentVersion,  # noqa: ARG004 — placeholder for lane B
        semantic: SemanticDocument,  # noqa: ARG004 — placeholder for lane B
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Project chunk nodes — *skeleton, filled by lane B (#141/#142)*.

        Returns empty lists today. The signature is fixed so #144 can
        wire the chunker into the orchestrator without touching
        :meth:`project`.
        """
        return [], []

    @staticmethod
    def project_chunk_relations(
        *,
        document: Document,  # noqa: ARG004 — placeholder for lane B
        version: DocumentVersion,  # noqa: ARG004 — placeholder for lane B
        semantic: SemanticDocument,  # noqa: ARG004 — placeholder for lane B
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Project chunk → section / chunk → chunk edges — *skeleton*.

        Returns empty lists today. Lane B (#141/#142) fills this with
        ``BELONGS_TO`` / ``NEXT_CHUNK`` style edges; the integration PR
        (#144) feeds it the chunk artefacts.
        """
        return [], []

    @staticmethod
    def project_topics(
        *,
        document: Document,  # noqa: ARG004 — placeholder for lane B
        version: DocumentVersion,  # noqa: ARG004 — placeholder for lane B
        semantic: SemanticDocument,  # noqa: ARG004 — placeholder for lane B
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Project topic nodes + attachments — *skeleton, filled by lane B*.

        Returns empty lists today. Topics attach to sections (and later
        to chunks) via ``ABOUT`` edges in the v0.2 wire contract.
        """
        return [], []

    def project_entities(self, result: EntityExtractionResult) -> None:
        """Upsert ``(:Entity)`` nodes + ``HAS_ENTITY`` edges from one
        extraction pass.

        Idempotent: re-running with the same input is a no-op modulo
        the ``HAS_ENTITY`` edge timestamps. Note we do NOT call
        :meth:`GraphStore.delete_subgraph_for_version` here — that is
        already called by :meth:`project` before this method runs.
        Calling it twice would purge the section nodes we just wrote.

        Triples that did not pass the extractor's source-reference
        validation never reach this method (they live in
        ``result.warnings``). Defence in depth: we still skip any
        triple here whose ``source_reference_ids`` is empty, so a
        misconfigured extractor cannot push uncited edges into the
        graph.
        """
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        seen_node_ids: set[str] = set()

        for triple in result.triples:
            if not triple.source_reference_ids:
                # Defensive guard: schema enforces min_length=1, but
                # belt-and-braces if a future caller bypasses Pydantic.
                log.warning(
                    "knowledge.entity_projection.uncited_triple_skipped",
                    extra={
                        "document_id": result.document_id,
                        "version_id": result.version_id,
                        "subject": triple.subject,
                    },
                )
                continue

            subject_id = _entity_id(triple.subject, triple.subject_type)
            object_id = _entity_id(triple.object, triple.object_type)
            if subject_id not in seen_node_ids:
                nodes.append(_entity_node(triple.subject, triple.subject_type, subject_id))
                seen_node_ids.add(subject_id)
            if object_id not in seen_node_ids:
                nodes.append(_entity_node(triple.object, triple.object_type, object_id))
                seen_node_ids.add(object_id)

            # One HAS_ENTITY edge per cited reference. The source-ref
            # id lives on the edge so the audit trail is per-citation;
            # querying "which references support X relates_to Y?"
            # walks edges, not properties.
            for ref_id in triple.source_reference_ids:
                edge_id = (
                    f"{result.version_id}:{triple.source_section_id}:"
                    f"{subject_id}->{triple.predicate}->{object_id}:{ref_id}"
                )
                edges.append(
                    GraphEdge(
                        id=edge_id,
                        kind="has_entity",
                        source_id=subject_id,
                        target_id=object_id,
                        properties={
                            "document_id": result.document_id,
                            "version_id": result.version_id,
                            "section_id": triple.source_section_id,
                            "predicate": triple.predicate,
                            "confidence": triple.confidence,
                            "source_reference_id": ref_id,
                        },
                    )
                )

        if not nodes and not edges:
            return

        # NB: entity nodes do NOT carry version_id in properties even
        # though we want them tracked per-version for cleanup. We pass
        # a synthetic ``version_id`` property purely for the in-memory
        # store's reverse-index bookkeeping; on re-projection it is
        # used by ``delete_subgraph_for_version`` reference counting.
        # The original subject/type live on the node for queries.
        for node in nodes:
            node.properties["version_id"] = result.version_id
            node.properties["document_id"] = result.document_id

        self._graph_store.upsert_nodes(nodes)
        self._graph_store.upsert_edges(edges)

        log.info(
            "knowledge.entity_projection.written",
            extra={
                "document_id": result.document_id,
                "version_id": result.version_id,
                "store": getattr(self._graph_store, "name", "unknown"),
                "entity_node_count": len(nodes),
                "has_entity_edge_count": len(edges),
                "warning_count": len(result.warnings),
                "token_usage": result.token_usage,
            },
        )


def _entity_id(text: str, entity_type: str) -> str:
    """Stable hash of ``(type, text)`` so canonical entities merge.

    Lowercases and trims whitespace before hashing so trivial casing
    differences ("ISO 9001" vs "iso 9001") collapse into one node.
    """
    normalized = f"{entity_type.strip().lower()}::{text.strip().lower()}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"entity-{digest}"


def _entity_node(subject: str, subject_type: str, node_id: str) -> GraphNode:
    return GraphNode(
        id=node_id,
        kind="entity",
        label=subject,
        properties={
            "subject": subject,
            "subject_type": subject_type,
        },
    )


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
