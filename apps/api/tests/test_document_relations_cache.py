"""Tests for the document_relations cache (#380 / ADR-031).

Covers:

* Migration 0011 creates the table + the three documented indexes.
* Store contract parity — :class:`InMemoryDocumentRelationsStore`
  and :class:`SQLiteDocumentRelationsStore` round-trip the same
  ``AggregatedRelationEvidence`` payload.
* Cache hit serves SQLite without re-invoking the underlying
  ``KnowledgeRelationsService``.
* Cache miss falls through to the on-demand compute and writes
  back.
* ``refresh=True`` bypasses a populated cache and updates it.
* The route surfaces the cached path + ``?refresh=true`` correctly.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_persistent_services
from app.main import create_app
from app.schemas.knowledge_relations import (
    AggregatedRelationEvidence,
    ContributingChunkPair,
)
from app.services.document_relations_cache import DocumentRelationsCache
from app.services.document_relations_store import (
    InMemoryDocumentRelationsStore,
    SQLiteDocumentRelationsStore,
)
from app.services.knowledge.relations import RelationNotFound


def _evidence(
    *,
    source: str = "doc-a",
    target: str = "doc-b",
    score: float = 0.74,
    pair_count: int = 3,
    is_bridge: bool = False,
    is_outlier: bool = False,
    pairs: list[ContributingChunkPair] | None = None,
) -> AggregatedRelationEvidence:
    return AggregatedRelationEvidence(
        source_document_id=source,
        target_document_id=target,
        aggregate_score=score,
        pair_count=pair_count,
        is_bridge=is_bridge,
        is_outlier=is_outlier,
        top_contributing_pairs=pairs
        or [
            ContributingChunkPair(
                relation_id="related_to:c-1->c-2",
                kind="related_to",
                source_chunk_id="c-1",
                target_chunk_id="c-2",
                score=0.81,
                strength_class="strong",
                reason="High keyword overlap.",
                shared_keywords=["audit", "policy"],
            )
        ],
    )


# ─── Migration ────────────────────────────────────────────────────


def test_migration_0011_creates_document_relations_table(tmp_path: Path) -> None:
    build_persistent_services(tmp_path)
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='document_relations'"
            ).fetchall()
        }
    finally:
        db.close()
    assert "document_relations" in names


def test_migration_0011_indexes_for_source_target_and_computed_at(tmp_path: Path) -> None:
    build_persistent_services(tmp_path)
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        idx = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='document_relations'"
            ).fetchall()
        }
    finally:
        db.close()
    assert "idx_document_relations_source" in idx
    assert "idx_document_relations_target" in idx
    assert "idx_document_relations_computed_at" in idx


# ─── Store parity ─────────────────────────────────────────────────


@pytest.fixture(params=["inmemory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "inmemory":
        return InMemoryDocumentRelationsStore()
    build_persistent_services(tmp_path)
    return SQLiteDocumentRelationsStore(tmp_path / "catalog.sqlite3")


def test_store_returns_none_when_pair_unseen(store) -> None:
    assert store.get(source_document_id="x", target_document_id="y") is None


def test_store_round_trips_aggregate(store) -> None:
    store.upsert(_evidence())
    cached = store.get(source_document_id="doc-a", target_document_id="doc-b")
    assert cached is not None
    evidence, computed_at = cached
    assert evidence.aggregate_score == pytest.approx(0.74)
    assert evidence.pair_count == 3
    assert evidence.top_contributing_pairs[0].source_chunk_id == "c-1"
    assert isinstance(computed_at, datetime)


def test_store_round_trips_bridge_and_outlier_flags(store) -> None:
    store.upsert(_evidence(is_bridge=True, is_outlier=True))
    cached = store.get(source_document_id="doc-a", target_document_id="doc-b")
    assert cached is not None
    evidence, _ = cached
    assert evidence.is_bridge is True
    assert evidence.is_outlier is True


def test_store_upsert_replaces_prior_row(store) -> None:
    store.upsert(_evidence(score=0.5))
    store.upsert(_evidence(score=0.9))
    cached = store.get(source_document_id="doc-a", target_document_id="doc-b")
    assert cached is not None
    evidence, _ = cached
    assert evidence.aggregate_score == pytest.approx(0.9)


def test_store_keeps_directions_independent(store) -> None:
    """The cache stores both (a→b) and (b→a) as separate rows so the
    response can preserve the caller's orientation without re-orienting
    contributing pair fields."""
    store.upsert(_evidence(source="a", target="b", score=0.4))
    store.upsert(_evidence(source="b", target="a", score=0.6))
    forward = store.get(source_document_id="a", target_document_id="b")
    reverse = store.get(source_document_id="b", target_document_id="a")
    assert forward is not None and reverse is not None
    assert forward[0].aggregate_score == pytest.approx(0.4)
    assert reverse[0].aggregate_score == pytest.approx(0.6)


def test_delete_for_document_removes_both_directions(store) -> None:
    store.upsert(_evidence(source="a", target="b"))
    store.upsert(_evidence(source="b", target="a"))
    store.upsert(_evidence(source="a", target="c"))
    deleted = store.delete_for_document("b")
    assert deleted == 2
    assert store.get(source_document_id="a", target_document_id="b") is None
    assert store.get(source_document_id="b", target_document_id="a") is None
    # Untouched.
    assert store.get(source_document_id="a", target_document_id="c") is not None


# ─── Cache service: hit / miss / refresh ──────────────────────────


class _StubRelations:
    """Counts compute invocations so cache-hit tests can assert
    we didn't re-walk Neo4j."""

    def __init__(self, *, evidence: AggregatedRelationEvidence | None = None) -> None:
        self._evidence = evidence
        self.call_count = 0

    def explain_aggregate(
        self, *, source_document_id: str, target_document_id: str, top_n: int
    ) -> AggregatedRelationEvidence:
        self.call_count += 1
        if self._evidence is None:
            raise RelationNotFound(
                f"No edges between {source_document_id!r} and {target_document_id!r}."
            )
        # Mimic the real service: returned evidence carries the caller's
        # (source, target) orientation regardless of internal canonicalisation.
        return self._evidence.model_copy(
            update={
                "source_document_id": source_document_id,
                "target_document_id": target_document_id,
            }
        )


def test_cache_miss_invokes_compute_and_writes_through() -> None:
    store = InMemoryDocumentRelationsStore()
    stub = _StubRelations(evidence=_evidence())
    cache = DocumentRelationsCache(store=store, relations=stub)  # type: ignore[arg-type]

    out = cache.get_or_compute(
        source_document_id="doc-a",
        target_document_id="doc-b",
    )
    assert stub.call_count == 1
    assert out.aggregate_score == pytest.approx(0.74)
    # Cache populated.
    cached = store.get(source_document_id="doc-a", target_document_id="doc-b")
    assert cached is not None


def test_cache_hit_skips_compute() -> None:
    store = InMemoryDocumentRelationsStore()
    store.upsert(_evidence())
    stub = _StubRelations(evidence=None)  # would raise RelationNotFound if invoked
    cache = DocumentRelationsCache(store=store, relations=stub)  # type: ignore[arg-type]

    out = cache.get_or_compute(
        source_document_id="doc-a",
        target_document_id="doc-b",
    )
    assert stub.call_count == 0
    assert out.aggregate_score == pytest.approx(0.74)


def test_refresh_bypasses_populated_cache() -> None:
    store = InMemoryDocumentRelationsStore()
    store.upsert(_evidence(score=0.1))  # stale
    stub = _StubRelations(evidence=_evidence(score=0.99))  # fresh
    cache = DocumentRelationsCache(store=store, relations=stub)  # type: ignore[arg-type]

    out = cache.get_or_compute(
        source_document_id="doc-a",
        target_document_id="doc-b",
        refresh=True,
    )
    assert stub.call_count == 1
    assert out.aggregate_score == pytest.approx(0.99)
    # Cache updated through the refresh.
    cached = store.get(source_document_id="doc-a", target_document_id="doc-b")
    assert cached is not None
    assert cached[0].aggregate_score == pytest.approx(0.99)


def test_cache_propagates_relation_not_found() -> None:
    store = InMemoryDocumentRelationsStore()
    stub = _StubRelations(evidence=None)
    cache = DocumentRelationsCache(store=store, relations=stub)  # type: ignore[arg-type]
    with pytest.raises(RelationNotFound):
        cache.get_or_compute(source_document_id="x", target_document_id="y")


def test_cache_truncates_to_caller_top_n_but_caches_full_payload() -> None:
    """Cache stores the full ceiling so a follow-up larger request
    doesn't force a recompute. Caller's ``top_n`` only affects
    what's returned."""
    pairs = [
        ContributingChunkPair(
            relation_id=f"related_to:c-{i}->c-{i + 1}",
            kind="related_to",
            source_chunk_id=f"c-{i}",
            target_chunk_id=f"c-{i + 1}",
            score=0.9 - 0.01 * i,
            strength_class="strong",
            reason="",
            shared_keywords=[],
        )
        for i in range(5)
    ]
    store = InMemoryDocumentRelationsStore()
    stub = _StubRelations(evidence=_evidence(pairs=pairs, pair_count=5))
    cache = DocumentRelationsCache(store=store, relations=stub)  # type: ignore[arg-type]

    out = cache.get_or_compute(
        source_document_id="doc-a",
        target_document_id="doc-b",
        top_n=2,
    )
    assert len(out.top_contributing_pairs) == 2
    assert out.pair_count == 5  # un-truncated total preserved
    # Cache has all five.
    cached = store.get(source_document_id="doc-a", target_document_id="doc-b")
    assert cached is not None
    assert len(cached[0].top_contributing_pairs) == 5


# ─── Route round-trip ─────────────────────────────────────────────
#
# The aggregate route runs ``assert_can_access_document`` on both
# source and target before any cache lookup. That requires real
# document rows in the catalog, so these tests upload a pair of docs
# first, then swap the cache's underlying compute with a stub.


def _seed_two_real_docs(client: TestClient) -> tuple[str, str]:
    a = client.post(
        "/documents/upload",
        files={"file": ("a.txt", b"alpha", "text/plain")},
    )
    b = client.post(
        "/documents/upload",
        files={"file": ("b.txt", b"beta", "text/plain")},
    )
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    return a.json()["document_id"], b.json()["document_id"]


def test_route_uses_cache_after_first_compute() -> None:
    app = create_app()
    services = app.state.services
    assert services.document_relations_cache is not None

    client = TestClient(app)
    doc_a, doc_b = _seed_two_real_docs(client)

    # Swap the cache's underlying compute with a stub so we can
    # count invocations across the two reads.
    stub = _StubRelations(evidence=_evidence(source=doc_a, target=doc_b))
    services.document_relations_cache._relations = stub  # type: ignore[attr-defined]

    r1 = client.get(
        "/knowledge/relations/aggregate",
        params={"source_document_id": doc_a, "target_document_id": doc_b},
    )
    r2 = client.get(
        "/knowledge/relations/aggregate",
        params={"source_document_id": doc_a, "target_document_id": doc_b},
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert stub.call_count == 1


def test_route_refresh_query_param_forces_recompute() -> None:
    app = create_app()
    services = app.state.services
    assert services.document_relations_cache is not None

    client = TestClient(app)
    doc_a, doc_b = _seed_two_real_docs(client)

    stub = _StubRelations(evidence=_evidence(source=doc_a, target=doc_b))
    services.document_relations_cache._relations = stub  # type: ignore[attr-defined]

    client.get(
        "/knowledge/relations/aggregate",
        params={"source_document_id": doc_a, "target_document_id": doc_b},
    )
    client.get(
        "/knowledge/relations/aggregate",
        params={
            "source_document_id": doc_a,
            "target_document_id": doc_b,
            "refresh": "true",
        },
    )
    assert stub.call_count == 2


def test_route_returns_404_when_no_relation_evidence() -> None:
    app = create_app()
    services = app.state.services
    assert services.document_relations_cache is not None

    client = TestClient(app)
    doc_a, doc_b = _seed_two_real_docs(client)

    services.document_relations_cache._relations = _StubRelations(  # type: ignore[attr-defined]
        evidence=None
    )

    response = client.get(
        "/knowledge/relations/aggregate",
        params={"source_document_id": doc_a, "target_document_id": doc_b},
    )
    assert response.status_code == 404


# ─── Sanity ────────────────────────────────────────────────────────


def test_sqlite_store_survives_restart_via_persistent_services(tmp_path: Path) -> None:
    """Wire-it-end-to-end: write through the cache via one
    services instance, read back through a freshly built one."""
    services_a = build_persistent_services(tmp_path)
    assert services_a.document_relations_cache is not None
    cache = services_a.document_relations_cache
    cache._store.upsert(_evidence())  # type: ignore[attr-defined]

    services_b = build_persistent_services(tmp_path)
    assert services_b.document_relations_cache is not None
    cached = services_b.document_relations_cache._store.get(  # type: ignore[attr-defined]
        source_document_id="doc-a",
        target_document_id="doc-b",
    )
    assert cached is not None
    assert cached[0].aggregate_score == pytest.approx(0.74)
    # Computed_at should be recent.
    assert datetime.now(UTC) - cached[1] < timedelta(minutes=5)


# ─── #385: warm-on-projection ─────────────────────────────────────


class _StubBridgedRelations:
    """Stub of :class:`KnowledgeRelationsService` for warm tests.

    Tracks ``list_bridged_documents`` invocations and ``explain_aggregate``
    invocations so the warm pass can be exercised in isolation from the
    real graph store. The bridged set is configurable per-doc; the
    explain stub returns a deterministic evidence shape per pair.
    """

    def __init__(self, *, bridged: dict[str, list[str]] | None = None) -> None:
        self._bridged = bridged or {}
        self.bridged_calls: list[str] = []
        self.explain_calls: list[tuple[str, str]] = []

    def list_bridged_documents(self, *, document_id: str) -> list[str]:
        self.bridged_calls.append(document_id)
        return list(self._bridged.get(document_id, []))

    def explain_aggregate(
        self,
        *,
        source_document_id: str,
        target_document_id: str,
        top_n: int,
    ) -> AggregatedRelationEvidence:
        self.explain_calls.append((source_document_id, target_document_id))
        return _evidence(source=source_document_id, target=target_document_id)


def test_warm_for_document_writes_both_directions_per_bridged_pair() -> None:
    store = InMemoryDocumentRelationsStore()
    relations = _StubBridgedRelations(bridged={"a": ["b", "c"]})
    cache = DocumentRelationsCache(store=store, relations=relations)  # type: ignore[arg-type]

    rows = cache.warm_for_document("a")
    # Two bridged docs × two directions each = 4 rows written.
    assert rows == 4
    assert relations.bridged_calls == ["a"]
    assert ("a", "b") in relations.explain_calls
    assert ("b", "a") in relations.explain_calls
    assert ("a", "c") in relations.explain_calls
    assert ("c", "a") in relations.explain_calls
    # And the cache reflects them.
    assert store.get(source_document_id="a", target_document_id="b") is not None
    assert store.get(source_document_id="b", target_document_id="a") is not None


def test_warm_for_document_no_bridge_writes_zero_rows() -> None:
    store = InMemoryDocumentRelationsStore()
    relations = _StubBridgedRelations(bridged={})
    cache = DocumentRelationsCache(store=store, relations=relations)  # type: ignore[arg-type]
    assert cache.warm_for_document("a") == 0


def test_warm_for_document_skips_pairs_that_no_longer_have_evidence() -> None:
    """A pair that vanished between ``list_bridged_documents`` and
    the per-pair recompute (race against another projection) is
    silently skipped, not surfaced as an exception."""

    class _RaceyRelations(_StubBridgedRelations):
        def explain_aggregate(self, **kwargs: object) -> AggregatedRelationEvidence:
            self.explain_calls.append(
                (str(kwargs["source_document_id"]), str(kwargs["target_document_id"]))
            )
            raise RelationNotFound("Race")

    store = InMemoryDocumentRelationsStore()
    relations = _RaceyRelations(bridged={"a": ["b"]})
    cache = DocumentRelationsCache(store=store, relations=relations)  # type: ignore[arg-type]
    assert cache.warm_for_document("a") == 0
    # Tried both directions; both swallowed.
    assert len(relations.explain_calls) == 2


def test_warm_for_document_swallows_unexpected_per_pair_errors() -> None:
    """Per-pair errors that aren't ``RelationNotFound`` (e.g. a Voyage
    blip on the underlying compute) should also be swallowed so one
    bad pair can't take down the whole warm pass."""

    class _BoomRelations(_StubBridgedRelations):
        def explain_aggregate(self, **kwargs: object) -> AggregatedRelationEvidence:
            raise RuntimeError("upstream Voyage failure")

    store = InMemoryDocumentRelationsStore()
    relations = _BoomRelations(bridged={"a": ["b"]})
    cache = DocumentRelationsCache(store=store, relations=relations)  # type: ignore[arg-type]
    # Should not raise; rows written = 0.
    assert cache.warm_for_document("a") == 0


def test_warm_for_document_swallows_bridged_lookup_failure() -> None:
    """If the underlying ``list_bridged_documents`` itself blows up
    (e.g. Neo4j hiccup), the warm pass returns 0 rather than
    propagating — projection completion must not depend on the
    warm succeeding (fire-and-log per ADR-012 §3)."""

    class _NoBridge:
        def list_bridged_documents(self, *, document_id: str) -> list[str]:
            del document_id
            raise RuntimeError("Neo4j down")

        def explain_aggregate(self, **kwargs: object) -> AggregatedRelationEvidence:
            raise AssertionError("explain should not be called when bridged lookup fails")

    store = InMemoryDocumentRelationsStore()
    cache = DocumentRelationsCache(store=store, relations=_NoBridge())  # type: ignore[arg-type]
    assert cache.warm_for_document("a") == 0


# ─── #385: graph-store boundary-docs query ────────────────────────


class TestFindDocumentIdsWithBoundaryEdges:
    """Pin the InMemoryGraphStore implementation of
    :meth:`GraphStore.find_document_ids_with_boundary_edges_to`. The
    Neo4j implementation is exercised by the integration suite."""

    def _seed_two_docs_with_cross_edge(self):
        from app.schemas.knowledge import GraphEdge, GraphNode
        from app.services.knowledge.graph_store import InMemoryGraphStore

        store = InMemoryGraphStore()
        store.upsert_nodes(
            [
                GraphNode(
                    id="ca1",
                    kind="chunk",
                    label="ca1",
                    properties={"document_id": "doc-a", "version_id": "v-a", "chunk_id": "ca1"},
                ),
                GraphNode(
                    id="cb1",
                    kind="chunk",
                    label="cb1",
                    properties={"document_id": "doc-b", "version_id": "v-b", "chunk_id": "cb1"},
                ),
            ]
        )
        store.upsert_edges(
            [
                GraphEdge(
                    id="e1",
                    kind="related_to",
                    source_id="ca1",
                    target_id="cb1",
                    properties={
                        "document_id": "doc-a",
                        "version_id": "v-a",
                        "source_chunk_id": "ca1",
                        "target_chunk_id": "cb1",
                        "score": 0.6,
                    },
                )
            ]
        )
        return store

    def test_returns_the_other_doc(self) -> None:
        store = self._seed_two_docs_with_cross_edge()
        assert store.find_document_ids_with_boundary_edges_to("doc-a") == ["doc-b"]
        assert store.find_document_ids_with_boundary_edges_to("doc-b") == ["doc-a"]

    def test_excludes_self(self) -> None:
        """A chunk-pair edge inside the SAME document doesn't count
        as a boundary edge — the query is for cross-document
        bridges."""
        from app.schemas.knowledge import GraphEdge, GraphNode
        from app.services.knowledge.graph_store import InMemoryGraphStore

        store = InMemoryGraphStore()
        store.upsert_nodes(
            [
                GraphNode(
                    id=f"c{i}",
                    kind="chunk",
                    label=f"c{i}",
                    properties={"document_id": "doc-a", "version_id": "v-a", "chunk_id": f"c{i}"},
                )
                for i in range(2)
            ]
        )
        store.upsert_edges(
            [
                GraphEdge(
                    id="e_intra",
                    kind="related_to",
                    source_id="c0",
                    target_id="c1",
                    properties={
                        "document_id": "doc-a",
                        "version_id": "v-a",
                        "source_chunk_id": "c0",
                        "target_chunk_id": "c1",
                        "score": 0.5,
                    },
                )
            ]
        )
        assert store.find_document_ids_with_boundary_edges_to("doc-a") == []

    def test_unknown_document_returns_empty(self) -> None:
        store = self._seed_two_docs_with_cross_edge()
        assert store.find_document_ids_with_boundary_edges_to("nope") == []

    def test_returns_sorted_distinct(self) -> None:
        """Multiple edges to the same other-doc collapse; multiple
        other-docs come back sorted."""
        from app.schemas.knowledge import GraphEdge, GraphNode
        from app.services.knowledge.graph_store import InMemoryGraphStore

        store = InMemoryGraphStore()
        nodes = [
            GraphNode(
                id="ca1",
                kind="chunk",
                label="ca1",
                properties={"document_id": "doc-a", "version_id": "v-a", "chunk_id": "ca1"},
            ),
            GraphNode(
                id="ca2",
                kind="chunk",
                label="ca2",
                properties={"document_id": "doc-a", "version_id": "v-a", "chunk_id": "ca2"},
            ),
        ]
        for ix, other in enumerate(["doc-z", "doc-b", "doc-m"]):
            nodes.append(
                GraphNode(
                    id=f"co{ix}",
                    kind="chunk",
                    label=f"co{ix}",
                    properties={
                        "document_id": other,
                        "version_id": f"v-{other}",
                        "chunk_id": f"co{ix}",
                    },
                )
            )
        store.upsert_nodes(nodes)
        store.upsert_edges(
            [
                GraphEdge(
                    id=f"e{ix}",
                    kind="related_to",
                    source_id="ca1" if ix % 2 == 0 else "ca2",
                    target_id=f"co{ix}",
                    properties={
                        "document_id": "doc-a",
                        "version_id": "v-a",
                        "source_chunk_id": "ca1",
                        "target_chunk_id": f"co{ix}",
                        "score": 0.5,
                    },
                )
                for ix in range(3)
            ]
        )
        assert store.find_document_ids_with_boundary_edges_to("doc-a") == [
            "doc-b",
            "doc-m",
            "doc-z",
        ]


# ─── #385: projector wiring + warm trigger ────────────────────────


class _WarmRecorder:
    """Cache stand-in that records ``warm_for_document`` calls so we
    can assert the projector fires the warm hook."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.raise_on_call = False

    def warm_for_document(self, document_id: str) -> int:
        self.calls.append(document_id)
        if self.raise_on_call:
            raise RuntimeError("simulated warm failure")
        return 0


def test_projector_calls_warm_for_document_after_successful_projection() -> None:
    from app.schemas.semantic_document import SemanticDocument, SemanticSection
    from app.services.knowledge.graph_store import InMemoryGraphStore
    from app.services.knowledge.projector import KnowledgeProjector

    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    recorder = _WarmRecorder()
    projector.set_document_relations_cache(recorder)  # type: ignore[arg-type]

    # Minimal valid document + version + semantic for projection.
    from datetime import UTC, datetime

    from app.models.document import DocumentVersionStatus
    from app.schemas.document import Document, DocumentVersion

    version = DocumentVersion(
        id="v-1",
        document_id="doc-x",
        version_number=1,
        filename="x.txt",
        content_type="text/plain",
        file_size=10,
        sha256="0" * 64,
        storage_uri="memory://x",
        status=DocumentVersionStatus.VALIDATED,
        created_at=datetime.now(UTC),
    )
    document = Document.with_first_version(version)
    semantic = SemanticDocument(
        id="sem-1",
        document_version_id="v-1",
        document_profile={"title": "x", "document_type": "unknown"},
        sections=[SemanticSection(id="s-1", heading="One", text="alpha alpha alpha")],
        validation_status="needs_review",
    )

    projector.project(document=document, version=version, semantic=semantic)
    assert recorder.calls == ["doc-x"]


def test_projector_swallows_warm_failures() -> None:
    """A warm failure must not roll back the projection — same
    fire-and-log boundary as Phase 3 embeddings."""
    from app.schemas.semantic_document import SemanticDocument, SemanticSection
    from app.services.knowledge.graph_store import InMemoryGraphStore
    from app.services.knowledge.projector import KnowledgeProjector

    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    recorder = _WarmRecorder()
    recorder.raise_on_call = True
    projector.set_document_relations_cache(recorder)  # type: ignore[arg-type]

    from datetime import UTC, datetime

    from app.models.document import DocumentVersionStatus
    from app.schemas.document import Document, DocumentVersion

    version = DocumentVersion(
        id="v-1",
        document_id="doc-x",
        version_number=1,
        filename="x.txt",
        content_type="text/plain",
        file_size=10,
        sha256="0" * 64,
        storage_uri="memory://x",
        status=DocumentVersionStatus.VALIDATED,
        created_at=datetime.now(UTC),
    )
    document = Document.with_first_version(version)
    semantic = SemanticDocument(
        id="sem-1",
        document_version_id="v-1",
        document_profile={"title": "x", "document_type": "unknown"},
        sections=[SemanticSection(id="s-1", heading="One", text="alpha alpha alpha")],
        validation_status="needs_review",
    )

    # Should not raise.
    projector.project(document=document, version=version, semantic=semantic)
    assert recorder.calls == ["doc-x"]


def test_default_projector_has_no_warm_hook() -> None:
    """A projector built without ``set_document_relations_cache``
    behaves exactly like the pre-#385 path — no warm call, no
    failure."""
    from app.schemas.semantic_document import SemanticDocument, SemanticSection
    from app.services.knowledge.graph_store import InMemoryGraphStore
    from app.services.knowledge.projector import KnowledgeProjector

    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    # No set_document_relations_cache call.

    from datetime import UTC, datetime

    from app.models.document import DocumentVersionStatus
    from app.schemas.document import Document, DocumentVersion

    version = DocumentVersion(
        id="v-1",
        document_id="doc-x",
        version_number=1,
        filename="x.txt",
        content_type="text/plain",
        file_size=10,
        sha256="0" * 64,
        storage_uri="memory://x",
        status=DocumentVersionStatus.VALIDATED,
        created_at=datetime.now(UTC),
    )
    document = Document.with_first_version(version)
    semantic = SemanticDocument(
        id="sem-1",
        document_version_id="v-1",
        document_profile={"title": "x", "document_type": "unknown"},
        sections=[SemanticSection(id="s-1", heading="One", text="alpha")],
        validation_status="needs_review",
    )

    # Just ensure no exception.
    projector.project(document=document, version=version, semantic=semantic)


def test_dependencies_wire_warm_hook_in_build_services() -> None:
    """Sanity that the in-memory build path wires the cache onto the
    projector — the warm-on-projection trigger is the whole point of
    #385 and an unwired projector silently negates the feature."""
    from app.dependencies import build_services

    services = build_services()
    projector = services.knowledge_projector
    cache = services.document_relations_cache
    if projector is None:
        # Knowledge layer disabled in the default settings — no
        # projector to wire. The cache is still present for reads.
        assert cache is not None
        return
    # Projector private attribute used to assert the wire — the
    # public surface is the setter we added in #385.
    assert projector._document_relations_cache is cache  # type: ignore[attr-defined]


# ─── KnowledgeRelationsService.list_bridged_documents adapter ───────


def test_list_bridged_documents_delegates_to_graph_store() -> None:
    from app.services.knowledge.graph_store import InMemoryGraphStore
    from app.services.knowledge.relations import KnowledgeRelationsService

    store = InMemoryGraphStore()
    service = KnowledgeRelationsService(graph_store=store)
    # Empty store → empty result.
    assert service.list_bridged_documents(document_id="anything") == []

    # Seed and re-test.
    from app.schemas.knowledge import GraphEdge, GraphNode

    store.upsert_nodes(
        [
            GraphNode(
                id="ca",
                kind="chunk",
                label="ca",
                properties={"document_id": "doc-a", "version_id": "v-a", "chunk_id": "ca"},
            ),
            GraphNode(
                id="cb",
                kind="chunk",
                label="cb",
                properties={"document_id": "doc-b", "version_id": "v-b", "chunk_id": "cb"},
            ),
        ]
    )
    store.upsert_edges(
        [
            GraphEdge(
                id="e",
                kind="related_to",
                source_id="ca",
                target_id="cb",
                properties={
                    "document_id": "doc-a",
                    "version_id": "v-a",
                    "source_chunk_id": "ca",
                    "target_chunk_id": "cb",
                    "score": 0.5,
                },
            )
        ]
    )
    assert service.list_bridged_documents(document_id="doc-a") == ["doc-b"]
