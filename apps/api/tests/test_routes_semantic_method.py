"""POST /semantic ``?method=`` route contract."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.services.semantic_generators import (
    SEMANTIC_METHOD_STRUCTURE_FIRST,
    SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE,
    SemanticIntelligenceGenerator,
    _AssetWire,
    _ProfileWire,
    _SemanticEnvelope,
)


class _FakeCompletion:
    def __init__(self) -> None:
        self.usage = type(
            "Usage",
            (),
            {"input_tokens": 1, "output_tokens": 1},
        )()


class _FakeInstructorClient:
    def __init__(self, envelope: _SemanticEnvelope) -> None:
        self._envelope = envelope

    def create_with_completion(self, **_):
        return self._envelope, _FakeCompletion()


def _upload_and_extract(client: TestClient) -> tuple[str, str]:
    version = client.post(
        "/documents/upload",
        files={"file": ("policy.txt", b"some policy text", "text/plain")},
    ).json()
    document_id, version_id = version["document_id"], version["id"]
    client.post(f"/documents/{document_id}/versions/{version_id}/extract")
    return document_id, version_id


def test_post_semantic_with_unknown_method_returns_400():
    services = build_services()
    client = TestClient(create_app(services=services))
    document_id, version_id = _upload_and_extract(client)

    response = client.post(
        f"/documents/{document_id}/versions/{version_id}/semantic",
        params={"method": "bogus"},
    )
    assert response.status_code == 400
    assert "bogus" in response.json()["detail"]


def test_post_semantic_without_method_defaults_to_deterministic():
    services = build_services()
    client = TestClient(create_app(services=services))
    document_id, version_id = _upload_and_extract(client)

    response = client.post(
        f"/documents/{document_id}/versions/{version_id}/semantic"
    )
    assert response.status_code == 200
    assert response.json()["extraction_method"] == SEMANTIC_METHOD_STRUCTURE_FIRST


def test_post_semantic_with_method_llm_runs_llm_generator():
    services = build_services()
    # Inject a fake LLM generator so the test never hits the network.
    services.semantic_outputs._generators[SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE] = (
        SemanticIntelligenceGenerator(
            client=_FakeInstructorClient(
                _SemanticEnvelope(
                    profile=_ProfileWire(
                        title="Supplier Policy",
                        document_type="policy",
                    ),
                    assets=[
                        # ``policy.txt`` parsing produces a single section
                        # whose id is the version_id; tests just need an
                        # asset that won't get dropped by the allow-list,
                        # so we cite the lone section id below by
                        # re-issuing the request after extraction.
                        _AssetWire(
                            type="requirement",
                            text="Must complete the onboarding form.",
                            confidence=0.9,
                            source_reference_ids=["__placeholder__"],
                        ),
                    ],
                ),
            ),
            model="test/fake",
        )
    )
    client = TestClient(create_app(services=services))
    document_id, version_id = _upload_and_extract(client)

    # Reach into the parser output for the real section id so the
    # allow-list keeps the asset rather than dropping it. The parser
    # emits one section keyed by ``"section-1"`` for the plain-text
    # parser; we look it up rather than hardcode.
    deterministic = client.post(
        f"/documents/{document_id}/versions/{version_id}/semantic"
    ).json()
    section_id = deterministic["sections"][0]["id"]
    # Update the queued envelope so the LLM call cites a valid id.
    services.semantic_outputs._generators[SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE] = (
        SemanticIntelligenceGenerator(
            client=_FakeInstructorClient(
                _SemanticEnvelope(
                    profile=_ProfileWire(
                        title="Supplier Policy",
                        document_type="policy",
                    ),
                    assets=[
                        _AssetWire(
                            type="requirement",
                            text="Must complete the onboarding form.",
                            confidence=0.9,
                            source_reference_ids=[section_id],
                        ),
                    ],
                ),
            ),
            model="test/fake",
        )
    )

    response = client.post(
        f"/documents/{document_id}/versions/{version_id}/semantic",
        params={"method": SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["extraction_method"] == SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE
    assert body["document_profile"]["title"] == "Supplier Policy"
    assert [a["text"] for a in body["assets"]] == [
        "Must complete the onboarding form.",
    ]


def test_post_semantic_with_method_change_overwrites_cached_row():
    """Same version, two methods, two persisted rows (only the last
    one survives — the second POST overwrites the first)."""
    services = build_services()
    client = TestClient(create_app(services=services))
    document_id, version_id = _upload_and_extract(client)

    first = client.post(
        f"/documents/{document_id}/versions/{version_id}/semantic",
        params={"method": SEMANTIC_METHOD_STRUCTURE_FIRST},
    )
    assert first.status_code == 200
    assert first.json()["extraction_method"] == SEMANTIC_METHOD_STRUCTURE_FIRST

    # Plug a fake LLM generator AFTER extraction so we know the section id.
    section_id = first.json()["sections"][0]["id"]
    services.semantic_outputs._generators[SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE] = (
        SemanticIntelligenceGenerator(
            client=_FakeInstructorClient(
                _SemanticEnvelope(
                    profile=_ProfileWire(
                        title="LLM Title",
                        document_type="report",
                    ),
                    assets=[
                        _AssetWire(
                            type="claim",
                            text="LLM claim",
                            confidence=0.7,
                            source_reference_ids=[section_id],
                        ),
                    ],
                ),
            ),
            model="test/fake",
        )
    )

    second = client.post(
        f"/documents/{document_id}/versions/{version_id}/semantic",
        params={"method": SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE},
    )
    assert second.status_code == 200
    assert second.json()["extraction_method"] == SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE
    # The persisted GET serves the most recently generated row.
    persisted = client.get(
        f"/documents/{document_id}/versions/{version_id}/semantic"
    ).json()
    assert persisted["extraction_method"] == SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE
    assert persisted["document_profile"]["title"] == "LLM Title"
