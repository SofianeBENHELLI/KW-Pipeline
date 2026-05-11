"""Project a validated ``SemanticDocument`` into the knowledge graph.

The v0.2 projection (Demo KG, #144) builds a deterministic
``Document → Version → Chunk`` skeleton and enriches it with
``Chunk -belongs_to-> Topic`` membership edges and the deterministic
chunk-to-chunk semantic edges (``related_to`` / ``shares_keyword`` /
``same_topic_as``). Section nodes from the v0.1 baseline are gone —
chunks (one per ``SemanticSection``) take their place — see the
contract doc at ``docs/architecture/knowledge_graph_payload.md`` for
the rationale.

Phase 2 (ADR-012 §4 + ADR-013) adds :meth:`KnowledgeProjector.project_entities`:
given an :class:`~app.schemas.knowledge.EntityExtractionResult`, it
upserts ``(:Entity)`` nodes plus ``HAS_ENTITY`` edges that carry a
``source_reference_id`` in their properties. Every edge has a
citation — ADR-009's needs-review gate, applied to graph edges.

Entity-node id choice: the id is a deterministic hash of
``(subject_type, normalized_subject)`` so two chunks referencing
"ISO 9001" merge into one canonical node. This makes cross-document
queries possible ("which versions cite ISO 9001?") at the cost of
needing reference-counted cleanup in
:meth:`GraphStore.delete_subgraph_for_version` — see the in-memory
implementation for the pattern.

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

:meth:`KnowledgeProjector.project` is a thin orchestrator that runs
lane B's chunk-relation and topic-clustering services once, then
delegates to a sequence of stage methods that turn the precomputed
artifacts into nodes/edges. The orchestrator concatenates the stage
output and writes the result in a single delete-then-upsert pass so
the "validated → graph" transition stays atomic from the caller's
point of view.

* :meth:`project_document_structure` — Document + Version nodes plus
  the ``Version -part_of-> Document`` edge.
* :meth:`project_chunks` — Chunk nodes plus
  ``Chunk -part_of-> Version``.
* :meth:`project_chunk_relations` — deterministic semantic edges from
  :class:`ChunkRelationService` (``related_to`` / ``shares_keyword``
  / ``same_topic_as``).
* :meth:`project_topics` — Topic nodes plus
  ``Chunk -belongs_to-> Topic`` membership edges from
  :class:`TopicClusteringService`.

:meth:`project_entities` is *not* part of this orchestration — it is
invoked separately by the route handler after ``project()`` returns,
because it consumes a different upstream artifact
(:class:`EntityExtractionResult`) produced by the entity extractor.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid an import cycle at runtime — DocumentRelationsCache lives
    # in app.services and depends on knowledge.relations, which is
    # adjacent to this module. Forward-referencing the type keeps
    # the projector's runtime imports unchanged.
    from app.services.claim_extractor import ClaimExtractor
    from app.services.claim_store import ClaimStore
    from app.services.document_relations_cache import DocumentRelationsCache
    from app.services.process_store import ProcessStore

from app.schemas.document import Document, DocumentVersion
from app.schemas.knowledge import (
    ChunkNodeProperties,
    ChunkRelationEdgeProperties,
    EntityExtractionResult,
    GraphEdge,
    GraphNode,
    StructuralEdgeProperties,
    TopicMembershipEdgeProperties,
    TopicNodeProperties,
)
from app.schemas.semantic_document import SemanticDocument
from app.services.knowledge.chunk_relations import (
    ChunkRecord,
    ChunkRelation,
    ChunkRelationService,
)
from app.services.knowledge.embedding_client import EmbeddingClient
from app.services.knowledge.graph_store import GraphStore
from app.services.knowledge.topic_clustering import (
    TopicAssignment,
    TopicClusteringService,
)

log = logging.getLogger(__name__)


class KnowledgeProjector:
    """Stateless projector — holds only a :class:`GraphStore` reference.

    Construct one per ``PipelineServices`` container and reuse across
    requests; it carries no per-request state.
    """

    def __init__(
        self,
        graph_store: GraphStore,
        *,
        chunk_relation_service: ChunkRelationService | None = None,
        topic_clustering_service: TopicClusteringService | None = None,
        embedding_client: EmbeddingClient | None = None,
        document_relations_cache: DocumentRelationsCache | None = None,
        process_store: ProcessStore | None = None,
        claim_extractor: ClaimExtractor | None = None,
        claim_store: ClaimStore | None = None,
    ) -> None:
        self._graph_store = graph_store
        # Lane B (#141/#142) services. Defaulting them keeps wiring
        # changes outside this module trivial — call sites just pass a
        # ``GraphStore`` as before.
        self._chunk_relation_service = chunk_relation_service or ChunkRelationService()
        self._topic_clustering_service = topic_clustering_service or TopicClusteringService()
        # Phase 3 (#186, ADR-015): when set, the projector embeds each
        # chunk's text after writing the chunk node and stores the
        # vectors in one round-trip via
        # :meth:`GraphStore.bulk_set_chunk_embeddings` (audit #225).
        # ``None`` preserves Phase 1 / Phase 2 behaviour exactly.
        self._embedding_client = embedding_client
        # Process-local embedding cache keyed by ``(model, sha256(text))``
        # so re-projections (and re-projections of the same chunk text
        # across versions) skip the Voyage round-trip. The cache is
        # bounded by the unique-chunk count of one process; that's fine
        # for the single-API-pod deployment story.
        self._embedding_cache: dict[tuple[str, str], list[float]] = {}
        # #385: when set, the projector calls ``warm_for_document``
        # after each successful projection so the document_relations
        # cache reflects the new graph state. The first read for any
        # bridged pair becomes a cache hit instead of a slow
        # on-demand compute. ``None`` preserves the previous
        # behaviour exactly (the route's cache-miss path still warms
        # on demand).
        self._document_relations_cache = document_relations_cache
        # #390: when set, the projector runs the deterministic SOP
        # parser after the structural projection completes and
        # writes any detected :class:`Process` payload through this
        # store. ``None`` (default) preserves the pre-#390 behaviour
        # exactly — non-procedural docs continue to extract as
        # chunks without a Process row appearing.
        self._process_store = process_store
        # #392: LLM-driven Claim extractor + the SQLite-backed Claim
        # store. The pair is wired together (both-or-nothing) — if the
        # operator wires the extractor but no store, the post-projection
        # hook is a no-op so we don't run the LLM call only to throw
        # the result away. Both ``None`` (the default) preserves the
        # pre-#392 behaviour exactly.
        self._claim_extractor = claim_extractor
        self._claim_store = claim_store

    def set_document_relations_cache(
        self,
        cache: DocumentRelationsCache | None,
    ) -> None:
        """Wire the cache after construction (#385).

        ``DocumentRelationsCache`` depends on ``KnowledgeRelationsService``,
        which itself depends on the same graph store the projector
        uses. The build order is projector → relations → cache;
        this setter lets the build wire the cache onto the projector
        once both exist, without breaking the projector's
        constructor signature.

        Pass ``None`` to disable warming (used by tests + as the
        default when knowledge layer is disabled).
        """
        self._document_relations_cache = cache

    def set_process_store(self, store: ProcessStore | None) -> None:
        """Wire the Process store after construction (#390).

        Mirrors :meth:`set_document_relations_cache`: the store is
        built alongside the projector in
        :func:`app.dependencies.build_services` /
        :func:`build_persistent_services`, and this setter lets the
        wiring attach it without breaking the projector's
        constructor signature for the existing test suite that
        builds ``KnowledgeProjector(graph_store=...)`` directly.

        Pass ``None`` to disable the SOP write (used by tests + as
        the default when callers don't want Process emission).
        """
        self._process_store = store

    def set_claim_extractor(self, extractor: ClaimExtractor | None) -> None:
        """Wire the Claim extractor after construction (#392).

        Mirrors :meth:`set_document_relations_cache` so the
        wiring layer can attach the extractor without breaking the
        projector's constructor signature for the existing tests that
        build ``KnowledgeProjector(graph_store=...)`` directly.

        The post-projection hook only fires when BOTH this and the
        Claim store are set — see :meth:`set_claim_store`.
        """
        self._claim_extractor = extractor

    def set_claim_store(self, store: ClaimStore | None) -> None:
        """Wire the Claim store after construction (#392).

        See :meth:`set_claim_extractor`. Both must be wired for the
        post-projection extraction hook to run; either ``None`` is
        a valid kill switch (e.g. when the LLM is not configured).
        """
        self._claim_store = store

    def project(
        self,
        *,
        document: Document,
        version: DocumentVersion,
        semantic: SemanticDocument,
    ) -> None:
        """Idempotently write the v0.2 projection for one validated
        version.

        Existing nodes/edges for ``version.id`` are removed first, so
        re-projecting after a section rename does not leave orphan
        chunk nodes.

        Orchestrator responsibilities:

        1. Validate the ``version`` ↔ ``semantic`` pairing.
        2. Run lane B's chunk relation + topic clustering services
           once, deterministically, on the semantic document.
        3. Run each projection stage with the precomputed lane-B
           artifacts, concatenate ``(nodes, edges)``, and write in a
           single delete-then-upsert pass.

        Stages stay pure — they receive everything they need by
        keyword argument and never touch the graph store.
        """
        if version.id != semantic.document_version_id:
            raise ValueError(
                f"Semantic document {semantic.id} is not for version "
                f"{version.id} (got {semantic.document_version_id})."
            )

        chunks = self._chunk_relation_service.chunks_for(semantic)
        relations = self._chunk_relation_service.relations_for(chunks)
        assignment = self._topic_clustering_service.cluster(chunks, relations)
        chunk_to_topic = {m.chunk_id: m.topic_id for m in assignment.memberships}

        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []

        for stage_nodes, stage_edges in (
            self.project_document_structure(document=document, version=version, semantic=semantic),
            self.project_chunks(
                document=document,
                version=version,
                chunks=chunks,
                chunk_to_topic=chunk_to_topic,
            ),
            self.project_chunk_relations(document=document, version=version, relations=relations),
            self.project_topics(document=document, version=version, assignment=assignment),
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
                "chunk_count": len(chunks),
                "topic_count": len(assignment.topics),
                "chunk_relation_count": len(relations),
            },
        )

        # #390: run the deterministic SOP parser and write any
        # detected Process payload through the wired store. Fire-
        # and-log per ADR-012 §3 — a parser hiccup leaves the
        # structural projection intact; the next re-projection
        # catches up. ``None`` (store not wired) preserves the
        # pre-#390 behaviour exactly. The import is local so the
        # projector's runtime imports stay unchanged for callers
        # that never enable SOP extraction.
        if self._process_store is not None:
            try:
                from app.services.sop_extractor import extract_process

                process = extract_process(
                    semantic,
                    document=document,
                    version=version,
                )
                # Drop any prior Process row for this version
                # **unconditionally** — including when the new
                # extraction returns ``None`` (the document used to
                # be procedural and the operator edited it so
                # ``detect_sop_structure`` no longer trips). Without
                # this, the stale Process row would keep claiming
                # the version is procedural after the structure was
                # removed. Mirrors the same posture
                # ``ClaimExtractor`` uses on its hook (delete then
                # maybe save).
                self._process_store.delete_for_version(version.id)
                if process is not None:
                    self._process_store.save_process(process)
                    log.info(
                        "knowledge.process_extraction.written",
                        extra={
                            "document_id": document.id,
                            "version_id": version.id,
                            "process_id": process.id,
                            "step_count": len(process.steps),
                        },
                    )
            except Exception as exc:  # noqa: BLE001 - fire-and-log boundary
                log.warning(
                    "knowledge.process_extraction.failed",
                    extra={
                        "document_id": document.id,
                        "version_id": version.id,
                        "error_type": type(exc).__name__,
                    },
                )

        # #385: warm the document_relations cache for every pair this
        # document now bridges. Fire-and-log per ADR-012 §3 — a cache
        # warm failure leaves the structural projection intact; the
        # next route call falls through to the on-demand compute and
        # writes back. ``None`` (cache not wired) preserves the
        # previous behaviour exactly.
        if self._document_relations_cache is not None:
            try:
                self._document_relations_cache.warm_for_document(document.id)
            except Exception as exc:  # noqa: BLE001 - fire-and-log boundary
                log.warning(
                    "knowledge.document_relations.cache.warm_failed",
                    extra={
                        "document_id": document.id,
                        "version_id": version.id,
                        "error_type": type(exc).__name__,
                    },
                )

        # Phase 3 vector RAG: write per-chunk embeddings if Voyage is
        # wired. Fire-and-log per ADR-012 §3 — a Voyage hiccup leaves
        # the structural projection intact; the search index just
        # lacks the latest version's chunks until the next re-project.
        if self._embedding_client is not None and chunks:
            try:
                self._embed_and_store_chunks(
                    document_id=document.id,
                    version_id=version.id,
                    chunks=chunks,
                )
            except Exception as exc:  # noqa: BLE001 - fire-and-log boundary
                log.warning(
                    "knowledge.embeddings.failed",
                    extra={
                        "document_id": document.id,
                        "version_id": version.id,
                        "error_type": type(exc).__name__,
                    },
                )

        # #392: LLM-driven Claim extraction. Both-or-nothing — if the
        # operator wires the extractor without the store, the LLM call
        # would just throw the result away, so we gate on both being
        # set. ``delete_for_version`` runs first so a re-projection
        # replaces the prior batch atomically (the FK on
        # ``claims.version_id`` would also cascade on a version delete,
        # but a re-extraction never deletes the version row). Fire-and-log
        # per ADR-012 §3 — a Claim-extractor failure leaves the
        # structural projection (and the cache warm above) intact.
        if self._claim_extractor is not None and self._claim_store is not None:
            try:
                claims = self._claim_extractor.extract(
                    semantic,
                    document=document,
                    version=version,
                )
                self._claim_store.delete_for_version(version.id)
                if claims:
                    self._claim_store.save_claims(claims)
                log.info(
                    "knowledge.claim_extraction.written",
                    extra={
                        "document_id": document.id,
                        "version_id": version.id,
                        "claim_count": len(claims),
                    },
                )
            except Exception as exc:  # noqa: BLE001 - fire-and-log boundary
                log.warning(
                    "knowledge.claim_extraction.failed",
                    extra={
                        "document_id": document.id,
                        "version_id": version.id,
                        "error_type": type(exc).__name__,
                    },
                )

    def _embed_and_store_chunks(
        self,
        *,
        document_id: str,
        version_id: str,
        chunks: Sequence[ChunkRecord],
    ) -> None:
        """Compute + persist embeddings for one version's chunks.

        The cache key is ``(model_name, sha256(text))`` so two chunks
        with identical text — say, a boilerplate footer that recurs
        across versions — embed once and reuse. Cache hits don't
        cost a Voyage call but still write the vector to the new
        chunk node so the index covers it.
        """
        assert self._embedding_client is not None  # narrowed by caller
        model = self._embedding_client.name
        # Index parallel to ``texts_to_embed`` so we can write back
        # cache entries after the network call.
        cache_keys: list[tuple[str, str]] = []
        for chunk in chunks:
            digest = hashlib.sha256((chunk.text or "").encode("utf-8")).hexdigest()
            cache_keys.append((model, digest))

        misses_idx: list[int] = []
        misses_text: list[str] = []
        for i, chunk in enumerate(chunks):
            if cache_keys[i] not in self._embedding_cache:
                misses_idx.append(i)
                misses_text.append(chunk.text or "")

        if misses_text:
            new_vectors = self._embedding_client.embed_documents(misses_text)
            if len(new_vectors) != len(misses_text):
                # The provider broke its own contract; surface as a
                # warning and abandon the embedding pass. The structural
                # projection already wrote successfully.
                raise RuntimeError(
                    f"embedding client returned {len(new_vectors)} vectors "
                    f"for {len(misses_text)} inputs."
                )
            for slot, vector in zip(misses_idx, new_vectors, strict=True):
                self._embedding_cache[cache_keys[slot]] = list(vector)

        # Single bulk write: one UNWIND transaction on Neo4j; one dict
        # update under the lock on the in-memory store. Replaces the
        # previous per-chunk round-trip (audit #225). Empty chunk lists
        # are unreachable here because the caller checks ``chunks``
        # before invoking this method.
        embedding_map = {
            chunk.chunk_id: self._embedding_cache[cache_keys[i]] for i, chunk in enumerate(chunks)
        }
        self._graph_store.bulk_set_chunk_embeddings(embedding_map)

        log.info(
            "knowledge.embeddings.computed",
            extra={
                "document_id": document_id,
                "version_id": version_id,
                "chunk_count": len(chunks),
                "embedding_model": model,
                "cache_hits": len(chunks) - len(misses_text),
                "embedded_count": len(misses_text),
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
        """v0.2 skeleton: Document and Version nodes + the
        ``Version -part_of-> Document`` edge.

        Section nodes from the v0.1 baseline are gone; chunks
        (:meth:`project_chunks`) take their place per the contract
        doc's "Should section nodes be removed once chunk is in place?"
        question, resolved here as part of #144.
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
        version_edge = GraphEdge(
            id=f"{version.id}->part_of->{document.id}",
            kind="part_of",
            source_id=version.id,
            target_id=document.id,
            properties=StructuralEdgeProperties(
                document_id=document.id,
                version_id=version.id,
            ).model_dump(),
        )
        return [document_node, version_node], [version_edge]

    @staticmethod
    def project_chunks(
        *,
        document: Document,
        version: DocumentVersion,
        chunks: Sequence[ChunkRecord],
        chunk_to_topic: dict[str, str],
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Emit one ``chunk`` node per :class:`ChunkRecord` plus the
        ``Chunk -part_of-> Version`` skeleton edge.

        ``chunk_to_topic`` is read for the ``topic_id`` field on the
        chunk node properties — the topic itself is emitted by
        :meth:`project_topics`. Chunks with no topic membership get
        ``topic_id = None``.
        """
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        for chunk in chunks:
            text_preview = (chunk.text or "").strip().replace("\n", " ")
            if len(text_preview) > 200:
                text_preview = text_preview[:199].rstrip() + "…"
            nodes.append(
                GraphNode(
                    id=chunk.chunk_id,
                    kind="chunk",
                    label=chunk.heading or "Untitled chunk",
                    properties=ChunkNodeProperties(
                        document_id=document.id,
                        version_id=version.id,
                        chunk_id=chunk.chunk_id,
                        section_id=chunk.section_id,
                        heading=chunk.heading,
                        text_preview=text_preview or None,
                        char_count=chunk.char_count,
                        keywords=list(chunk.keywords),
                        topic_id=chunk_to_topic.get(chunk.chunk_id),
                    ).model_dump(),
                )
            )
            edges.append(
                GraphEdge(
                    id=f"{chunk.chunk_id}->part_of->{version.id}",
                    kind="part_of",
                    source_id=chunk.chunk_id,
                    target_id=version.id,
                    properties=StructuralEdgeProperties(
                        document_id=document.id,
                        version_id=version.id,
                        chunk_id=chunk.chunk_id,
                        section_id=chunk.section_id,
                    ).model_dump(),
                )
            )
        return nodes, edges

    @staticmethod
    def project_chunk_relations(
        *,
        document: Document,
        version: DocumentVersion,
        relations: Sequence[ChunkRelation],
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Emit one deterministic semantic edge per
        :class:`ChunkRelation`. The edge ``kind`` mirrors the relation
        kind (``related_to`` / ``shares_keyword`` / ``same_topic_as``);
        properties carry the audit trail (``score``, ``reason``,
        ``shared_keywords``, ``source_chunk_id``, ``target_chunk_id``)
        per the v0.2 contract.
        """
        edges = [
            GraphEdge(
                id=(
                    f"{version.id}:{relation.source_chunk_id}->"
                    f"{relation.kind}->{relation.target_chunk_id}"
                ),
                kind=relation.kind,
                source_id=relation.source_chunk_id,
                target_id=relation.target_chunk_id,
                properties=ChunkRelationEdgeProperties(
                    document_id=document.id,
                    version_id=version.id,
                    source_chunk_id=relation.source_chunk_id,
                    target_chunk_id=relation.target_chunk_id,
                    score=relation.score,
                    reason=relation.reason,
                    shared_keywords=list(relation.shared_keywords),
                ).model_dump(),
            )
            for relation in relations
        ]
        return [], edges

    @staticmethod
    def project_topics(
        *,
        document: Document,
        version: DocumentVersion,
        assignment: TopicAssignment,
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Emit one ``topic`` node per cluster plus the
        ``Chunk -belongs_to-> Topic`` membership edges.
        """
        nodes = [
            GraphNode(
                id=topic.topic_id,
                kind="topic",
                label=topic.label,
                properties=TopicNodeProperties(
                    document_id=document.id,
                    version_id=version.id,
                    topic_id=topic.topic_id,
                    label=topic.label,
                    keywords=list(topic.keywords),
                    summary=topic.summary,
                    chunk_count=len(topic.chunk_ids),
                    chunk_ids=list(topic.chunk_ids),
                ).model_dump(),
            )
            for topic in assignment.topics
        ]
        edges = [
            GraphEdge(
                id=(f"{version.id}:{membership.chunk_id}->belongs_to->{membership.topic_id}"),
                kind="belongs_to",
                source_id=membership.chunk_id,
                target_id=membership.topic_id,
                properties=TopicMembershipEdgeProperties(
                    document_id=document.id,
                    version_id=version.id,
                    chunk_id=membership.chunk_id,
                    topic_id=membership.topic_id,
                    score=membership.score,
                ).model_dump(),
            )
            for membership in assignment.memberships
        ]
        return nodes, edges

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
