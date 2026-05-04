"""Topic-Jaccard document similarity (ADR-025, EPIC-C C.2).

Computes ``sim(a, b) = |Ta ∩ Tb| / |Ta ∪ Tb|`` over the set of topic
ids touched by each document's chunks (already produced by
:class:`app.services.knowledge.topic_clustering.TopicClusteringService`).

Why Jaccard:

- **Deterministic.** Same input topic sets always produce the same
  similarity score. No tie-breaking needed beyond a documented sort
  order in :meth:`top_k`.
- **Free.** Reuses topic ids that are computed for every projected
  document; no Phase 3 vector dependency, no extra LLM spend.
- **Set-based.** Robust to long-document bias that bag-of-words cosine
  exhibits — a 50-page document and a 1-page document that touch the
  same two topics score the same as each other.

This service is the C.2 first slice. The future ``GET /documents/{id}/similar``
HTTP route (C.3) and the nightly batch recompute (deferred) read from
this same surface; ADR-025 §3 records the rationale.
"""

from __future__ import annotations

from typing import Protocol


class DocumentTopicProvider(Protocol):
    """Read-side contract for "what topic ids does document X touch?".

    Decoupled from the concrete clustering / catalog wiring so unit
    tests can inject a fake topic map without spinning up the full
    projection stack. Production wiring threads through whichever
    persistence path holds the topic-membership rows for the active
    catalog.

    Implementations MUST return an empty set (not raise) for documents
    that have no topics yet — a freshly-uploaded document with no
    semantic output is a valid input for the similarity service.
    """

    def topic_ids_for_document(self, document_id: str) -> set[str]:  # pragma: no cover - Protocol
        ...

    def known_document_ids(self) -> list[str]:  # pragma: no cover - Protocol
        """Return the list of document ids the provider can answer
        :meth:`topic_ids_for_document` for. Used by :meth:`top_k` to
        enumerate similarity candidates."""
        ...


class DocumentSimilarityService:
    """Pairwise topic-Jaccard similarity over the catalog (ADR-025).

    The service is stateless: the constructor takes a
    :class:`DocumentTopicProvider` it consults on every call. No
    caching here — the future nightly batch recompute job will own
    that surface.
    """

    def __init__(self, *, topics: DocumentTopicProvider) -> None:
        self._topics = topics

    def compute(self, doc_a_id: str, doc_b_id: str) -> float:
        """Return Jaccard similarity in ``[0.0, 1.0]``.

        Identity: ``compute(x, x) == 1.0`` by definition. If either
        document has no topics yet, returns ``0.0`` (rather than
        raising) so the caller can render "no similar documents yet"
        gracefully during the cold-start window of a fresh catalog.
        """
        if doc_a_id == doc_b_id:
            return 1.0
        topics_a = self._topics.topic_ids_for_document(doc_a_id)
        topics_b = self._topics.topic_ids_for_document(doc_b_id)
        if not topics_a or not topics_b:
            return 0.0
        intersection = topics_a & topics_b
        if not intersection:
            return 0.0
        union = topics_a | topics_b
        # ``union`` is non-empty because at least one of ``topics_a`` /
        # ``topics_b`` is non-empty (guarded above); guard against
        # divide-by-zero is therefore unnecessary, but the explicit
        # ``len`` keeps the formula readable.
        return len(intersection) / len(union)

    def top_k(self, doc_id: str, k: int) -> list[tuple[str, float]]:
        """Return the K most similar documents to ``doc_id``.

        Excludes ``doc_id`` itself. Sorted by similarity descending,
        ties broken by ``document_id`` ascending so the order is
        deterministic across runs. Documents with similarity ``0.0``
        are dropped — they carry no signal and would dilute the list.

        ``k <= 0`` returns ``[]``. If the catalog contains no other
        documents (or none with overlapping topics), returns ``[]``.
        """
        if k <= 0:
            return []
        candidates = self._topics.known_document_ids()
        scored: list[tuple[str, float]] = []
        for candidate_id in candidates:
            if candidate_id == doc_id:
                continue
            score = self.compute(doc_id, candidate_id)
            if score <= 0.0:
                continue
            scored.append((candidate_id, score))
        # ``-score`` first so descending similarity wins, then
        # ``candidate_id`` ascending for the stable tie-break documented
        # above.
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:k]
