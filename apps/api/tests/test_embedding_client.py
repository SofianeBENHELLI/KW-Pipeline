"""Unit tests for the Phase 3 embedding client (ADR-015).

The default ``pytest`` invocation hits :class:`FakeEmbeddingClient`
exclusively — no network, no `voyageai` SDK required. The Voyage
implementation is exercised through a stub passed via the
``client`` constructor parameter so the SDK shape is documented in
tests and protected from accidental drift, without ever issuing a
real HTTP call. Real Voyage API calls live behind the
``embedding_integration`` pytest marker (per the marker added in
``pyproject.toml`` at the same time as this module).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from app.services.knowledge.embedding_client import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_VOYAGE_MODEL,
    VOYAGE_MODEL_DIMS,
    EmbeddingClient,
    FakeEmbeddingClient,
    VoyageEmbeddingClient,
)


# ─── FakeEmbeddingClient ───────────────────────────────────────────────────


class TestFakeEmbeddingClient:
    def test_protocol_conformance(self) -> None:
        """Fake matches the runtime-checkable Protocol surface."""
        fake = FakeEmbeddingClient()
        assert isinstance(fake, EmbeddingClient)
        assert fake.name == "fake-embedding"

    def test_dim_default_and_override(self) -> None:
        assert FakeEmbeddingClient().dim == 16
        assert FakeEmbeddingClient(dim=32).dim == 32

    def test_dim_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            FakeEmbeddingClient(dim=0)
        with pytest.raises(ValueError, match="positive"):
            FakeEmbeddingClient(dim=-4)

    def test_embed_documents_returns_one_vector_per_input(self) -> None:
        fake = FakeEmbeddingClient(dim=16)
        out = fake.embed_documents(["alpha", "beta", "gamma"])
        assert len(out) == 3
        for vec in out:
            assert len(vec) == 16
            for component in vec:
                assert -1.0 <= component <= 1.0

    def test_embed_documents_empty_input_returns_empty_list(self) -> None:
        fake = FakeEmbeddingClient()
        # ``Sequence[str]`` accepts the empty list — should pass through cleanly.
        assert fake.embed_documents([]) == []

    def test_embed_documents_is_deterministic(self) -> None:
        fake_a = FakeEmbeddingClient(dim=16)
        fake_b = FakeEmbeddingClient(dim=16)
        # Same text → identical vectors regardless of which instance encodes.
        assert fake_a.embed_documents(["hello"]) == fake_b.embed_documents(["hello"])

    def test_embed_documents_distinguishes_inputs(self) -> None:
        fake = FakeEmbeddingClient(dim=16)
        a = fake.embed_documents(["alpha"])[0]
        b = fake.embed_documents(["beta"])[0]
        assert a != b

    def test_embed_documents_vectors_are_l2_normalised(self) -> None:
        fake = FakeEmbeddingClient(dim=32)
        for vec in fake.embed_documents(["x", "longer text", "third example"]):
            length = math.sqrt(sum(c * c for c in vec))
            assert math.isclose(length, 1.0, rel_tol=1e-9, abs_tol=1e-9)

    def test_embed_query_vector_shape(self) -> None:
        fake = FakeEmbeddingClient(dim=16)
        vec = fake.embed_query("how do i train a chunker")
        assert isinstance(vec, list)
        assert len(vec) == 16
        length = math.sqrt(sum(c * c for c in vec))
        assert math.isclose(length, 1.0, rel_tol=1e-9, abs_tol=1e-9)

    def test_asymmetric_query_and_document_differ_for_same_text(self) -> None:
        """Default ``asymmetric=True`` — query encoding differs from doc."""
        fake = FakeEmbeddingClient(dim=16)
        doc_vec = fake.embed_documents(["the same"])[0]
        qry_vec = fake.embed_query("the same")
        assert doc_vec != qry_vec

    def test_symmetric_query_and_document_match_for_same_text(self) -> None:
        """``asymmetric=False`` routes both calls to the same hash bucket."""
        fake = FakeEmbeddingClient(dim=16, asymmetric=False)
        doc_vec = fake.embed_documents(["the same"])[0]
        qry_vec = fake.embed_query("the same")
        assert doc_vec == qry_vec

    def test_calls_log(self) -> None:
        """Tests can introspect what the fake was asked to embed."""
        fake = FakeEmbeddingClient()
        fake.embed_documents(["a", "b"])
        fake.embed_query("q")
        assert fake.calls == [
            {"method": "embed_documents", "texts": ["a", "b"]},
            {"method": "embed_query", "query": "q"},
        ]

    def test_dim_larger_than_one_digest_loops(self) -> None:
        """`dim` >= 32 floats forces the SHA-256 hash loop to run twice."""
        fake = FakeEmbeddingClient(dim=64)
        vec = fake.embed_documents(["needs more bytes"])[0]
        assert len(vec) == 64
        # Still normalised even after multiple-digest concatenation.
        length = math.sqrt(sum(c * c for c in vec))
        assert math.isclose(length, 1.0, rel_tol=1e-9, abs_tol=1e-9)


# ─── VoyageEmbeddingClient (with injected stub) ────────────────────────────


class _StubVoyageResponse:
    """Mimics the SDK's response object shape: just an ``embeddings`` list."""

    def __init__(self, embeddings: list[list[float]]) -> None:
        self.embeddings = embeddings


class _StubVoyageClient:
    """Records calls and returns canned responses, mimicking voyageai.Client."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls: list[dict[str, Any]] = []
        self._counter = 0

    def embed(self, *, texts: list[str], model: str, input_type: str) -> _StubVoyageResponse:
        self.calls.append({"texts": list(texts), "model": model, "input_type": input_type})
        # Generate distinct vectors so order can be checked.
        out: list[list[float]] = []
        for _ in texts:
            self._counter += 1
            base = float(self._counter)
            out.append([base + i * 0.01 for i in range(self.dim)])
        return _StubVoyageResponse(out)


class _MalformedVoyageClient:
    def embed(self, *, texts: list[str], model: str, input_type: str) -> Any:  # noqa: ARG002
        # SDK shape drift — return something without ``embeddings``.
        return object()


class TestVoyageEmbeddingClient:
    def test_constructor_requires_api_key(self) -> None:
        with pytest.raises(RuntimeError, match="non-empty `api_key`"):
            VoyageEmbeddingClient(api_key="", client=_StubVoyageClient())

    def test_default_model_and_dim_resolved_from_static_map(self) -> None:
        stub = _StubVoyageClient()
        emb = VoyageEmbeddingClient(api_key="x", client=stub)
        assert emb.name == "voyage"
        # ``voyage-3`` is in ``VOYAGE_MODEL_DIMS`` so no probe is needed.
        assert emb.dim == VOYAGE_MODEL_DIMS[DEFAULT_VOYAGE_MODEL]
        assert stub.calls == []  # `dim` resolution didn't issue a request.

    def test_unknown_model_probes_for_dim(self) -> None:
        stub = _StubVoyageClient(dim=7)
        emb = VoyageEmbeddingClient(api_key="x", model="custom-private", client=stub)
        # First access of ``dim`` triggers a single probe call.
        assert emb.dim == 7
        # Second access is cached — no additional call.
        assert emb.dim == 7
        assert len(stub.calls) == 1
        assert stub.calls[0]["input_type"] == "document"
        assert stub.calls[0]["texts"] == ["dim_probe"]

    def test_embed_documents_passes_input_type_document(self) -> None:
        stub = _StubVoyageClient(dim=4)
        emb = VoyageEmbeddingClient(api_key="x", client=stub, batch_size=8)
        out = emb.embed_documents(["hello", "world"])
        assert len(out) == 2
        assert all(len(vec) == 4 for vec in out)
        assert len(stub.calls) == 1
        assert stub.calls[0]["input_type"] == "document"
        assert stub.calls[0]["model"] == DEFAULT_VOYAGE_MODEL

    def test_embed_documents_empty_input_short_circuits(self) -> None:
        stub = _StubVoyageClient()
        emb = VoyageEmbeddingClient(api_key="x", client=stub)
        assert emb.embed_documents([]) == []
        assert stub.calls == []

    def test_embed_documents_batches_at_batch_size(self) -> None:
        stub = _StubVoyageClient(dim=2)
        emb = VoyageEmbeddingClient(api_key="x", client=stub, batch_size=2)
        out = emb.embed_documents(["a", "b", "c", "d", "e"])
        assert len(out) == 5
        # 2 + 2 + 1 = three batched calls.
        assert [len(c["texts"]) for c in stub.calls] == [2, 2, 1]

    def test_batch_size_clamped_to_at_least_one(self) -> None:
        stub = _StubVoyageClient(dim=2)
        emb = VoyageEmbeddingClient(api_key="x", client=stub, batch_size=0)
        emb.embed_documents(["a", "b"])
        # Each text becomes its own call.
        assert [len(c["texts"]) for c in stub.calls] == [1, 1]

    def test_embed_query_passes_input_type_query(self) -> None:
        stub = _StubVoyageClient(dim=4)
        emb = VoyageEmbeddingClient(api_key="x", client=stub)
        vec = emb.embed_query("user question?")
        assert len(vec) == 4
        assert len(stub.calls) == 1
        assert stub.calls[0]["input_type"] == "query"
        assert stub.calls[0]["texts"] == ["user question?"]

    def test_malformed_response_raises(self) -> None:
        emb = VoyageEmbeddingClient(api_key="x", client=_MalformedVoyageClient())
        with pytest.raises(RuntimeError, match="embeddings"):
            emb.embed_documents(["x"])

    def test_default_batch_size_constant(self) -> None:
        # The constant is part of the public surface — pin its value so a
        # change is intentional.
        assert DEFAULT_BATCH_SIZE == 128

    def test_voyage_dim_map_includes_default(self) -> None:
        assert DEFAULT_VOYAGE_MODEL in VOYAGE_MODEL_DIMS
        assert VOYAGE_MODEL_DIMS[DEFAULT_VOYAGE_MODEL] == 1024
