"""HTTP-level tests for the Phase 2 entity-extraction side-effect.

Same pattern as ``test_routes_knowledge.py``: drive a document through
upload → extract → semantic → validate via the HTTP routes, then
inspect the graph via ``GET /documents/{id}/graph``. The LLM is a
``FakeLLMClient`` with recorded responses so the default test suite
never touches the network.

Coverage targets:

- after VALIDATED, the graph contains ``(:Entity)`` nodes plus
  ``HAS_ENTITY`` edges with a ``source_reference_id`` property;
- the reject route does NOT trigger entity extraction;
- if extraction raises, validation still succeeds (fire-and-log).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import PipelineServices, build_services
from app.main import create_app
from app.services.knowledge import (
    EntityExtractor,
    FakeLLMClient,
    InMemoryGraphStore,
    KnowledgeProjector,
)


def _drive_to_needs_review(
    client: TestClient,
    *,
    filename: str = "policy.txt",
    content: bytes | None = None,
) -> dict:
    body = content if content is not None else f"# {filename}\nline one\nline two\n".encode()
    version = client.post(
        "/documents/upload",
        files={"file": (filename, body, "text/plain")},
    ).json()
    client.post(f"/documents/{version['document_id']}/versions/{version['id']}/extract")
    client.post(f"/documents/{version['document_id']}/versions/{version['id']}/semantic")
    return version


def _services_with_extractor(
    fake_llm: FakeLLMClient,
) -> tuple[PipelineServices, InMemoryGraphStore]:
    base = build_services()
    graph_store = InMemoryGraphStore()
    projector = KnowledgeProjector(graph_store=graph_store)
    extractor = EntityExtractor(llm=fake_llm)
    services = PipelineServices(
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
        entity_extractor=extractor,
    )
    return services, graph_store


def _allowed_ref_id(client: TestClient, document_id: str, version_id: str) -> str:
    """Pull a real source-reference id from the semantic doc for this version.

    The default pipeline assigns one source_reference per section line,
    so any non-empty section's ``source_reference_ids`` is fair game.
    """
    semantic = client.get(f"/documents/{document_id}/versions/{version_id}/semantic").json()
    for section in semantic["sections"]:
        if section["source_reference_ids"]:
            return section["source_reference_ids"][0]
    raise AssertionError("No section with source_reference_ids in test fixture")


@pytest.fixture
def fake_llm() -> FakeLLMClient:
    """A fake that emits two valid triples for any section with refs.

    The extractor calls ``complete_with_tool`` once per section; we
    enqueue many responses so any number of sections in the test
    fixture is covered. Triples cite a placeholder ref id which the
    test rewrites to the real one before the call. Simpler approach
    here: enqueue empties, then override per-test as needed.
    """
    return FakeLLMClient()


def test_validate_route_extracts_entities_into_graph(fake_llm):
    services, _store = _services_with_extractor(fake_llm)
    client = TestClient(create_app(services))

    v = _drive_to_needs_review(client, filename="policy-extract.txt")
    ref_id = _allowed_ref_id(client, v["document_id"], v["id"])

    # The fake will be invoked once per section in the semantic doc.
    # Plain text typically yields one section; enqueue an extra empty
    # response for safety so the test does not depend on parser
    # internals.
    fake_llm.enqueue(
        {
            "triples": [
                {
                    "subject": "Acme Corp",
                    "subject_type": "Organization",
                    "predicate": "OPERATES",
                    "object": "Service A",
                    "object_type": "Product",
                    "confidence": 0.85,
                    "source_reference_ids": [ref_id],
                },
                {
                    "subject": "Service A",
                    "subject_type": "Product",
                    "predicate": "BELONGS_TO",
                    "object": "Acme Corp",
                    "object_type": "Organization",
                    "confidence": 0.8,
                    "source_reference_ids": [ref_id],
                },
            ]
        },
        {"input_tokens": 100, "output_tokens": 30},
    )
    # Top up with empty responses for any extra sections.
    for _ in range(5):
        fake_llm.enqueue({"triples": []}, {"input_tokens": 5, "output_tokens": 0})

    resp = client.post(
        f"/documents/{v['document_id']}/versions/{v['id']}/validate",
        json={"reviewer_note": "ok"},
    )
    assert resp.status_code == 200, resp.text

    graph = client.get(f"/documents/{v['document_id']}/graph").json()
    kinds = {n["kind"] for n in graph["nodes"]}
    assert "entity" in kinds, f"Expected entity nodes; got kinds={kinds}"

    has_entity_edges = [e for e in graph["edges"] if e["kind"] == "has_entity"]
    assert has_entity_edges, "Expected at least one HAS_ENTITY edge"
    for edge in has_entity_edges:
        assert edge["properties"].get("source_reference_id") == ref_id


def test_reject_route_does_not_extract(fake_llm):
    services, _store = _services_with_extractor(fake_llm)
    client = TestClient(create_app(services))

    v = _drive_to_needs_review(client, filename="reject-extract.txt")

    resp = client.post(
        f"/documents/{v['document_id']}/versions/{v['id']}/reject",
        json={"reviewer_note": "no"},
    )
    assert resp.status_code == 200
    # The fake LLM must not have been called.
    assert fake_llm.calls == []
    # And the graph is empty (no projection on reject either).
    graph = client.get(f"/documents/{v['document_id']}/graph").json()
    assert graph["nodes"] == []


def test_extraction_failure_does_not_roll_back_validation(monkeypatch):
    fake = FakeLLMClient()
    services, _store = _services_with_extractor(fake)
    client = TestClient(create_app(services))

    v = _drive_to_needs_review(client, filename="boom-extract.txt")

    def explode(**_kwargs):
        raise RuntimeError("LLM is on fire")

    monkeypatch.setattr(services.entity_extractor, "extract", explode)

    resp = client.post(
        f"/documents/{v['document_id']}/versions/{v['id']}/validate",
        json={"reviewer_note": "ok"},
    )
    assert resp.status_code == 200, resp.text
    # The catalog still says VALIDATED — extraction failure is
    # logged-and-skipped, not rolled back.
    fetched = client.get(f"/documents/{v['document_id']}").json()
    assert fetched["versions"][0]["status"] == "VALIDATED"


def test_disabled_extractor_preserves_phase_1a_behaviour():
    """Phase 1a still runs (graph projection) when the extractor is None."""
    base = build_services()
    graph_store = InMemoryGraphStore()
    projector = KnowledgeProjector(graph_store=graph_store)
    services = PipelineServices(
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
        entity_extractor=None,
    )
    client = TestClient(create_app(services))

    v = _drive_to_needs_review(client, filename="phase1-only.txt")
    resp = client.post(
        f"/documents/{v['document_id']}/versions/{v['id']}/validate",
        json={"reviewer_note": "ok"},
    )
    assert resp.status_code == 200

    graph = client.get(f"/documents/{v['document_id']}/graph").json()
    kinds = {n["kind"] for n in graph["nodes"]}
    assert "entity" not in kinds
    # Phase 1a nodes are still present.
    assert {"document", "version", "section"} <= kinds
