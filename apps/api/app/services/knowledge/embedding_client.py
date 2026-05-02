"""Embedding client boundary for the knowledge layer (ADR-015).

The :class:`EmbeddingClient` Protocol is the only seam between the
Phase 3 vector mode (chunk indexing + retrieval) and a concrete
embeddings provider. ADR-015 commits to one provider in v1 — Voyage AI
``voyage-3`` — behind this Protocol so adding a second provider later
(OpenAI, a future first-party Anthropic endpoint, a local
``sentence-transformers`` build) is a new implementation, not a
rewrite of every call site.

Three implementations live here:

- :class:`VoyageEmbeddingClient` is the production wrapper. It
  lazy-imports the ``voyageai`` SDK so this module loads in
  environments without the dependency installed (e.g. minimal CI
  images that only run the unit suite without ``VOYAGE_API_KEY``).
- :class:`FakeEmbeddingClient` is the in-process test double. It
  returns deterministic vectors derived from a SHA-256 of each input
  string, so the default ``pytest`` invocation never reaches the
  network and tests can assert on retrieval order without a real
  embedding provider.
- The :class:`EmbeddingClient` Protocol itself is
  ``@runtime_checkable`` so tests can assert conformance with
  ``isinstance``.

The Protocol surface is intentionally two methods —
``embed_documents`` and ``embed_query`` — because asymmetric models
(separate document and query encoders) are common, and ``voyage-3`` is
one of them. For symmetric models, an implementation can simply route
``embed_query`` to ``embed_documents``.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

# Default Voyage model per ADR-015. Operators can override via
# ``KW_EMBEDDING_MODEL`` without code changes.
DEFAULT_VOYAGE_MODEL = "voyage-3"

# Vector dimensionality for known Voyage models. Used to provision the
# Neo4j vector index at startup (Phase 3 implementation reads
# :attr:`EmbeddingClient.dim` and creates the index against it).
# Adding a new model = adding a row here; no client logic changes.
VOYAGE_MODEL_DIMS: dict[str, int] = {
    "voyage-3": 1024,
    "voyage-3-large": 1024,
    "voyage-3-lite": 512,
    "voyage-code-2": 1536,
    "voyage-law-2": 1024,
    "voyage-finance-2": 1024,
}

# Voyage's batch endpoint accepts up to 128 texts per call as of the
# 2026-05 SDK; chosen as the default upper bound here so the call site
# does not need to know the provider's limit.
DEFAULT_BATCH_SIZE = 128


@runtime_checkable
class EmbeddingClient(Protocol):
    """One embedding call → list of float vectors.

    Two methods because asymmetric encoders (different model heads for
    documents vs queries) are common. The Protocol is shape-only;
    concrete implementations decide whether the two methods route to
    one model or two.
    """

    name: str
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed one or more chunks for indexing. Order-preserving."""

    def embed_query(self, query: str) -> list[float]:
        """Embed a single user query for retrieval."""


# ─── Voyage implementation ───────────────────────────────────────────────


class VoyageEmbeddingClient:
    """Production :class:`EmbeddingClient` against the Voyage AI SDK.

    The SDK is imported in ``__init__`` (not at module load) so that
    importing this module does not require ``voyageai`` to be
    installed. Tests that exercise this class set up their own SDK
    stubs; the default unit suite uses :class:`FakeEmbeddingClient`
    and never touches this module.

    ``voyage-3`` is asymmetric: documents use ``input_type="document"``
    and queries use ``input_type="query"``. The Voyage SDK exposes
    this via the ``input_type`` parameter on ``embed``.
    """

    name: str = "voyage"

    def __init__(  # pragma: no cover - exercised behind pytest -m embedding_integration
        self,
        *,
        api_key: str,
        model: str = DEFAULT_VOYAGE_MODEL,
        batch_size: int = DEFAULT_BATCH_SIZE,
        client: Any = None,
    ) -> None:
        if not api_key:
            raise RuntimeError(
                "VoyageEmbeddingClient requires a non-empty `api_key`. "
                "Set `VOYAGE_API_KEY` (or `KW_VOYAGE_API_KEY`) or use "
                "FakeEmbeddingClient for tests."
            )
        if client is None:
            try:
                import voyageai  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "VoyageEmbeddingClient requires the `voyageai` package. "
                    "Install with `pip install voyageai` or use "
                    "FakeEmbeddingClient for tests."
                ) from exc
            client = voyageai.Client(api_key=api_key)
        self._client = client
        self._model = model
        self._batch_size = max(1, int(batch_size))
        # Public ``dim`` is read by the Phase 3 vector-index migration
        # to size the Neo4j HNSW index. Unknown models fall back to a
        # one-shot probe via ``embed_query`` on first use; we keep the
        # known-models map as the cheap path.
        self._dim_known: int | None = VOYAGE_MODEL_DIMS.get(model)

    @property
    def dim(self) -> int:
        if self._dim_known is not None:
            return self._dim_known
        # Probe once with a minimal query and cache the result. This
        # path runs only for unrecognised model ids; the default
        # ``voyage-3`` resolves from the static map without a network
        # call.
        probe = self._call_embed(["dim_probe"], input_type="document")
        self._dim_known = len(probe[0])
        return self._dim_known

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            out.extend(self._call_embed(batch, input_type="document"))
        return out

    def embed_query(self, query: str) -> list[float]:
        result = self._call_embed([query], input_type="query")
        return result[0]

    def _call_embed(
        self,
        texts: list[str],
        *,
        input_type: str,
    ) -> list[list[float]]:
        """One Voyage API call. Isolates the SDK shape for testability."""
        response = self._client.embed(
            texts=texts,
            model=self._model,
            input_type=input_type,
        )
        # The Voyage SDK returns an object with ``.embeddings`` — a
        # list[list[float]] in the same order as the input texts.
        embeddings = getattr(response, "embeddings", None)
        if embeddings is None or not isinstance(embeddings, list):
            raise RuntimeError(
                "Voyage response did not include an `embeddings` list. "
                "SDK shape may have changed; check `voyageai` version."
            )
        return [list(vec) for vec in embeddings]


# ─── In-process fake (used by all default unit tests) ────────────────────


class FakeEmbeddingClient:
    """Deterministic in-process :class:`EmbeddingClient` for unit tests.

    Vectors are derived from a SHA-256 hash of each input string,
    truncated/padded to :attr:`dim` floats in ``[-1.0, 1.0]``. The same
    text always returns the same vector, which is enough to drive
    retrieval-order assertions without a real provider.

    Asymmetric encoding is mimicked by salting document vs query
    digests with a constant prefix, so a query ``"x"`` and a document
    ``"x"`` deliberately differ. Tests that want symmetric behaviour
    can pass ``asymmetric=False``.
    """

    name: str = "fake-embedding"

    def __init__(
        self,
        *,
        dim: int = 16,
        asymmetric: bool = True,
    ) -> None:
        if dim <= 0:
            raise ValueError("FakeEmbeddingClient `dim` must be positive.")
        self.dim = dim
        self._asymmetric = asymmetric
        self.calls: list[dict[str, Any]] = []

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append({"method": "embed_documents", "texts": list(texts)})
        return [self._vector_for(t, salt="doc:" if self._asymmetric else "") for t in texts]

    def embed_query(self, query: str) -> list[float]:
        self.calls.append({"method": "embed_query", "query": query})
        return self._vector_for(query, salt="qry:" if self._asymmetric else "")

    def _vector_for(self, text: str, *, salt: str) -> list[float]:
        # Deterministic per (salt, text). SHA-256 → bytes → floats in
        # [-1, 1]. Looped if `dim` exceeds 32 floats per digest.
        out: list[float] = []
        counter = 0
        while len(out) < self.dim:
            digest = hashlib.sha256(f"{salt}{counter}:{text}".encode()).digest()
            for i in range(0, len(digest), 2):
                if len(out) >= self.dim:
                    break
                # Two bytes → unsigned 16-bit → mapped to [-1, 1].
                hi, lo = digest[i], digest[i + 1] if i + 1 < len(digest) else 0
                u = (hi << 8) | lo
                out.append((u / 65535.0) * 2.0 - 1.0)
            counter += 1
        # L2-normalise so cosine similarity in tests behaves.
        norm = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm for x in out]


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_VOYAGE_MODEL",
    "VOYAGE_MODEL_DIMS",
    "EmbeddingClient",
    "FakeEmbeddingClient",
    "VoyageEmbeddingClient",
]
