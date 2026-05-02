"""Tests for ``ReconciliationService`` (#124).

Covers:

* Detection of VALIDATED catalog versions whose ``(:Version)`` node is
  missing from the graph projection.
* One-shot repair via ``reconcile_version`` — projection runs, drift
  goes away, the call is idempotent against an already-healthy version.
* ``reconcile_all_drifted`` continues past per-version errors.
* Guardrails: knowledge layer disabled, missing document, missing
  version, version not in VALIDATED.

The unit suite uses :class:`InMemoryGraphStore` and
:class:`InMemoryCatalogStore` so it stays hermetic. The Neo4j
counterpart lives in ``tests/integration/test_reconciliation_integration.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticDocument,
    SemanticSection,
)
from app.services.catalog_store import InMemoryCatalogStore
from app.services.knowledge.graph_store import InMemoryGraphStore
from app.services.knowledge.projector import KnowledgeProjector
from app.services.knowledge.reconciliation import (
    DriftedVersion,
    KnowledgeLayerDisabled,
    ReconciliationService,
)

# ─── Fixtures ─────────────────────────────────────────────────────────


def _make_version(
    *,
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    status: DocumentVersionStatus = DocumentVersionStatus.VALIDATED,
) -> DocumentVersion:
    return DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=1,
        filename="policy.txt",
        content_type="text/plain",
        file_size=42,
        sha256="0" * 64,
        storage_uri="file://fake",
        status=status,
    )


def _make_document(*, version: DocumentVersion) -> Document:
    return Document(
        id=version.document_id,
        original_filename=version.filename,
        latest_version_id=version.id,
        versions=[version],
    )


def _make_semantic(*, version: DocumentVersion) -> SemanticDocument:
    return SemanticDocument(
        id=f"sem-{version.id}",
        document_version_id=version.id,
        document_profile=DocumentProfile(title="Test Doc"),
        sections=[SemanticSection(id="s1", heading="Intro", text="hello")],
        validation_status="validated",
        markdown="# Test\n",
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


def _seed_catalog_with_validated(
    catalog: InMemoryCatalogStore,
    *,
    document: Document,
    version: DocumentVersion,
) -> None:
    """Add a document+version and bypass the lifecycle FSM by writing
    the VALIDATED status directly into the in-memory dicts.

    Using ``update_version_review`` would require threading the FSM
    through — needlessly heavy for a unit test that only cares about
    the catalog ↔ graph drift surface.
    """
    catalog.save_document_with_version(document=document, version=version)


def _make_reconciler(
    *,
    catalog: InMemoryCatalogStore,
    graph_store: InMemoryGraphStore,
    projector: KnowledgeProjector | None,
    semantic: SemanticDocument,
) -> ReconciliationService:
    return ReconciliationService(
        catalog=catalog,
        graph_store=graph_store,
        projector=projector,
        entity_extractor=None,
        get_semantic=lambda doc_id, ver_id: semantic,
    )


# ─── Detection ────────────────────────────────────────────────────────


class TestFindDriftedVersions:
    def test_validated_version_with_no_projection_is_drifted(self):
        catalog = InMemoryCatalogStore()
        graph_store = InMemoryGraphStore()
        version = _make_version()
        document = _make_document(version=version)
        _seed_catalog_with_validated(catalog, document=document, version=version)

        reconciler = _make_reconciler(
            catalog=catalog,
            graph_store=graph_store,
            projector=KnowledgeProjector(graph_store=graph_store),
            semantic=_make_semantic(version=version),
        )

        drifted = reconciler.find_drifted_versions()

        assert drifted == [
            DriftedVersion(
                document_id=document.id,
                version_id=version.id,
                reason="version node missing from graph",
            )
        ]

    def test_already_projected_version_is_not_drifted(self):
        catalog = InMemoryCatalogStore()
        graph_store = InMemoryGraphStore()
        version = _make_version()
        document = _make_document(version=version)
        semantic = _make_semantic(version=version)
        _seed_catalog_with_validated(catalog, document=document, version=version)

        projector = KnowledgeProjector(graph_store=graph_store)
        projector.project(document=document, version=version, semantic=semantic)

        reconciler = _make_reconciler(
            catalog=catalog,
            graph_store=graph_store,
            projector=projector,
            semantic=semantic,
        )

        assert reconciler.find_drifted_versions() == []

    def test_non_validated_versions_are_ignored(self):
        catalog = InMemoryCatalogStore()
        graph_store = InMemoryGraphStore()
        # NEEDS_REVIEW shouldn't be reported as drift; it was never
        # supposed to be projected.
        version = _make_version(status=DocumentVersionStatus.NEEDS_REVIEW)
        document = _make_document(version=version)
        _seed_catalog_with_validated(catalog, document=document, version=version)

        reconciler = _make_reconciler(
            catalog=catalog,
            graph_store=graph_store,
            projector=KnowledgeProjector(graph_store=graph_store),
            semantic=_make_semantic(version=version),
        )

        assert reconciler.find_drifted_versions() == []

    def test_one_subgraph_read_per_document_even_with_many_versions(self):
        """The detection cache means a 50-version document is one read."""
        catalog = InMemoryCatalogStore()
        graph_store = InMemoryGraphStore()
        v1 = _make_version(version_id="ver-1")
        v2 = _make_version(version_id="ver-2")
        document = Document(
            id="doc-1",
            original_filename="policy.txt",
            latest_version_id="ver-2",
            versions=[v1, v2],
        )
        catalog.documents[document.id] = document

        reads = {"count": 0}
        original = graph_store.find_subgraph_for_document

        def counting_read(document_id):  # type: ignore[no-untyped-def]
            reads["count"] += 1
            return original(document_id)

        graph_store.find_subgraph_for_document = counting_read  # type: ignore[method-assign]

        reconciler = _make_reconciler(
            catalog=catalog,
            graph_store=graph_store,
            projector=KnowledgeProjector(graph_store=graph_store),
            semantic=_make_semantic(version=v1),
        )

        drifted = reconciler.find_drifted_versions()

        assert {d.version_id for d in drifted} == {"ver-1", "ver-2"}
        assert reads["count"] == 1, (
            f"Expected one find_subgraph_for_document call per document, got {reads['count']}"
        )


# ─── Repair ───────────────────────────────────────────────────────────


class TestReconcileVersion:
    def test_reconcile_runs_projection_and_clears_drift(self):
        catalog = InMemoryCatalogStore()
        graph_store = InMemoryGraphStore()
        version = _make_version()
        document = _make_document(version=version)
        semantic = _make_semantic(version=version)
        _seed_catalog_with_validated(catalog, document=document, version=version)

        projector = KnowledgeProjector(graph_store=graph_store)
        reconciler = _make_reconciler(
            catalog=catalog,
            graph_store=graph_store,
            projector=projector,
            semantic=semantic,
        )

        outcome = reconciler.reconcile_version(document_id=document.id, version_id=version.id)

        assert outcome.projection_ok is True
        assert outcome.entity_extraction_ok is None  # extractor not configured
        assert outcome.error is None
        # And drift is gone.
        assert reconciler.find_drifted_versions() == []

    def test_reconcile_is_idempotent_against_a_healthy_version(self):
        catalog = InMemoryCatalogStore()
        graph_store = InMemoryGraphStore()
        version = _make_version()
        document = _make_document(version=version)
        semantic = _make_semantic(version=version)
        _seed_catalog_with_validated(catalog, document=document, version=version)

        projector = KnowledgeProjector(graph_store=graph_store)
        projector.project(document=document, version=version, semantic=semantic)
        # Snapshot the pre-reconcile graph; a no-op reconcile should leave it
        # equal up to the version node's own re-write.
        before = graph_store.find_subgraph_for_document(document.id)

        reconciler = _make_reconciler(
            catalog=catalog,
            graph_store=graph_store,
            projector=projector,
            semantic=semantic,
        )
        outcome = reconciler.reconcile_version(document_id=document.id, version_id=version.id)

        assert outcome.projection_ok is True
        after = graph_store.find_subgraph_for_document(document.id)
        # Same set of node ids, same set of edge ids.
        assert {n.id for n in before.nodes} == {n.id for n in after.nodes}
        assert {e.id for e in before.edges} == {e.id for e in after.edges}

    def test_reconcile_raises_when_knowledge_layer_disabled(self):
        catalog = InMemoryCatalogStore()
        graph_store = InMemoryGraphStore()
        version = _make_version()
        document = _make_document(version=version)
        semantic = _make_semantic(version=version)
        _seed_catalog_with_validated(catalog, document=document, version=version)

        # No projector → layer is off.
        reconciler = _make_reconciler(
            catalog=catalog,
            graph_store=graph_store,
            projector=None,
            semantic=semantic,
        )

        with pytest.raises(KnowledgeLayerDisabled):
            reconciler.reconcile_version(document_id=document.id, version_id=version.id)

    def test_reconcile_raises_lookuperror_for_unknown_document(self):
        reconciler = _make_reconciler(
            catalog=InMemoryCatalogStore(),
            graph_store=InMemoryGraphStore(),
            projector=KnowledgeProjector(graph_store=InMemoryGraphStore()),
            semantic=_make_semantic(version=_make_version()),
        )
        with pytest.raises(LookupError, match="Document"):
            reconciler.reconcile_version(document_id="nope", version_id="ver-1")

    def test_reconcile_raises_lookuperror_for_unknown_version(self):
        catalog = InMemoryCatalogStore()
        version = _make_version()
        document = _make_document(version=version)
        _seed_catalog_with_validated(catalog, document=document, version=version)

        reconciler = _make_reconciler(
            catalog=catalog,
            graph_store=InMemoryGraphStore(),
            projector=KnowledgeProjector(graph_store=InMemoryGraphStore()),
            semantic=_make_semantic(version=version),
        )
        with pytest.raises(LookupError, match="Version"):
            reconciler.reconcile_version(document_id=document.id, version_id="nope")

    def test_reconcile_raises_valueerror_when_version_not_validated(self):
        catalog = InMemoryCatalogStore()
        version = _make_version(status=DocumentVersionStatus.NEEDS_REVIEW)
        document = _make_document(version=version)
        _seed_catalog_with_validated(catalog, document=document, version=version)

        reconciler = _make_reconciler(
            catalog=catalog,
            graph_store=InMemoryGraphStore(),
            projector=KnowledgeProjector(graph_store=InMemoryGraphStore()),
            semantic=_make_semantic(version=version),
        )
        with pytest.raises(ValueError, match="not VALIDATED"):
            reconciler.reconcile_version(document_id=document.id, version_id=version.id)

    def test_reconcile_runs_entity_extractor_when_configured(self):
        """Phase 2 path: with an extractor wired, reconciliation runs it
        and reports ``entity_extraction_ok=True`` on success."""
        catalog = InMemoryCatalogStore()
        graph_store = InMemoryGraphStore()
        version = _make_version()
        document = _make_document(version=version)
        semantic = _make_semantic(version=version)
        _seed_catalog_with_validated(catalog, document=document, version=version)

        calls: list[tuple[str, str]] = []

        class StubExtractor:
            def extract(self, *, document, version, semantic):  # type: ignore[no-untyped-def]
                calls.append((document.id, version.id))
                return _StubExtractionResult()

        class _StubExtractionResult:
            triples = []
            warnings = []
            token_usage = {}

        # The projector is real but we monkeypatch project_entities so we
        # don't need a fully-shaped EntityExtractionResult.
        projector = KnowledgeProjector(graph_store=graph_store)
        projector.project_entities = lambda result: None  # type: ignore[method-assign]

        reconciler = ReconciliationService(
            catalog=catalog,
            graph_store=graph_store,
            projector=projector,
            entity_extractor=StubExtractor(),  # type: ignore[arg-type]
            get_semantic=lambda d, v: semantic,
        )

        outcome = reconciler.reconcile_version(document_id=document.id, version_id=version.id)

        assert outcome.projection_ok is True
        assert outcome.entity_extraction_ok is True
        assert outcome.error is None
        assert calls == [(document.id, version.id)]

    def test_reconcile_records_entity_extraction_failure(self):
        """Projection-OK + extractor-raises → projection_ok=True,
        entity_extraction_ok=False, error reflects the extractor."""
        catalog = InMemoryCatalogStore()
        graph_store = InMemoryGraphStore()
        version = _make_version()
        document = _make_document(version=version)
        semantic = _make_semantic(version=version)
        _seed_catalog_with_validated(catalog, document=document, version=version)

        class FailingExtractor:
            def extract(self, *, document, version, semantic):  # type: ignore[no-untyped-def]
                raise RuntimeError("anthropic 503")

        reconciler = ReconciliationService(
            catalog=catalog,
            graph_store=graph_store,
            projector=KnowledgeProjector(graph_store=graph_store),
            entity_extractor=FailingExtractor(),  # type: ignore[arg-type]
            get_semantic=lambda d, v: semantic,
        )

        outcome = reconciler.reconcile_version(document_id=document.id, version_id=version.id)

        assert outcome.projection_ok is True
        assert outcome.entity_extraction_ok is False
        assert outcome.error is not None
        assert "anthropic 503" in outcome.error

    def test_reconcile_raises_lookuperror_when_semantic_missing(self):
        """If the semantic blob can't be loaded (KeyError), reconciliation
        surfaces it as a LookupError so the CLI exits 1."""
        catalog = InMemoryCatalogStore()
        version = _make_version()
        document = _make_document(version=version)
        _seed_catalog_with_validated(catalog, document=document, version=version)

        def missing_semantic(d, v):  # type: ignore[no-untyped-def]
            raise KeyError(v)

        reconciler = ReconciliationService(
            catalog=catalog,
            graph_store=InMemoryGraphStore(),
            projector=KnowledgeProjector(graph_store=InMemoryGraphStore()),
            entity_extractor=None,
            get_semantic=missing_semantic,
        )

        with pytest.raises(LookupError, match="No semantic document"):
            reconciler.reconcile_version(document_id=document.id, version_id=version.id)

    def test_reconcile_records_projection_failure_without_raising(self):
        """Projection errors are caught + reported, not raised — the CLI
        must see partial progress on a multi-version reconcile."""

        class FailingProjector:
            def project(self, *, document, version, semantic):  # type: ignore[no-untyped-def]
                raise RuntimeError("boom")

            def project_entities(self, result):  # type: ignore[no-untyped-def]
                pass

        catalog = InMemoryCatalogStore()
        version = _make_version()
        document = _make_document(version=version)
        semantic = _make_semantic(version=version)
        _seed_catalog_with_validated(catalog, document=document, version=version)

        reconciler = ReconciliationService(
            catalog=catalog,
            graph_store=InMemoryGraphStore(),
            projector=FailingProjector(),  # type: ignore[arg-type]
            entity_extractor=None,
            get_semantic=lambda d, v: semantic,
        )

        outcome = reconciler.reconcile_version(document_id=document.id, version_id=version.id)

        assert outcome.projection_ok is False
        assert outcome.error is not None
        assert "boom" in outcome.error


class TestReconcileAllDrifted:
    def test_continues_past_per_version_errors(self):
        catalog = InMemoryCatalogStore()
        graph_store = InMemoryGraphStore()
        v1 = _make_version(version_id="ver-1")
        v2 = _make_version(version_id="ver-2")
        document = Document(
            id="doc-1",
            original_filename="policy.txt",
            latest_version_id="ver-2",
            versions=[v1, v2],
        )
        catalog.documents[document.id] = document

        # get_semantic returns the right object for v1 but raises for v2,
        # simulating a missing semantic blob — reconciliation should record
        # the error against v2 and continue.
        sem_v1 = _make_semantic(version=v1)

        def get_semantic(doc_id, ver_id):  # type: ignore[no-untyped-def]
            if ver_id == v1.id:
                return sem_v1
            raise KeyError(ver_id)

        reconciler = ReconciliationService(
            catalog=catalog,
            graph_store=graph_store,
            projector=KnowledgeProjector(graph_store=graph_store),
            entity_extractor=None,
            get_semantic=get_semantic,
        )

        outcomes = reconciler.reconcile_all_drifted()

        assert len(outcomes) == 2
        by_version = {o.version_id: o for o in outcomes}
        assert by_version["ver-1"].projection_ok is True
        assert by_version["ver-1"].error is None
        assert by_version["ver-2"].projection_ok is False
        assert by_version["ver-2"].error is not None
