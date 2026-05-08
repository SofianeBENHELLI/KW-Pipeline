"""HTTP coverage for the relation evidence routes (#311, ADR-028).

Two routes:
- ``GET /knowledge/relations/{relation_id}`` — single stored edge.
- ``GET /knowledge/relations/aggregate`` — synthesised doc-doc.

Asserts the auth/scope gate stack (401 in bearer mode without token,
404 hidden-existence when the caller lacks scope on the document the
edge belongs to) plus the happy paths.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.schemas.knowledge import GraphEdge, GraphNode
from app.services.auth import DevModeAuthService, encode_hs256

_SECRET = "k" * 32


def _bearer_token(role: str = "viewer", user_id: str = "viewer-1") -> str:
    return encode_hs256(
        {"sub": user_id, "role": role, "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _swap_user(services, user_id: str) -> None:
    object.__setattr__(services, "auth", DevModeAuthService(user_id=user_id))


def _seed_owned_chunk_relation(
    client: TestClient,
    services,
    *,
    edge_id: str = "ver-x:c1->related_to->c2",
    document_id: str | None = None,
):
    """Upload one document so a personal-scope link exists, then seed a
    deterministic chunk relation tied to that document. Returns
    ``(document_id, edge_id)``."""
    upload = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"hello world", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    body = upload.json()
    doc_id = document_id or body["document_id"]
    ver_id = body["id"]
    services.graph_store.upsert_edges(
        [
            GraphEdge(
                id=edge_id,
                kind="related_to",
                source_id="c1",
                target_id="c2",
                properties={
                    "document_id": doc_id,
                    "version_id": ver_id,
                    "source_chunk_id": "c1",
                    "target_chunk_id": "c2",
                    "score": 0.7,
                    "reason": "Shared keywords on safety reviews.",
                    "shared_keywords": ["safety", "policy"],
                },
            )
        ]
    )
    return doc_id, edge_id


# ─── GET /knowledge/relations/{relation_id} ────────────────────────────


class TestExplainRoute:
    def test_owner_sees_evidence(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        _, edge_id = _seed_owned_chunk_relation(client, services)

        response = client.get(f"/knowledge/relations/{quote(edge_id, safe='')}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["kind"] == "related_to"
        assert body["provenance_class"] == "deterministic"
        assert body["reason"].startswith("Shared keywords")
        assert "score" in body
        assert body["score"] is not None

    def test_unknown_relation_returns_404(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        response = client.get("/knowledge/relations/does-not-exist")
        assert response.status_code == 404, response.text

    def test_other_user_sees_404_hidden_existence(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        _, edge_id = _seed_owned_chunk_relation(client, services)

        # Switch identity — alice has no scope on dev's document.
        _swap_user(services, "alice")
        response = client.get(f"/knowledge/relations/{quote(edge_id, safe='')}")
        assert response.status_code == 404, response.text
        assert "Document not found" in response.text

    def test_anonymous_caller_in_bearer_mode_is_rejected(self, monkeypatch) -> None:
        monkeypatch.setenv("KW_AUTH_MODE", "bearer")
        monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
        client, services = _client_and_services()
        # Seed via contributor token first.
        contrib_headers = {"Authorization": f"Bearer {_bearer_token('contributor')}"}
        upload = client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"hello world", "text/plain")},
            headers=contrib_headers,
        )
        assert upload.status_code == 200, upload.text
        body = upload.json()
        services.graph_store.upsert_edges(
            [
                GraphEdge(
                    id="bearer-edge",
                    kind="related_to",
                    source_id="c1",
                    target_id="c2",
                    properties={
                        "document_id": body["document_id"],
                        "version_id": body["id"],
                        "source_chunk_id": "c1",
                        "target_chunk_id": "c2",
                        "score": 0.5,
                        "reason": "x",
                        "shared_keywords": [],
                    },
                )
            ]
        )
        # No Authorization header → 401.
        response = client.get("/knowledge/relations/bearer-edge")
        assert response.status_code == 401, response.text


# ─── GET /knowledge/relations/aggregate ────────────────────────────────


class TestAggregateRoute:
    def _seed_two_owned_docs_with_cross_edge(self, client: TestClient, services):
        """Two documents owned by the same actor, plus one chunk-level
        cross-doc edge so aggregation has something to return."""
        upload_a = client.post(
            "/documents/upload",
            files={"file": ("a.txt", b"alpha", "text/plain")},
        )
        upload_b = client.post(
            "/documents/upload",
            files={"file": ("b.txt", b"beta", "text/plain")},
        )
        doc_a = upload_a.json()["document_id"]
        doc_b = upload_b.json()["document_id"]
        ver_a = upload_a.json()["id"]

        # Anchor chunks on each side so find_subgraph_for_document picks them up.
        services.graph_store.upsert_nodes(
            [
                GraphNode(
                    id="ca1",
                    kind="chunk",
                    label="ca1",
                    properties={"document_id": doc_a, "version_id": ver_a, "chunk_id": "ca1"},
                ),
                GraphNode(
                    id="cb1",
                    kind="chunk",
                    label="cb1",
                    properties={
                        "document_id": doc_b,
                        "version_id": upload_b.json()["id"],
                        "chunk_id": "cb1",
                    },
                ),
            ]
        )
        services.graph_store.upsert_edges(
            [
                GraphEdge(
                    id="x-edge",
                    kind="related_to",
                    source_id="ca1",
                    target_id="cb1",
                    properties={
                        "document_id": doc_a,
                        "version_id": ver_a,
                        "source_chunk_id": "ca1",
                        "target_chunk_id": "cb1",
                        "score": 0.6,
                        "reason": "Shared topic across the two documents.",
                        "shared_keywords": ["safety"],
                    },
                )
            ]
        )
        return doc_a, doc_b

    def test_owner_sees_aggregate_with_contributing_pairs(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        doc_a, doc_b = self._seed_two_owned_docs_with_cross_edge(client, services)

        response = client.get(
            "/knowledge/relations/aggregate",
            params={"source_document_id": doc_a, "target_document_id": doc_b},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source_document_id"] == doc_a
        assert body["target_document_id"] == doc_b
        assert body["pair_count"] == 1
        assert len(body["top_contributing_pairs"]) == 1
        assert body["top_contributing_pairs"][0]["relation_id"] == "x-edge"

    def test_no_cross_edges_returns_404(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        # Two uploads but no cross edges seeded.
        upload_a = client.post(
            "/documents/upload",
            files={"file": ("a.txt", b"alpha", "text/plain")},
        )
        upload_b = client.post(
            "/documents/upload",
            files={"file": ("b.txt", b"beta", "text/plain")},
        )
        response = client.get(
            "/knowledge/relations/aggregate",
            params={
                "source_document_id": upload_a.json()["document_id"],
                "target_document_id": upload_b.json()["document_id"],
            },
        )
        assert response.status_code == 404, response.text

    def test_other_user_sees_404_on_either_side(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, services = _client_and_services()
        doc_a, doc_b = self._seed_two_owned_docs_with_cross_edge(client, services)
        # Switch to alice — neither doc is in her scope.
        _swap_user(services, "alice")
        response = client.get(
            "/knowledge/relations/aggregate",
            params={"source_document_id": doc_a, "target_document_id": doc_b},
        )
        assert response.status_code == 404, response.text
