"""Service-layer tests for the relation evidence API (#311, ADR-028).

Drives :class:`KnowledgeRelationsService` directly against an
:class:`InMemoryGraphStore` so the projection logic (kind →
provenance class → evidence shape) is exercised in isolation. The
HTTP-level gates (auth, scope, 404 envelope) live in
``test_routes_knowledge_relations.py``.
"""

from __future__ import annotations

import pytest

from app.schemas.knowledge import GraphEdge, GraphNode
from app.schemas.knowledge_relations import ProvenanceClass
from app.services.knowledge.graph_store import InMemoryGraphStore
from app.services.knowledge.relations import (
    KnowledgeRelationsService,
    RelationNotFound,
)
from app.services.knowledge.scoring import StrengthClass


def _service() -> tuple[KnowledgeRelationsService, InMemoryGraphStore]:
    store = InMemoryGraphStore()
    return KnowledgeRelationsService(graph_store=store), store


def _seed_chunk(
    store: InMemoryGraphStore, *, chunk_id: str, document_id: str, version_id: str
) -> None:
    store.upsert_nodes(
        [
            GraphNode(
                id=chunk_id,
                kind="chunk",
                label=chunk_id,
                properties={
                    "document_id": document_id,
                    "version_id": version_id,
                    "chunk_id": chunk_id,
                },
            )
        ]
    )


def _seed_deterministic_edge(
    store: InMemoryGraphStore,
    *,
    edge_id: str,
    source_chunk_id: str,
    target_chunk_id: str,
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    score: float = 0.55,
    reason: str = "Shared keywords on safety reviews",
    shared_keywords: list[str] | None = None,
    kind: str = "related_to",
) -> None:
    store.upsert_edges(
        [
            GraphEdge(
                id=edge_id,
                kind=kind,  # type: ignore[arg-type]
                source_id=source_chunk_id,
                target_id=target_chunk_id,
                properties={
                    "document_id": document_id,
                    "version_id": version_id,
                    "source_chunk_id": source_chunk_id,
                    "target_chunk_id": target_chunk_id,
                    "score": score,
                    "reason": reason,
                    "shared_keywords": shared_keywords or ["safety", "review"],
                },
            )
        ]
    )


def _seed_has_entity_edge(
    store: InMemoryGraphStore,
    *,
    edge_id: str,
    subject: str,
    obj: str,
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    section_id: str = "sec-1",
    predicate: str = "RELATES_TO",
    confidence: float = 0.85,
    source_reference_id: str = "ref-1",
) -> None:
    store.upsert_edges(
        [
            GraphEdge(
                id=edge_id,
                kind="has_entity",
                source_id=subject,
                target_id=obj,
                properties={
                    "document_id": document_id,
                    "version_id": version_id,
                    "section_id": section_id,
                    "predicate": predicate,
                    "confidence": confidence,
                    "source_reference_id": source_reference_id,
                },
            )
        ]
    )


# ── explain (single edge) ─────────────────────────────────────────────


class TestExplainDeterministic:
    def test_returns_full_evidence_for_chunk_relation(self) -> None:
        service, store = _service()
        _seed_deterministic_edge(
            store,
            edge_id="ver-1:chunk-a->related_to->chunk-b",
            source_chunk_id="chunk-a",
            target_chunk_id="chunk-b",
            score=0.65,
            shared_keywords=["safety", "policy", "audit"],
        )
        evidence = service.explain("ver-1:chunk-a->related_to->chunk-b")
        assert evidence.kind == "related_to"
        assert evidence.provenance_class is ProvenanceClass.DETERMINISTIC
        assert evidence.source_id == "chunk-a"
        assert evidence.target_id == "chunk-b"
        assert evidence.reason == "Shared keywords on safety reviews"
        assert evidence.shared_keywords == ["safety", "policy", "audit"]
        assert evidence.source_chunk_ids == ["chunk-a", "chunk-b"]
        assert evidence.document_id == "doc-1"
        assert evidence.version_id == "ver-1"

    def test_carries_score_and_strength_class(self) -> None:
        service, store = _service()
        _seed_deterministic_edge(
            store,
            edge_id="e-strong",
            source_chunk_id="c1",
            target_chunk_id="c2",
            score=0.80,
            shared_keywords=["a", "b", "c", "d"],
        )
        evidence = service.explain("e-strong")
        assert evidence.score is not None
        assert evidence.score >= 0.80  # raw + bonus
        assert evidence.strength_class == StrengthClass.STRONG.value
        # contributing_factors are populated for transparency.
        assert "raw_score" in evidence.contributing_factors

    def test_low_raw_score_lands_in_weak(self) -> None:
        service, store = _service()
        _seed_deterministic_edge(
            store,
            edge_id="e-weak",
            source_chunk_id="c1",
            target_chunk_id="c2",
            score=0.10,
            shared_keywords=[],
        )
        evidence = service.explain("e-weak")
        assert evidence.strength_class == StrengthClass.WEAK.value


class TestExplainLLM:
    def test_returns_confidence_and_citation(self) -> None:
        service, store = _service()
        _seed_has_entity_edge(
            store,
            edge_id="e-ent",
            subject="entity:Alice",
            obj="entity:Bob",
            confidence=0.92,
            source_reference_id="ref-42",
        )
        evidence = service.explain("e-ent")
        assert evidence.kind == "has_entity"
        assert evidence.provenance_class is ProvenanceClass.LLM
        assert evidence.confidence == 0.92
        assert evidence.predicate == "RELATES_TO"
        assert evidence.source_section_id == "sec-1"
        assert evidence.source_reference_ids == ["ref-42"]
        # LLM edges don't carry a deterministic ``score`` — that field
        # stays None, the frontend reads ``confidence`` instead.
        assert evidence.score is None
        assert evidence.strength_class is None


class TestExplainStructural:
    def test_returns_bare_evidence(self) -> None:
        service, store = _service()
        store.upsert_edges(
            [
                GraphEdge(
                    id="e-belongs",
                    kind="belongs_to",
                    source_id="chunk-a",
                    target_id="topic-1",
                    properties={
                        "document_id": "doc-1",
                        "version_id": "ver-1",
                    },
                )
            ]
        )
        evidence = service.explain("e-belongs")
        assert evidence.provenance_class is ProvenanceClass.STRUCTURAL
        assert evidence.kind == "belongs_to"
        assert evidence.score is None
        assert evidence.confidence is None
        assert evidence.document_id == "doc-1"


class TestExplainNotFound:
    def test_unknown_id_raises_relation_not_found(self) -> None:
        service, _ = _service()
        with pytest.raises(RelationNotFound):
            service.explain("e-missing")


# ── explain_aggregate (doc-doc) ──────────────────────────────────────


class TestAggregate:
    def _seed_two_doc_pair(self, store: InMemoryGraphStore) -> None:
        # Doc-A has chunks ca1, ca2; doc-B has chunks cb1, cb2.
        # Each chunk node carries document_id so find_subgraph_for_document
        # picks up the right slices.
        for cid in ("ca1", "ca2"):
            _seed_chunk(store, chunk_id=cid, document_id="doc-a", version_id="ver-a")
        for cid in ("cb1", "cb2"):
            _seed_chunk(store, chunk_id=cid, document_id="doc-b", version_id="ver-b")
        # A document-level node anchors find_subgraph_for_document so
        # the chunk nodes get included.
        store.upsert_nodes(
            [
                GraphNode(
                    id="doc-a",
                    kind="document",
                    label="A",
                    properties={"document_id": "doc-a"},
                ),
                GraphNode(
                    id="doc-b",
                    kind="document",
                    label="B",
                    properties={"document_id": "doc-b"},
                ),
            ]
        )
        # Two cross-doc deterministic edges.
        _seed_deterministic_edge(
            store,
            edge_id="x-1",
            source_chunk_id="ca1",
            target_chunk_id="cb1",
            document_id="doc-a",
            version_id="ver-a",
            score=0.55,
        )
        _seed_deterministic_edge(
            store,
            edge_id="x-2",
            source_chunk_id="ca2",
            target_chunk_id="cb2",
            document_id="doc-a",
            version_id="ver-a",
            score=0.85,
            shared_keywords=["compliance", "policy", "review"],
        )

    def test_returns_top_contributing_pairs_sorted_by_score(self) -> None:
        service, store = _service()
        self._seed_two_doc_pair(store)
        agg = service.explain_aggregate(
            source_document_id="doc-a", target_document_id="doc-b", top_n=10
        )
        assert agg.source_document_id == "doc-a"
        assert agg.target_document_id == "doc-b"
        assert agg.pair_count == 2
        ids = [p.relation_id for p in agg.top_contributing_pairs]
        # Strongest first.
        assert ids[0] == "x-2"
        assert ids[1] == "x-1"
        # Aggregate score is the MAX of contributing scores.
        assert agg.aggregate_score == agg.top_contributing_pairs[0].score

    def test_top_n_truncates_but_pair_count_stays_total(self) -> None:
        service, store = _service()
        self._seed_two_doc_pair(store)
        agg = service.explain_aggregate(
            source_document_id="doc-a", target_document_id="doc-b", top_n=1
        )
        assert agg.pair_count == 2
        assert len(agg.top_contributing_pairs) == 1
        assert agg.top_contributing_pairs[0].relation_id == "x-2"

    def test_no_cross_edges_raises_relation_not_found(self) -> None:
        service, store = _service()
        # Two documents with chunks but no edges between them.
        for cid in ("ca1",):
            _seed_chunk(store, chunk_id=cid, document_id="doc-a", version_id="ver-a")
        for cid in ("cb1",):
            _seed_chunk(store, chunk_id=cid, document_id="doc-b", version_id="ver-b")
        store.upsert_nodes(
            [
                GraphNode(
                    id="doc-a", kind="document", label="A", properties={"document_id": "doc-a"}
                ),
                GraphNode(
                    id="doc-b", kind="document", label="B", properties={"document_id": "doc-b"}
                ),
            ]
        )
        with pytest.raises(RelationNotFound):
            service.explain_aggregate(source_document_id="doc-a", target_document_id="doc-b")

    def test_invalid_top_n_raises(self) -> None:
        service, _ = _service()
        with pytest.raises(ValueError):
            service.explain_aggregate(source_document_id="x", target_document_id="y", top_n=0)
