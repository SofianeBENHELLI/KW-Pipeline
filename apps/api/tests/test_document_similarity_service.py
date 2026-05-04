"""Topic-Jaccard similarity (ADR-025, EPIC-C C.2).

Pure unit tests against a hand-built ``DocumentTopicProvider``
fake — no clustering, no projection, no FastAPI. The contract under
test is the formula
``sim(a, b) = |Ta ∩ Tb| / |Ta ∪ Tb|`` plus the edge cases the
service docstrings pin (identity, cold-start, sort stability,
exclusion of the query document from ``top_k``).
"""

from __future__ import annotations

import pytest

from app.services.document_similarity_service import (
    DocumentSimilarityService,
    DocumentTopicProvider,
)


class FakeTopicProvider:
    """Minimal :class:`DocumentTopicProvider` for unit tests."""

    def __init__(self, topics_by_document: dict[str, set[str]]) -> None:
        self._topics_by_document = topics_by_document

    def topic_ids_for_document(self, document_id: str) -> set[str]:
        # Defensive copy so the service can't mutate the fixture.
        return set(self._topics_by_document.get(document_id, set()))

    def known_document_ids(self) -> list[str]:
        return list(self._topics_by_document.keys())


def test_fake_provider_satisfies_protocol():
    """The Protocol is structural; assert the fake is accepted at the
    type-system level so a future Protocol-shape change breaks the
    fake first rather than every test downstream."""
    provider: DocumentTopicProvider = FakeTopicProvider({"doc-a": {"topic-1"}})
    assert provider.topic_ids_for_document("doc-a") == {"topic-1"}


def test_compute_identity_returns_one():
    service = DocumentSimilarityService(
        topics=FakeTopicProvider({"doc-a": {"topic-1", "topic-2"}}),
    )
    assert service.compute("doc-a", "doc-a") == 1.0


def test_compute_disjoint_topic_sets_returns_zero():
    service = DocumentSimilarityService(
        topics=FakeTopicProvider(
            {
                "doc-a": {"topic-1", "topic-2"},
                "doc-b": {"topic-3", "topic-4"},
            }
        ),
    )
    assert service.compute("doc-a", "doc-b") == 0.0


def test_compute_partial_overlap_returns_expected_ratio():
    """|Ta ∩ Tb| = 1, |Ta ∪ Tb| = 3 → 1/3."""
    service = DocumentSimilarityService(
        topics=FakeTopicProvider(
            {
                "doc-a": {"topic-1", "topic-2"},
                "doc-b": {"topic-2", "topic-3"},
            }
        ),
    )
    assert service.compute("doc-a", "doc-b") == pytest.approx(1.0 / 3.0)


def test_compute_full_overlap_returns_one():
    service = DocumentSimilarityService(
        topics=FakeTopicProvider(
            {
                "doc-a": {"topic-1", "topic-2"},
                "doc-b": {"topic-1", "topic-2"},
            }
        ),
    )
    assert service.compute("doc-a", "doc-b") == 1.0


def test_compute_returns_zero_when_either_doc_has_no_topics():
    """Cold-start tolerance — a freshly-uploaded document with no
    semantic output (and therefore no topic ids yet) is a valid input
    for the similarity service. The service returns 0.0 instead of
    raising so the route can render "no similar documents yet"
    gracefully."""
    service = DocumentSimilarityService(
        topics=FakeTopicProvider(
            {
                "doc-a": {"topic-1"},
                "doc-empty": set(),
            }
        ),
    )
    assert service.compute("doc-a", "doc-empty") == 0.0
    assert service.compute("doc-empty", "doc-a") == 0.0
    assert service.compute("doc-empty", "doc-missing") == 0.0


def test_compute_for_unknown_document_returns_zero():
    """A document the provider doesn't know about behaves the same as
    a document with no topics yet — the provider returns ``set()``
    and the formula collapses to 0.0."""
    service = DocumentSimilarityService(
        topics=FakeTopicProvider({"doc-a": {"topic-1"}}),
    )
    assert service.compute("doc-a", "doc-ghost") == 0.0


def test_top_k_orders_by_similarity_descending_then_id_ascending():
    """``doc-a`` shares 1 topic with ``doc-b`` (Jaccard 1/3) and 2
    topics with ``doc-c`` (Jaccard 2/3). ``doc-c`` ranks first; the
    list is then truncated to ``k``. Ties on the score are broken by
    document id ascending."""
    service = DocumentSimilarityService(
        topics=FakeTopicProvider(
            {
                "doc-a": {"topic-1", "topic-2", "topic-3"},
                "doc-b": {"topic-3", "topic-4"},
                "doc-c": {"topic-1", "topic-2", "topic-4"},
                "doc-d": {"topic-99"},  # disjoint — must be dropped
            }
        ),
    )
    assert service.top_k("doc-a", k=10) == [
        ("doc-c", pytest.approx(2.0 / 4.0)),
        ("doc-b", pytest.approx(1.0 / 4.0)),
    ]


def test_top_k_breaks_ties_by_document_id_ascending():
    """Two candidates with the same Jaccard score → the lexically
    smaller id wins. Determinism is the contract."""
    service = DocumentSimilarityService(
        topics=FakeTopicProvider(
            {
                "doc-a": {"topic-1", "topic-2"},
                # Both candidates share exactly one topic with doc-a;
                # |Tb ∪ Ta| = |Tc ∪ Ta| = 3, so the scores are equal.
                "doc-c": {"topic-1", "topic-99"},
                "doc-b": {"topic-2", "topic-77"},
            }
        ),
    )
    ranked = service.top_k("doc-a", k=10)
    assert [doc_id for doc_id, _ in ranked] == ["doc-b", "doc-c"]


def test_top_k_excludes_the_query_document_itself():
    service = DocumentSimilarityService(
        topics=FakeTopicProvider(
            {
                "doc-a": {"topic-1", "topic-2"},
                "doc-b": {"topic-1"},
            }
        ),
    )
    assert all(doc_id != "doc-a" for doc_id, _ in service.top_k("doc-a", k=10))


def test_top_k_truncates_to_k():
    service = DocumentSimilarityService(
        topics=FakeTopicProvider(
            {
                "doc-a": {"topic-1"},
                "doc-b": {"topic-1"},
                "doc-c": {"topic-1"},
                "doc-d": {"topic-1"},
            }
        ),
    )
    assert len(service.top_k("doc-a", k=2)) == 2


def test_top_k_returns_empty_when_catalog_has_no_other_documents():
    service = DocumentSimilarityService(
        topics=FakeTopicProvider({"doc-a": {"topic-1"}}),
    )
    assert service.top_k("doc-a", k=10) == []


def test_top_k_returns_empty_when_no_overlap():
    """Even with other documents in the catalog, if none of them
    share a topic with the query, the result is empty (zero-score
    entries are dropped)."""
    service = DocumentSimilarityService(
        topics=FakeTopicProvider(
            {
                "doc-a": {"topic-1"},
                "doc-b": {"topic-2"},
                "doc-c": {"topic-3"},
            }
        ),
    )
    assert service.top_k("doc-a", k=10) == []


def test_top_k_returns_empty_for_non_positive_k():
    service = DocumentSimilarityService(
        topics=FakeTopicProvider(
            {
                "doc-a": {"topic-1"},
                "doc-b": {"topic-1"},
            }
        ),
    )
    assert service.top_k("doc-a", k=0) == []
    assert service.top_k("doc-a", k=-1) == []
