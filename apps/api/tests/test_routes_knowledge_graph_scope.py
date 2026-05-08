"""HTTP coverage for the ``GET /knowledge/graph`` scope filter
(#326, ADR-020 §2).

Two-user isolation: documents uploaded by one actor must not surface
in the other actor's catalog-wide graph walk. The route returns the
filtered page plus an ``omitted_by_scope_count`` so the frontend can
distinguish scope-omitted from cursor-budget truncation.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.schemas.knowledge import GraphEdge, GraphNode
from app.services.auth import DevModeAuthService


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _swap_user(services, user_id: str) -> None:
    object.__setattr__(services, "auth", DevModeAuthService(user_id=user_id))


def _seed_chunk(services, *, chunk_id: str, document_id: str, version_id: str) -> None:
    services.graph_store.upsert_nodes(
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


def _seed_related_edge(
    services,
    *,
    source_chunk_id: str,
    target_chunk_id: str,
    document_id: str,
    version_id: str,
    score: float = 0.5,
) -> str:
    edge_id = f"{version_id}:{source_chunk_id}->related_to->{target_chunk_id}"
    services.graph_store.upsert_edges(
        [
            GraphEdge(
                id=edge_id,
                kind="related_to",
                source_id=source_chunk_id,
                target_id=target_chunk_id,
                properties={
                    "document_id": document_id,
                    "version_id": version_id,
                    "source_chunk_id": source_chunk_id,
                    "target_chunk_id": target_chunk_id,
                    "score": score,
                    "reason": "shared keywords",
                    "shared_keywords": ["a", "b"],
                },
            )
        ]
    )
    return edge_id


def _upload_owned_document(client: TestClient) -> tuple[str, str]:
    """Upload a single document via the route so the personal-scope
    link is created. Returns ``(document_id, version_id)``."""
    response = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["document_id"], body["id"]


# ─── Two-user isolation ──────────────────────────────────────────────


class TestTwoUserIsolation:
    def test_other_user_does_not_see_owners_chunks(self, monkeypatch) -> None:
        # Dev uploads a document and seeds a chunk on the graph store.
        # Switch identity to alice → her graph walk omits dev's chunk
        # entirely and reports it under ``omitted_by_scope_count``.
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        doc_id, ver_id = _upload_owned_document(client)
        _seed_chunk(services, chunk_id="dev-chunk", document_id=doc_id, version_id=ver_id)

        _swap_user(services, "alice")
        response = client.get("/knowledge/graph")
        assert response.status_code == 200, response.text
        body = response.json()
        node_ids = {node["id"] for node in body["nodes"]}
        assert "dev-chunk" not in node_ids
        assert body["omitted_by_scope_count"] >= 1

    def test_owner_sees_their_own_chunks(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        doc_id, ver_id = _upload_owned_document(client)
        _seed_chunk(services, chunk_id="dev-chunk", document_id=doc_id, version_id=ver_id)

        response = client.get("/knowledge/graph")
        assert response.status_code == 200, response.text
        body = response.json()
        node_ids = {node["id"] for node in body["nodes"]}
        assert "dev-chunk" in node_ids
        assert body["omitted_by_scope_count"] == 0

    def test_edges_incident_on_omitted_nodes_are_dropped(self, monkeypatch) -> None:
        # Seed two chunks on dev's document plus an edge connecting
        # them. As alice (no scope), both nodes drop AND the edge
        # drops too — ``edges`` list is empty, the count reports
        # the two omitted nodes.
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        doc_id, ver_id = _upload_owned_document(client)
        _seed_chunk(services, chunk_id="dev-a", document_id=doc_id, version_id=ver_id)
        _seed_chunk(services, chunk_id="dev-b", document_id=doc_id, version_id=ver_id)
        _seed_related_edge(
            services,
            source_chunk_id="dev-a",
            target_chunk_id="dev-b",
            document_id=doc_id,
            version_id=ver_id,
        )
        _swap_user(services, "alice")
        response = client.get("/knowledge/graph")
        body = response.json()
        assert body["edges"] == []
        # Two chunks dropped (the document/version nodes don't carry
        # ``document_id`` in their properties so they survive — but
        # ``dev-a`` and ``dev-b`` definitely drop).
        assert body["omitted_by_scope_count"] >= 2


# ─── Mixed visibility (some docs visible, some not) ──────────────────


class TestMixedVisibility:
    def test_only_accessible_documents_chunks_are_returned(self, monkeypatch) -> None:
        # Set up two documents with explicit scope links via the
        # catalog (avoiding the upload-route's per-actor scope side
        # effects so the test asserts the filter, not the upload
        # route's behaviour). dev-doc owned by dev; alice-doc owned
        # by alice. Alice's view must include alice-doc's chunk and
        # exclude dev-doc's chunk.
        from datetime import UTC, datetime

        from app.schemas.scope import Scope

        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        # Upload as dev so dev-doc's catalog row + personal:dev scope
        # link are created the way real clients do.
        doc_dev, ver_dev = _upload_owned_document(client)
        _seed_chunk(services, chunk_id="dev-only", document_id=doc_dev, version_id=ver_dev)
        # Hand-create an alice-doc with an explicit personal:alice
        # scope. We bypass the upload route here so the swap order
        # doesn't matter — we're testing the filter, not the upload
        # actor.
        doc_alice = "doc-alice-explicit"
        ver_alice = "ver-alice-explicit"
        services.documents.catalog.save_document_with_version(
            __import__("app.schemas.document", fromlist=["Document"]).Document(
                id=doc_alice,
                original_filename="alice.txt",
                latest_version_id=ver_alice,
                created_at=datetime(2026, 5, 8, tzinfo=UTC),
                versions=[],
                scopes=[],
            ),
            __import__("app.schemas.document", fromlist=["DocumentVersion"]).DocumentVersion(
                id=ver_alice,
                document_id=doc_alice,
                version_number=1,
                filename="alice.txt",
                content_type="text/plain",
                file_size=10,
                sha256="b" * 64,
                storage_uri="memory://alice",
                status="STORED",
            ),
        )
        services.documents.catalog.add_scope(
            doc_alice,
            Scope(
                kind="personal",
                ref="alice",
                added_at=datetime(2026, 5, 8, tzinfo=UTC),
                added_by="alice",
            ),
        )
        _seed_chunk(services, chunk_id="alice-only", document_id=doc_alice, version_id=ver_alice)

        _swap_user(services, "alice")
        response = client.get("/knowledge/graph")
        body = response.json()
        node_ids = {node["id"] for node in body["nodes"]}
        assert "alice-only" in node_ids
        assert "dev-only" not in node_ids
        assert body["omitted_by_scope_count"] >= 1


# ─── Entity nodes retained (no document_id property) ─────────────────


class TestEntityNodesRetained:
    def test_node_without_document_id_is_kept(self, monkeypatch) -> None:
        # Cross-doc entity nodes (Phase 2) carry no ``document_id`` on
        # their properties — they're always retained per the ADR-028
        # deferral note. Confirm the filter doesn't accidentally drop
        # them.
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        services.graph_store.upsert_nodes(
            [
                GraphNode(
                    id="entity-shared",
                    kind="entity",
                    label="Shared Entity",
                    properties={},  # no document_id
                )
            ]
        )

        _swap_user(services, "alice")
        response = client.get("/knowledge/graph")
        body = response.json()
        node_ids = {node["id"] for node in body["nodes"]}
        assert "entity-shared" in node_ids


# ─── Per-document cache hit (perf path) ──────────────────────────────


class TestPerDocumentCacheHit:
    def test_multiple_chunks_one_doc_one_catalog_hit(self, monkeypatch) -> None:
        # Reviewer ask: protect the per-document cache that amortises
        # the catalog roundtrip. Wrap ``user_can_access`` with a spy
        # and seed multiple chunks on the SAME document — the spy
        # should be called exactly ONCE per request even though the
        # filter walks 5 chunks.
        from app.services.auth import scope_filter as _scope_filter
        from app.routes import knowledge as _knowledge_routes

        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        doc_id, ver_id = _upload_owned_document(client)
        for i in range(5):
            _seed_chunk(services, chunk_id=f"c{i}", document_id=doc_id, version_id=ver_id)

        call_count = 0
        original = _scope_filter.user_can_access

        def _spy(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original(*args, **kwargs)

        # Patch the symbol on the routes module — the route imports
        # ``user_can_access`` directly, so swapping the module-level
        # binding redirects the helper's lookup too.
        monkeypatch.setattr(_knowledge_routes, "user_can_access", _spy)

        response = client.get("/knowledge/graph")
        assert response.status_code == 200
        # Five chunks share one document_id → exactly one cache miss
        # → exactly one call into ``user_can_access``.
        assert call_count == 1


# ─── Disabled mode bypasses filter ────────────────────────────────────


class TestDisabledModeBypass:
    def test_disabled_mode_returns_unfiltered_page(self, monkeypatch) -> None:
        # ``user_can_access`` returns True for every doc under
        # disabled mode (legacy escape hatch), so the filter is a
        # no-op and ``omitted_by_scope_count`` stays at 0.
        monkeypatch.setenv("KW_AUTH_MODE", "disabled")
        client, services = _client_and_services()
        # Seed a chunk on a doc with NO scope link at all (raw graph
        # node). Default mode would drop it (no scope = inaccessible);
        # disabled mode keeps it.
        services.graph_store.upsert_nodes(
            [
                GraphNode(
                    id="orphan-chunk",
                    kind="chunk",
                    label="orphan",
                    properties={
                        "document_id": "doc-orphan",
                        "version_id": "ver-orphan",
                    },
                )
            ]
        )
        response = client.get("/knowledge/graph")
        body = response.json()
        node_ids = {node["id"] for node in body["nodes"]}
        assert "orphan-chunk" in node_ids
        assert body["omitted_by_scope_count"] == 0
