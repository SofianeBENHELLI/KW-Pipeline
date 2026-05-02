"""HTTP-level tests for the Phase 1 knowledge endpoints.

Covers:

- ``GET /documents/{id}/graph`` returns the projection for one
  document family.
- ``GET /knowledge/graph`` walks the catalog-wide projection with
  cursor pagination + limit guardrails.
- The validate-route side-effect actually projects when a
  ``KnowledgeProjector`` is wired into ``PipelineServices``.
- The validate route still succeeds when projection raises (the
  catalog write is the source of truth).
- The reject route does NOT trigger projection.
- ``build_services()`` with no env vars set leaves projection
  disabled and the new endpoints still return empty payloads.

Tests construct a ``PipelineServices`` directly so they don't depend
on env vars and follow the existing review-route test pattern (drive
the FSM via HTTP routes only).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import PipelineServices, build_services
from app.main import create_app
from app.services.knowledge import InMemoryGraphStore, KnowledgeProjector


def _drive_to_needs_review(
    client: TestClient,
    *,
    filename: str = "policy.txt",
    content: bytes | None = None,
) -> dict:
    """Upload + extract + semantic. Mirrors test_routes_review.py.

    The content defaults to a per-filename unique payload so callers
    that drive multiple documents in the same test don't trip the
    SHA-256 duplicate-detection guard.
    """
    body = content if content is not None else f"# {filename}\nline one\nline two\n".encode()
    version = client.post(
        "/documents/upload",
        files={"file": (filename, body, "text/plain")},
    ).json()
    client.post(f"/documents/{version['document_id']}/versions/{version['id']}/extract")
    client.post(f"/documents/{version['document_id']}/versions/{version['id']}/semantic")
    return version


@pytest.fixture
def services_with_projector() -> PipelineServices:
    """A services container with the knowledge layer wired."""
    base = build_services()
    graph_store = InMemoryGraphStore()
    projector = KnowledgeProjector(graph_store=graph_store)
    return PipelineServices(
        storage=base.storage,
        documents=base.documents,
        parsers=base.parsers,
        extraction_jobs=base.extraction_jobs,
        semantic_extractor=base.semantic_extractor,
        markdown_generator=base.markdown_generator,
        semantic_outputs=base.semantic_outputs,
        idempotency=base.idempotency,
        graph_store=graph_store,
        knowledge_projector=projector,
    )


@pytest.fixture
def client_with_projector(services_with_projector) -> TestClient:
    return TestClient(create_app(services_with_projector))


def test_get_document_graph_is_empty_for_unknown_document(client_with_projector):
    resp = client_with_projector.get("/documents/missing-doc/graph")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["nodes"] == []
    assert payload["edges"] == []
    assert payload["document_id"] == "missing-doc"


def test_validate_route_projects_into_graph(client_with_projector):
    v = _drive_to_needs_review(client_with_projector, filename="another.txt")

    resp = client_with_projector.post(
        f"/documents/{v['document_id']}/versions/{v['id']}/validate",
        json={"reviewer_note": "ok"},
    )
    assert resp.status_code == 200, resp.text

    graph_resp = client_with_projector.get(f"/documents/{v['document_id']}/graph")
    assert graph_resp.status_code == 200
    payload = graph_resp.json()
    kinds = {n["kind"] for n in payload["nodes"]}
    assert {"document", "version", "chunk"} <= kinds
    assert "section" not in kinds, "v0.2 dropped section nodes (#144)"
    assert payload["document_id"] == v["document_id"]
    assert payload["version_id"] == v["id"]
    # Allowed structural and deterministic edge kinds in v0.2. Entity
    # / has_entity stays Phase 2 (off without ANTHROPIC_API_KEY).
    edge_kinds = {e["kind"] for e in payload["edges"]}
    assert edge_kinds <= {
        "part_of",
        "has_chunk",
        "has_version",
        "belongs_to",
        "related_to",
        "shares_keyword",
        "same_topic_as",
    }
    assert "part_of" in edge_kinds


def test_reject_route_does_not_project(client_with_projector):
    v = _drive_to_needs_review(client_with_projector, filename="bad.txt")

    resp = client_with_projector.post(
        f"/documents/{v['document_id']}/versions/{v['id']}/reject",
        json={"reviewer_note": "no"},
    )
    assert resp.status_code == 200

    graph_resp = client_with_projector.get(f"/documents/{v['document_id']}/graph")
    assert graph_resp.status_code == 200
    payload = graph_resp.json()
    assert payload["nodes"] == []
    assert payload["edges"] == []


def test_validate_succeeds_even_if_projection_raises(monkeypatch, services_with_projector):
    """If the graph store throws, validation still succeeds; the catalog
    is the source of truth and the graph catches up later."""
    client = TestClient(create_app(services_with_projector))
    v = _drive_to_needs_review(client, filename="boom.txt")

    def explode(*_args, **_kwargs):
        raise RuntimeError("graph is on fire")

    monkeypatch.setattr(services_with_projector.knowledge_projector, "project", explode)

    resp = client.post(
        f"/documents/{v['document_id']}/versions/{v['id']}/validate",
        json={"reviewer_note": "ok"},
    )
    assert resp.status_code == 200, resp.text
    # The catalog wrote the validated status; the graph stayed empty.
    graph_resp = client.get(f"/documents/{v['document_id']}/graph")
    assert graph_resp.json()["nodes"] == []
    # And the catalog says VALIDATED.
    fetched = client.get(f"/documents/{v['document_id']}").json()
    assert fetched["versions"][0]["status"] == "VALIDATED"


def test_knowledge_graph_endpoint_paginates(client_with_projector):
    # Validate three documents.
    for i in range(3):
        v = _drive_to_needs_review(client_with_projector, filename=f"doc-{i}.txt")
        resp = client_with_projector.post(
            f"/documents/{v['document_id']}/versions/{v['id']}/validate",
            json={"reviewer_note": "ok"},
        )
        assert resp.status_code == 200

    page1 = client_with_projector.get("/knowledge/graph?limit=2").json()
    assert len(page1["nodes"]) == 2
    assert page1["next_cursor"] is not None

    page2 = client_with_projector.get(
        f"/knowledge/graph?limit=2&cursor={page1['next_cursor']}"
    ).json()
    assert len(page2["nodes"]) >= 1
    page1_ids = {n["id"] for n in page1["nodes"]}
    page2_ids = {n["id"] for n in page2["nodes"]}
    assert page1_ids.isdisjoint(page2_ids)


def test_knowledge_graph_endpoint_rejects_out_of_range_limit(client_with_projector):
    resp = client_with_projector.get("/knowledge/graph?limit=10000")
    assert resp.status_code == 400


def test_knowledge_graph_endpoint_rejects_invalid_cursor(client_with_projector):
    resp = client_with_projector.get("/knowledge/graph?cursor=not-base64-json")
    assert resp.status_code == 400


def test_disabled_knowledge_layer_returns_empty_graph_endpoints():
    """``build_services`` with no env vars set leaves projection disabled
    and uses an empty in-memory graph store. The endpoints still work —
    they just return empty payloads."""
    services = build_services()
    assert services.knowledge_projector is None
    client = TestClient(create_app(services))

    resp = client.get("/documents/anything/graph")
    assert resp.status_code == 200
    assert resp.json()["nodes"] == []

    resp = client.get("/knowledge/graph")
    assert resp.status_code == 200
    assert resp.json()["nodes"] == []
