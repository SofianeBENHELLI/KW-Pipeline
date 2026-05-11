"""Pydantic schemas for the atomic Claim/Fact data model (#368, ADR-031).

A :class:`Claim` is a subject–predicate–object atom extracted from a
validated document version. Together with its provenance (the chunk
ids it was sourced from), it lets future consumers detect
contradictions across documents, surface knowledge gaps ("we have no
claim about X"), and diff two versions semantically.

Per ADR-031 the persistence boundary is SQLite — claims are
governance / audit data, not graph traversal data. The wire shape
here is what both the in-memory test fake and the SQLite store
round-trip; the store layer never invents fields the wire model
doesn't carry.

Field invariants that are enforced here (not the DB schema):

* ``object_value`` XOR ``object_entity_id``. Exactly one of the two
  is set per row — a claim is either "X has property Y" (literal
  object) or "X is_related_to Y" (entity object). Both set is
  ambiguous; neither set is meaningless.
* ``schema_version`` is a frozen ``Literal["v0.1"]``. Future
  evolution lands a new literal value and the store / extractor
  paths update in lock-step; the wire is gated so v0.1 readers
  never silently parse a v0.2 payload.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas import APISchemaModel as BaseModel

# Bumped when the wire shape of a Claim changes. The SQLite store
# records this per-row so a future v0.2 extractor can co-exist with
# v0.1 rows during a gradual re-extraction.
ClaimSchemaVersion = Literal["v0.1"]
CLAIM_SCHEMA_VERSION: ClaimSchemaVersion = "v0.1"


class Claim(BaseModel):
    """One atomic assertion extracted from a validated document.

    ``subject_entity_id`` is a soft reference to the entity-id
    convention emitted by
    :func:`app.services.knowledge.entity_extractor` — a deterministic
    ``entity-<sha256[:16]>`` hash. There is no centralised entities
    table today, so the field is a free-text string and the store
    layer adds no FK; a future "entities" table can introduce one
    without changing the wire.

    ``predicate`` is a free-text string (e.g. ``is_a``,
    ``has_property``, ``located_in``) — keeping it open lets the v0.1
    extractor emit any verb the LLM produces. A future controlled
    vocabulary lives at the schema layer (a ``Literal[...]`` swap)
    when the consumer set stabilises.

    ``object_value`` and ``object_entity_id`` are mutually exclusive.
    A literal object ("ISO 9001 was published in 2015") sets
    ``object_value="2015"``; an entity object ("Acme acquired
    Beta") sets ``object_entity_id="entity-<hash>"``. The
    ``model_validator`` below enforces XOR.

    ``confidence`` is a value in [0, 1] reflecting the extractor's
    certainty in the triple — the same band as the
    :class:`EntityExtractor` confidence field so consumers can apply
    a single threshold across both surfaces.

    ``provenance_chunk_ids`` is the list of section / chunk ids the
    triple was sourced from. The list is always non-empty; a claim
    with no provenance is unverifiable and the extractor must skip
    it. Stored on disk as a JSON-encoded array per ADR-031 (SQLite
    is the truth; the JSON column avoids a N:M join table for the
    v1 read API).

    ``extracted_at`` is set server-side by the store on save — the
    extractor hands the claim in without it (the operator workflow
    has no notion of "when did the LLM run"). The store's
    ``save_claims`` populates it before INSERT.
    """

    id: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1, max_length=200)
    version_id: str = Field(min_length=1, max_length=200)
    subject_entity_id: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=200)
    object_value: str | None = Field(default=None, max_length=2000)
    object_entity_id: str | None = Field(default=None, max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)
    schema_version: ClaimSchemaVersion = CLAIM_SCHEMA_VERSION
    extracted_at: datetime
    provenance_chunk_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_object_xor(self) -> Claim:
        has_value = self.object_value is not None
        has_entity = self.object_entity_id is not None
        if has_value and has_entity:
            raise ValueError(
                "Claim object is ambiguous: set exactly one of "
                "object_value or object_entity_id, not both."
            )
        if not has_value and not has_entity:
            raise ValueError(
                "Claim object is missing: set exactly one of object_value or object_entity_id."
            )
        return self


class ClaimsListResponse(BaseModel):
    """Response envelope for ``GET /knowledge/claims``.

    ``next_cursor`` follows the same opaque-cursor pattern as the
    rest of the catalog read paths — the codec lives in
    :mod:`app.services.catalog_store` and clients must treat the
    string as opaque.
    """

    schema_version: ClaimSchemaVersion = CLAIM_SCHEMA_VERSION
    items: list[Claim] = Field(default_factory=list)
    next_cursor: str | None = None


__all__ = [
    "CLAIM_SCHEMA_VERSION",
    "Claim",
    "ClaimSchemaVersion",
    "ClaimsListResponse",
]
