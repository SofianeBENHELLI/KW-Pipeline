"""Integration test for ``ReconciliationService`` against a real Neo4j (#124).

The unit suite in ``tests/test_knowledge_reconciliation.py`` exercises
the service against ``InMemoryGraphStore``. This module verifies the
same contract with a live ``Neo4jGraphStore`` backed by a Bolt
connection — the scenario the CLI actually targets in production.

Opt-in via ``pytest -m integration``. Skips cleanly when the
``KW_NEO4J_*`` env vars are absent, mirroring
``test_neo4j_graph_store.py``.

Test scenario:

    1. Build a small VALIDATED document and project it via the real
       ``KnowledgeProjector`` so the graph has a healthy baseline.
    2. Manually drop the version's subgraph via
       ``delete_subgraph_for_version`` to simulate the failure mode
       ADR-012 §4 worries about (validation succeeded, projection
       didn't).
    3. Assert ``find_drifted_versions`` reports the version.
    4. Run ``reconcile_version`` and assert the projection is back
       and drift is gone.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
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
from app.services.knowledge.graph_store import Neo4jGraphStore
from app.services.knowledge.projector import KnowledgeProjector
from app.services.knowledge.reconciliation import ReconciliationService

pytestmark = pytest.mark.integration


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def neo4j_config() -> dict[str, str]:
    uri = os.environ.get("KW_NEO4J_URI")
    user = os.environ.get("KW_NEO4J_USER")
    password = os.environ.get("KW_NEO4J_PASSWORD")
    missing = [
        name
        for name, value in (
            ("KW_NEO4J_URI", uri),
            ("KW_NEO4J_USER", user),
            ("KW_NEO4J_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        pytest.skip(
            "Neo4j integration tests require "
            f"{', '.join(missing)}; set them or run "
            "`docker compose -f docker/docker-compose.yml up -d neo4j`."
        )
    assert uri and user and password
    return {"uri": uri, "user": user, "password": password}


@pytest.fixture()
def store(neo4j_config: dict[str, str]) -> Iterator[Neo4jGraphStore]:
    s = Neo4jGraphStore(
        uri=neo4j_config["uri"],
        user=neo4j_config["user"],
        password=neo4j_config["password"],
    )
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def namespace() -> str:
    """UUID-prefixed id namespace so a stray DETACH DELETE failure can't
    leak state into the next test (mirrors the pattern in
    ``test_neo4j_graph_store.py``)."""
    return f"reconcile-{uuid.uuid4().hex[:8]}"


def _build_fixtures(
    namespace: str,
) -> tuple[Document, DocumentVersion, SemanticDocument]:
    document_id = f"{namespace}-doc"
    version_id = f"{namespace}-ver"
    version = DocumentVersion(
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
    document = Document(
        id=document_id,
        original_filename=version.filename,
        latest_version_id=version.id,
        versions=[version],
    )
    semantic = SemanticDocument(
        id=f"sem-{version_id}",
        document_version_id=version.id,
        document_profile=DocumentProfile(title="Test"),
        sections=[SemanticSection(id=f"{namespace}-s1", heading="Intro", text="hello")],
        validation_status="validated",
        markdown="# Test\n",
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    return document, version, semantic


# ─── Tests ──────────────────────────────────────────────────────────────


def test_drift_simulated_then_reconciled_against_neo4j(
    store: Neo4jGraphStore, namespace: str
) -> None:
    document, version, semantic = _build_fixtures(namespace)

    catalog = InMemoryCatalogStore()
    catalog.save_document_with_version(document=document, version=version)

    projector = KnowledgeProjector(graph_store=store)
    reconciler = ReconciliationService(
        catalog=catalog,
        graph_store=store,
        projector=projector,
        entity_extractor=None,
        get_semantic=lambda d, v: semantic,
    )

    # Healthy baseline: project, then verify no drift.
    projector.project(document=document, version=version, semantic=semantic)
    try:
        assert reconciler.find_drifted_versions() == []

        # Simulate the ADR-012 §4 failure mode: validation landed, projection
        # didn't (or got nuked by an outage). Drop the version's subgraph.
        store.delete_subgraph_for_version(document_id=document.id, version_id=version.id)

        drifted = reconciler.find_drifted_versions()
        assert len(drifted) == 1
        assert drifted[0].document_id == document.id
        assert drifted[0].version_id == version.id

        # Reconcile.
        outcome = reconciler.reconcile_version(document_id=document.id, version_id=version.id)
        assert outcome.projection_ok is True
        assert outcome.error is None

        # Drift cleared; the projection is back.
        assert reconciler.find_drifted_versions() == []
        projection = store.find_subgraph_for_document(document.id)
        node_kinds = {(n.kind, n.id) for n in projection.nodes}
        assert ("document", document.id) in node_kinds
        assert ("version", version.id) in node_kinds
    finally:
        # Clean up so reruns don't accumulate fixture data.
        store.delete_subgraph_for_version(document_id=document.id, version_id=version.id)
