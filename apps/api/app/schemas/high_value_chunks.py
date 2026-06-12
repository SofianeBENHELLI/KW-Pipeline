"""Response schemas for ``GET /documents/{document_id}/high-value-chunks``
(converged plan §C.2).

The high-value-chunks surface is the operator's "start here" entry
into a long document. It ranks the chunks of a validated semantic
document by a weighted-sum importance score, returning the top-K so
a reviewer can jump straight to the dense ones instead of paging
through 800 sections.

The score and its components are surfaced on the wire so the UI can
explain *why* a chunk ranks high — important for the demo story and
for letting an operator distinguish "claims-rich" chunks from
"highly-connected" chunks. The four components below are normalized
to ``[0, 1]`` against the document's own per-component max so the
score is comparable across documents of different sizes.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel

# Bumped when the wire shape changes. v0.1 ships with the converged
# plan §C.2 ranker; a v0.2 might add a weighting envelope or a
# breakdown across additional signals (entity types, taxonomy
# allocation depth, …).
HighValueChunksSchemaVersion = Literal["v0.1"]
HIGH_VALUE_CHUNKS_SCHEMA_VERSION: Final[HighValueChunksSchemaVersion] = "v0.1"


class HighValueChunkSignals(BaseModel):
    """Per-component normalised contribution to the composite score.

    Every field lives in ``[0, 1]``: it's the raw count for this
    chunk divided by the document's per-component max. A score of
    ``1.0`` means "the densest chunk in this document on this
    signal". The composite ``score`` on the parent row is a
    weighted sum; the weights are exposed on the response so
    operators can inspect the formula.
    """

    claims: float = Field(ge=0.0, le=1.0)
    process_steps: float = Field(ge=0.0, le=1.0)
    graph_degree: float = Field(ge=0.0, le=1.0)
    entity_density: float = Field(ge=0.0, le=1.0)


class HighValueChunk(BaseModel):
    """One ranked chunk row.

    ``score`` is the composite importance, ``signals`` carries the
    per-component contributions for explainability, and the raw
    counts (``claim_count`` / ``process_step_count`` / …) are
    surfaced so the UI doesn't have to multiply back the
    normalisation.

    ``heading`` and ``snippet`` come from the semantic document
    section the chunk maps to (per ADR-031 chunks today are 1:1
    with sections); the snippet is a deterministic prefix capped at
    240 chars so an operator can recognise the content without
    needing to fetch the full section.
    """

    chunk_id: str = Field(min_length=1, max_length=200)
    section_id: str = Field(min_length=1, max_length=200)
    heading: str = Field(min_length=0, max_length=500)
    snippet: str = Field(min_length=0, max_length=240)
    char_count: int = Field(ge=0)
    score: float = Field(ge=0.0, le=1.0)
    signals: HighValueChunkSignals
    claim_count: int = Field(ge=0)
    process_step_count: int = Field(ge=0)
    graph_degree: int = Field(ge=0)
    entity_mention_count: int = Field(ge=0)


class HighValueChunksResponse(BaseModel):
    """Response envelope for ``GET /documents/{id}/high-value-chunks``.

    ``items`` is sorted by ``score`` DESC then ``chunk_id`` ASC so
    ties tie-break deterministically. The list is truncated to
    ``limit`` rows; the route enforces the cap via Query validation
    so the wire stays bounded.

    ``weights`` is the formula the ranker used — surfacing it on
    the wire means an operator can inspect and (in a future
    iteration) override the weighting without re-deploying.
    """

    schema_version: HighValueChunksSchemaVersion = HIGH_VALUE_CHUNKS_SCHEMA_VERSION
    document_id: str = Field(min_length=1, max_length=200)
    version_id: str = Field(min_length=1, max_length=200)
    version_number: int = Field(ge=1)
    total_chunks: int = Field(
        ge=0,
        description="Number of chunks in the version (the pool the ranker scored).",
    )
    weights: HighValueChunkSignals = Field(
        description=(
            "Weights applied to the normalised per-component signals "
            "when computing the composite score. Exposed on the wire "
            "for transparency."
        ),
    )
    items: list[HighValueChunk] = Field(default_factory=list)


__all__ = [
    "HIGH_VALUE_CHUNKS_SCHEMA_VERSION",
    "HighValueChunk",
    "HighValueChunkSignals",
    "HighValueChunksResponse",
    "HighValueChunksSchemaVersion",
]
