"""Unit tests for the in-memory BM25 keyword index (EPIC-4 item 4.3)."""

from __future__ import annotations

import pytest

from app.services.knowledge.bm25 import BM25Hit, BM25Index, tokenize

# ─── tokenize ──────────────────────────────────────────────────────────


class TestTokenize:
    def test_lowercase_and_split(self) -> None:
        assert tokenize("Battery Thermal Management") == [
            "battery",
            "thermal",
            "management",
        ]

    def test_strips_punctuation(self) -> None:
        assert tokenize("foo, bar! baz?") == ["foo", "bar", "baz"]

    def test_handles_unicode_word_chars(self) -> None:
        # ``re.UNICODE`` is on by default in Python 3 — accents stay
        # inside the token, separators around them split.
        assert tokenize("café, résumé") == ["café", "résumé"]

    def test_min_token_length_filter(self) -> None:
        # Drop noise tokens by length.
        assert tokenize("a battery is on", min_token_length=2) == [
            "battery",
            "is",
            "on",
        ]

    def test_empty_returns_empty(self) -> None:
        assert tokenize("") == []
        assert tokenize("   ") == []


# ─── BM25Index basics ──────────────────────────────────────────────────


class TestBM25IndexBasics:
    def test_empty_corpus_returns_no_hits(self) -> None:
        idx = BM25Index([])
        with pytest.raises(ValueError, match="query must not be empty"):
            idx.search("")
        # Non-empty query against empty corpus → no hits.
        assert idx.search("anything", limit=5) == []
        assert idx.num_docs == 0
        assert idx.avg_doc_length == 0.0

    def test_known_keyword_hit_outranks_unrelated(self) -> None:
        idx = BM25Index(
            [
                ("chunk-thermal", "Battery thermal management cooling loop"),
                ("chunk-revenue", "Quarterly revenue and earnings call"),
                ("chunk-ml", "Transformer attention mechanisms"),
            ]
        )
        hits = idx.search("battery cooling", limit=5)
        assert hits, "non-trivial query should produce a hit"
        # The thermal chunk has both keywords; the other two share
        # zero query tokens.
        assert hits[0].chunk_id == "chunk-thermal"

    def test_zero_score_chunks_are_filtered(self) -> None:
        """Chunks that share no tokens with the query are dropped from
        the result entirely — surfacing them as "ranked 0.0" would be
        misleading."""
        idx = BM25Index(
            [
                ("chunk-a", "alpha beta gamma"),
                ("chunk-b", "delta epsilon zeta"),
            ]
        )
        hits = idx.search("alpha", limit=5)
        assert [h.chunk_id for h in hits] == ["chunk-a"]

    def test_tie_broken_by_chunk_id_ascending(self) -> None:
        idx = BM25Index(
            [
                ("chunk-z", "battery cooling"),
                ("chunk-a", "battery cooling"),
            ]
        )
        hits = idx.search("battery cooling", limit=5)
        # Identical text → identical score → tie-break on id ascending.
        assert [h.chunk_id for h in hits] == ["chunk-a", "chunk-z"]

    def test_term_frequency_saturation(self) -> None:
        """BM25's ``k1`` parameter saturates the term-frequency boost:
        doubling a term's count does NOT double its contribution."""
        idx = BM25Index(
            [
                ("chunk-once", "battery"),
                ("chunk-many", "battery battery battery battery battery"),
            ]
        )
        once = idx.score("battery", "chunk-once")
        many = idx.score("battery", "chunk-many")
        assert many > once  # more occurrences → higher
        assert many < once * 5  # but not linearly higher


class TestBM25IndexValidation:
    def test_rejects_negative_k1(self) -> None:
        with pytest.raises(ValueError, match="k1 must be"):
            BM25Index([("a", "x")], k1=-0.1)

    def test_rejects_b_out_of_range(self) -> None:
        with pytest.raises(ValueError, match=r"b must be in \[0, 1\]"):
            BM25Index([("a", "x")], b=1.5)

    def test_rejects_duplicate_chunk_id(self) -> None:
        with pytest.raises(ValueError, match="duplicate chunk_id"):
            BM25Index([("a", "first"), ("a", "second")])

    def test_rejects_invalid_limit(self) -> None:
        idx = BM25Index([("a", "battery")])
        with pytest.raises(ValueError, match="limit must be"):
            idx.search("battery", limit=0)


class TestBM25IndexHitShape:
    def test_hit_carries_chunk_id_and_score(self) -> None:
        idx = BM25Index([("c1", "battery cooling system")])
        hits = idx.search("battery", limit=1)
        assert isinstance(hits[0], BM25Hit)
        assert hits[0].chunk_id == "c1"
        assert hits[0].score > 0.0
