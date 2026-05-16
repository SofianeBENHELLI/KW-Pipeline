"""Pydantic schemas for LLM business-taxonomy chunk allocations
(EPIC-1 slice 1.3, issue #340).

Layer-2 of the hybrid taxonomy model (ADR-017): an LLM aligns each
chunk's deterministic concepts (slice 1.1 output) against the
operator-imposed business taxonomy (slice 1.2 YAML / SQLite store)
and emits one :class:`ChunkTaxonomyAllocation` per chunk with the
matched category ids, per-assignment confidence, and a rationale
short enough to render inline in the chunk inspector.

Persistence boundary is SQLite (governance / audit data, not graph
traversal) — same posture as :mod:`app.schemas.document_topic` and
:mod:`app.schemas.claim`. A future v0.2 lands a new ``schema_version``
literal so v0.1 readers can refuse mixed-version rows at the
boundary instead of silently flowing them through.

Version pinning (per slice 1.3 acceptance criteria):

* ``taxonomy_fingerprint`` — SHA-256 of the canonical JSON of the
  active :class:`~app.schemas.taxonomy.Taxonomy` at the time of the
  allocation pass. Two allocations with the same fingerprint were
  produced against the same category tree, regardless of which
  taxonomy_id / version_number combo was in effect (which the
  current ``TaxonomyStore`` does not yet expose on its read path).
* ``model_id`` — the LLM model the allocator called (``claude-…``
  or ``gemini-…``); the same value the structured-log telemetry
  surfaces.
* ``prompt_hash`` — SHA-256 of the full prompt body (system + user)
  truncated to 16 hex chars for readability. Allocations with
  matching ``(model_id, prompt_hash)`` were produced from identical
  prompts; an operator inspecting drift between two passes can
  diff by this pair.

Provenance constraint mirrors the rest of the LLM extractors: an
allocation with no assignments is unverifiable and the store must
not surface it. We still persist the empty case at the
``ChunkTaxonomyAllocation`` row level (so "we asked the LLM and
nothing matched" is recoverable from the audit log) but the
``assignments`` list is allowed to be empty — that's a meaningful
operator-facing signal, not a default-deny rejection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel

# Bumped when the wire shape of an allocation changes. The SQLite
# store records this per-row so a future v0.2 allocator can co-exist
# with v0.1 rows during a gradual re-allocation pass.
ChunkTaxonomyAllocationSchemaVersion = Literal["v0.1"]
CHUNK_TAXONOMY_ALLOCATION_SCHEMA_VERSION: Final[ChunkTaxonomyAllocationSchemaVersion] = "v0.1"


class BusinessCategoryAssignment(BaseModel):
    """One ``chunk → category`` assignment.

    ``category_id`` references a node in the operator-imposed
    :class:`~app.schemas.taxonomy.Taxonomy`. The allocator filters
    hallucinated ids (any value not in the published tree) before the
    assignment reaches the store, so consumers can assume every id
    here resolves at the time of allocation. A subsequent taxonomy
    edit that removes a category leaves stale allocations in place
    (the fingerprint flags them as ``taxonomy_drifted``); the
    reconciler can re-allocate or archive them.

    ``confidence`` lives in ``[0, 1]`` so the chunk inspector can
    apply one threshold across allocations, claims, and topics. The
    LLM is prompted to score conservatively: > 0.8 for "clearly
    applies", 0.5–0.8 for "partial match", < 0.5 indicates the LLM
    is hedging and the UI may surface it under a "weak match" banner.

    ``rationale`` is a one-sentence justification the LLM emits for
    each assignment, capped to 500 chars so the chunk inspector can
    render the full text without scrolling. The supporting
    deterministic-concept texts are listed separately so the
    inspector can highlight the source spans in the chunk body.
    """

    category_id: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=500)
    supporting_concept_texts: list[str] = Field(default_factory=list)


class ChunkTaxonomyAllocation(BaseModel):
    """One LLM allocation pass over a single chunk.

    Rows are addressed by ``id`` (deterministic: ``alloc-<version
    _id>-<chunk_id>``) so a re-allocation idempotently replaces the
    prior row. ``assignments`` may be empty when the LLM found no
    matching category — we still persist the empty case so the audit
    trail records "the allocator ran and chose to abstain" rather
    than collapsing it with "the allocator never ran".

    ``extracted_at`` is set server-side by the store on save — the
    allocator hands the allocation in with a sentinel timestamp and
    the store stamps it with ``datetime.now(UTC)`` before INSERT.
    """

    id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    section_id: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1, max_length=200)
    version_id: str = Field(min_length=1, max_length=200)
    assignments: list[BusinessCategoryAssignment] = Field(default_factory=list)
    taxonomy_fingerprint: str = Field(min_length=1, max_length=200)
    model_id: str = Field(min_length=1, max_length=200)
    prompt_hash: str = Field(min_length=1, max_length=200)
    schema_version: ChunkTaxonomyAllocationSchemaVersion = CHUNK_TAXONOMY_ALLOCATION_SCHEMA_VERSION
    extracted_at: datetime


class ChunkTaxonomyAllocationsListResponse(BaseModel):
    """Response envelope for ``GET /knowledge/taxonomy-allocations``.

    ``next_cursor`` follows the same opaque-cursor pattern as the
    rest of the catalog read paths — the codec lives in
    :mod:`app.services.catalog_store` and clients must treat the
    string as opaque.
    """

    schema_version: ChunkTaxonomyAllocationSchemaVersion = CHUNK_TAXONOMY_ALLOCATION_SCHEMA_VERSION
    items: list[ChunkTaxonomyAllocation] = Field(default_factory=list)
    next_cursor: str | None = None


__all__ = [
    "CHUNK_TAXONOMY_ALLOCATION_SCHEMA_VERSION",
    "BusinessCategoryAssignment",
    "ChunkTaxonomyAllocation",
    "ChunkTaxonomyAllocationSchemaVersion",
    "ChunkTaxonomyAllocationsListResponse",
]
