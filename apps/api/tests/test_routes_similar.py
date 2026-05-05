"""HTTP-level coverage for ``GET /documents/{id}/similar`` (EPIC-C C.3).

Tests inject a fake :class:`DocumentTopicProvider` directly through
``services.document_similarity`` so we exercise the route's ranking,
clamping, identity-exclusion, and cold-start contracts without
requiring the knowledge-layer projection chain to run end-to-end.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.services.document_similarity_service import DocumentSimilarityService


@pytest.fixture(autouse=True)
def _disable_scope_filter(monkeypatch):
    """Bypass the D.5 scope filter — these tests seed via
    ``services.documents.upload`` which doesn't write a scope link
    (that's the upload-route's job). Legacy ``KW_AUTH_MODE=disabled``
    skips the predicate so the topic-ranking + identity-exclusion
    contracts under test remain reachable."""
    monkeypatch.setenv("KW_AUTH_MODE", "disabled")


class _FakeTopicProvider:
    """Hand-crafted :class:`DocumentTopicProvider` for the route tests.

    Mirrors the ``FakeTopicProvider`` used in
    ``test_document_similarity_service.py`` — kept private to this
    module so the unit-level fake can evolve independently if either
    side adds new edge cases.
    """

    def __init__(self, topics_by_document: dict[str, set[str]]) -> None:
        self._topics_by_document = topics_by_document

    def topic_ids_for_document(self, document_id: str) -> set[str]:
        return set(self._topics_by_document.get(document_id, set()))

    def known_document_ids(self) -> list[str]:
        return list(self._topics_by_document.keys())


def _client_with_fake_topics(topic_map: dict[str, set[str]]):
    """Build an app whose similarity service reads from ``topic_map``."""
    services = build_services()
    # Swap the wired provider for the test fake. The dataclass is
    # frozen, but ``object.__setattr__`` is the documented override
    # used in ``__post_init__`` for the same reason.
    object.__setattr__(
        services,
        "document_similarity",
        DocumentSimilarityService(topics=_FakeTopicProvider(topic_map)),
    )
    return TestClient(create_app(services=services)), services


def _seed_document(services, *, content: bytes) -> str:
    """Upload one document and return its catalog id."""
    version = services.documents.upload(
        filename="policy.txt",
        content_type="text/plain",
        content=content,
    )
    return version.document_id


def test_similar_404_for_unknown_document():
    client, _ = _client_with_fake_topics({})

    response = client.get("/documents/does-not-exist/similar")

    assert response.status_code == 404


def test_similar_returns_empty_results_when_query_doc_has_no_topics():
    """Cold-start: query document exists in the catalog but has no
    projected topics yet — the service returns 0.0 against every
    candidate, ``top_k`` drops them, and the route surfaces an empty
    list with HTTP 200 instead of a 5xx."""
    client, services = _client_with_fake_topics({})
    doc_id = _seed_document(services, content=b"first body")
    # Re-bind the topic map now that the catalog has a row, but with
    # no topic ids attached — mirrors the freshly-uploaded state.
    object.__setattr__(
        services,
        "document_similarity",
        DocumentSimilarityService(topics=_FakeTopicProvider({doc_id: set()})),
    )

    response = client.get(f"/documents/{doc_id}/similar")

    assert response.status_code == 200
    assert response.json() == {"document_id": doc_id, "results": []}


def test_similar_ranks_by_jaccard_descending_and_excludes_identity():
    """Two neighbors with overlapping topics — ``doc-c`` shares 2/3 of
    the query's topics, ``doc-b`` shares 1/3. The query document is
    never in its own results."""
    client, services = _client_with_fake_topics({})
    doc_a = _seed_document(services, content=b"first body")
    doc_b = _seed_document(services, content=b"second body different bytes")
    doc_c = _seed_document(services, content=b"third body distinct bytes")
    object.__setattr__(
        services,
        "document_similarity",
        DocumentSimilarityService(
            topics=_FakeTopicProvider(
                {
                    doc_a: {"topic-1", "topic-2", "topic-3"},
                    doc_b: {"topic-3", "topic-4"},
                    doc_c: {"topic-1", "topic-2", "topic-4"},
                }
            )
        ),
    )

    response = client.get(f"/documents/{doc_a}/similar")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == doc_a
    ranked_ids = [row["document_id"] for row in body["results"]]
    # doc_a is excluded; doc_c (Jaccard 2/4 = 0.5) ranks above doc_b
    # (Jaccard 1/4 = 0.25).
    assert ranked_ids == [doc_c, doc_b]
    assert body["results"][0]["similarity"] > body["results"][1]["similarity"]


def test_similar_truncates_to_k():
    """``k=1`` returns only the top match, even when more candidates
    have positive scores. The clamp is applied by the service, not the
    route."""
    client, services = _client_with_fake_topics({})
    doc_a = _seed_document(services, content=b"first body")
    doc_b = _seed_document(services, content=b"second body different bytes")
    doc_c = _seed_document(services, content=b"third body distinct bytes")
    object.__setattr__(
        services,
        "document_similarity",
        DocumentSimilarityService(
            topics=_FakeTopicProvider(
                {
                    doc_a: {"topic-1", "topic-2"},
                    doc_b: {"topic-1"},
                    doc_c: {"topic-2"},
                }
            )
        ),
    )

    response = client.get(f"/documents/{doc_a}/similar", params={"k": 1})

    assert response.status_code == 200
    body = response.json()
    assert len(body["results"]) == 1


def test_similar_rejects_k_above_50():
    """The route validator clamps ``k`` to ``[1, 50]``; FastAPI itself
    yields 422 on out-of-range values rather than silently truncating."""
    client, services = _client_with_fake_topics({})
    doc_a = _seed_document(services, content=b"first body")
    object.__setattr__(
        services,
        "document_similarity",
        DocumentSimilarityService(topics=_FakeTopicProvider({doc_a: {"topic-1"}})),
    )

    response = client.get(f"/documents/{doc_a}/similar", params={"k": 51})

    assert response.status_code == 422


def test_similar_rejects_k_zero():
    client, services = _client_with_fake_topics({})
    doc_a = _seed_document(services, content=b"first body")
    object.__setattr__(
        services,
        "document_similarity",
        DocumentSimilarityService(topics=_FakeTopicProvider({doc_a: {"topic-1"}})),
    )

    response = client.get(f"/documents/{doc_a}/similar", params={"k": 0})

    assert response.status_code == 422


def test_similar_returns_family_filename_and_latest_status():
    """The response surfaces the latest version's filename + status so
    the lineage modal can render the row without a follow-up
    ``GET /documents/{id}`` per neighbor."""
    client, services = _client_with_fake_topics({})
    doc_a = _seed_document(services, content=b"first body")
    doc_b = _seed_document(services, content=b"second body different bytes")
    object.__setattr__(
        services,
        "document_similarity",
        DocumentSimilarityService(
            topics=_FakeTopicProvider(
                {
                    doc_a: {"topic-1"},
                    doc_b: {"topic-1"},
                }
            )
        ),
    )

    response = client.get(f"/documents/{doc_a}/similar")

    body = response.json()
    assert len(body["results"]) == 1
    row = body["results"][0]
    assert row["document_id"] == doc_b
    assert row["family_filename"] == "policy.txt"
    # Status is the catalog default for an unfinished pipeline upload.
    assert row["latest_version_status"] == "STORED"
