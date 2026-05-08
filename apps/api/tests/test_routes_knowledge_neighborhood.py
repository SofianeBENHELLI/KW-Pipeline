"""HTTP coverage for the focused neighborhood route (#310, ADR-028).

Asserts the slice-3 gate stack (auth + scope) plus the happy path
through ``GET /knowledge/neighborhood``. The deeper BFS / scoring /
truncation tests live in ``test_knowledge_neighborhood_service.py``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.schemas.knowledge import GraphEdge, GraphNode
from app.services.auth import DevModeAuthService, encode_hs256

_SECRET = "k" * 32


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _swap_user(services, user_id: str) -> None:
    object.__setattr__(services, "auth", DevModeAuthService(user_id=user_id))


def _bearer_token(role: str = "viewer", user_id: str = "viewer-1") -> str:
    return encode_hs256(
        {"sub": user_id, "role": role, "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _seed_owned_chunk_with_neighbor(client: TestClient, services):
    """Upload one document so a personal-scope link exists, then seed
    two chunks belonging to it plus one related_to edge between them.
    Returns ``(document_id, root_chunk_id)``."""
    upload = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"hello world", "text/plain")},
    )
    body = upload.json()
    doc_id = body["document_id"]
    ver_id = body["id"]
    services.graph_store.upsert_nodes(
        [
            GraphNode(
                id="c-root",
                kind="chunk",
                label="root",
                properties={
                    "document_id": doc_id,
                    "version_id": ver_id,
                    "chunk_id": "c-root",
                },
            ),
            GraphNode(
                id="c-neighbor",
                kind="chunk",
                label="neighbor",
                properties={
                    "document_id": doc_id,
                    "version_id": ver_id,
                    "chunk_id": "c-neighbor",
                },
            ),
        ]
    )
    services.graph_store.upsert_edges(
        [
            GraphEdge(
                id=f"{ver_id}:c-root->related_to->c-neighbor",
                kind="related_to",
                source_id="c-root",
                target_id="c-neighbor",
                properties={
                    "document_id": doc_id,
                    "version_id": ver_id,
                    "source_chunk_id": "c-root",
                    "target_chunk_id": "c-neighbor",
                    "score": 0.7,
                    "reason": "shared safety + policy",
                    "shared_keywords": ["safety", "policy"],
                },
            )
        ]
    )
    return doc_id, "c-root"


# ─── Happy path ───────────────────────────────────────────────────────


class TestHappyPath:
    def test_owner_sees_neighborhood(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        _, root = _seed_owned_chunk_with_neighbor(client, services)
        response = client.get(
            "/knowledge/neighborhood",
            params={"root_kind": "chunk", "root_id": root, "depth": 1, "edge_limit": 10},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["schema_version"] == "v0.1"
        assert body["root_id"] == root
        assert len(body["edges"]) == 1
        assert body["edges"][0]["score"] is not None
        assert body["edges"][0]["strength_class"] in ("strong", "medium", "weak")

    def test_topic_rooted_neighborhood_returns_chunks(self, monkeypatch) -> None:
        # Reviewer ask: topic root_kind needs HTTP-level coverage too.
        # Seed a topic node owned by an uploaded document, link two
        # chunks to it via ``belongs_to`` (structural), and confirm
        # the route returns the topic + its member chunks.
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        upload = client.post(
            "/documents/upload",
            files={"file": ("topic.txt", b"hello topic", "text/plain")},
        )
        body = upload.json()
        doc_id = body["document_id"]
        ver_id = body["id"]
        services.graph_store.upsert_nodes(
            [
                GraphNode(
                    id="t-1",
                    kind="topic",
                    label="Topic 1",
                    properties={
                        "document_id": doc_id,
                        "version_id": ver_id,
                        "topic_id": "t-1",
                    },
                ),
                GraphNode(
                    id="ch-a",
                    kind="chunk",
                    label="ch-a",
                    properties={
                        "document_id": doc_id,
                        "version_id": ver_id,
                        "chunk_id": "ch-a",
                    },
                ),
                GraphNode(
                    id="ch-b",
                    kind="chunk",
                    label="ch-b",
                    properties={
                        "document_id": doc_id,
                        "version_id": ver_id,
                        "chunk_id": "ch-b",
                    },
                ),
            ]
        )
        services.graph_store.upsert_edges(
            [
                GraphEdge(
                    id="ch-a->belongs_to->t-1",
                    kind="belongs_to",
                    source_id="ch-a",
                    target_id="t-1",
                    properties={"document_id": doc_id, "version_id": ver_id},
                ),
                GraphEdge(
                    id="ch-b->belongs_to->t-1",
                    kind="belongs_to",
                    source_id="ch-b",
                    target_id="t-1",
                    properties={"document_id": doc_id, "version_id": ver_id},
                ),
            ]
        )
        response = client.get(
            "/knowledge/neighborhood",
            params={"root_kind": "topic", "root_id": "t-1", "depth": 1, "edge_limit": 10},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["root_kind"] == "topic"
        assert body["root_id"] == "t-1"
        node_ids = {node["id"] for node in body["nodes"]}
        assert {"t-1", "ch-a", "ch-b"} <= node_ids
        # Belongs-to edges are structural — score stays None.
        for edge in body["edges"]:
            assert edge["score"] is None
            assert edge["strength_class"] is None


# ─── Unknown / mismatched root → 404 ──────────────────────────────────


class TestNotFound:
    def test_unknown_root_returns_404(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        response = client.get(
            "/knowledge/neighborhood",
            params={"root_kind": "chunk", "root_id": "missing", "depth": 1},
        )
        assert response.status_code == 404, response.text


# ─── D.5 hidden-existence ─────────────────────────────────────────────


class TestScopeHidden:
    def test_other_user_sees_404(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        _, root = _seed_owned_chunk_with_neighbor(client, services)

        # Switch identity — alice doesn't have scope on dev's doc.
        _swap_user(services, "alice")
        response = client.get(
            "/knowledge/neighborhood",
            params={"root_kind": "chunk", "root_id": root, "depth": 1},
        )
        assert response.status_code == 404, response.text


# ─── Bearer mode: 401 without token ───────────────────────────────────


class TestUnauthenticated:
    def test_anonymous_caller_in_bearer_mode_is_rejected(self, monkeypatch) -> None:
        monkeypatch.setenv("KW_AUTH_MODE", "bearer")
        monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
        client, services = _client_and_services()
        # Seed a node so the route has something to find.
        services.graph_store.upsert_nodes(
            [
                GraphNode(
                    id="c-bearer",
                    kind="chunk",
                    label="x",
                    properties={"document_id": "doc-bearer"},
                )
            ]
        )
        response = client.get(
            "/knowledge/neighborhood",
            params={"root_kind": "chunk", "root_id": "c-bearer", "depth": 1},
        )
        assert response.status_code == 401, response.text


# ─── Query-param validation ───────────────────────────────────────────


class TestQueryValidation:
    def test_depth_above_max_returns_422(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        response = client.get(
            "/knowledge/neighborhood",
            params={"root_kind": "chunk", "root_id": "x", "depth": 5},
        )
        assert response.status_code == 422

    def test_unknown_root_kind_returns_422(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        response = client.get(
            "/knowledge/neighborhood",
            params={"root_kind": "bogus", "root_id": "x", "depth": 1},
        )
        assert response.status_code == 422
