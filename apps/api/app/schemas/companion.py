"""Wire-shape models for the AURA companion layer (#373 EPIC).

This module ships the **citation contract** ahead of the companion
implementation (#370). Every grounded response the companion produces
carries this exact shape so downstream consumers — the web app, the
widget, future 3DEXPERIENCE embeds — can encode against a stable
contract from day 1.

Locking the shape now (with no route yet) means the schema lands in
the OpenAPI snapshot only when the first companion route ships, but
the Pydantic models are importable across services right away. Future
back-compat policy (ADR-029):

* **Additive fields are non-breaking.** New optional fields are fine.
* **Renames or removals require bumping** ``GroundedAnswer.schema_version``
  and keeping the old shape behind a feature flag for one release.
* The trust fields (``validation_status``, ``is_source_backed``)
  intentionally mirror the names already used by
  ``ExploreSearchChunk`` / ``ExploreSearchDocument`` so the explorer's
  trust-label rendering can be reused without translation.

Implementation of ``POST /companion/answer`` lands in a follow-up
issue under EPIC #373; this module is contract-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel

CitationSchemaVersion = Literal["v0.1"]


class CitationSpan(BaseModel):
    """Character-offset span inside a chunk's source text.

    Optional on every citation: paragraph- or sentence-grain
    citations may omit ``span`` when the citing answer paraphrases
    rather than quotes. When present, the ``[start_char, end_char)``
    half-open interval lets frontends highlight the exact source
    range without re-fetching the chunk body.
    """

    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)


class Citation(BaseModel):
    """One source attribution carried alongside a grounded answer.

    The fields fall into three groups:

    * **Identity** — ``chunk_id`` / ``document_id`` / ``version_id``
      pin the citation to a specific point in the catalog. The
      version_id is required (not just ``document_id``) so a
      citation never silently follows a document forward to a newer
      version with different content.
    * **Trust** — ``validation_status`` and ``is_source_backed``
      mirror the explorer's search-result trust signals so frontends
      can reuse their existing label-rendering logic.
    * **Surface** — ``span`` and ``snippet`` give the UI everything
      it needs to render an in-line citation without an additional
      API round-trip.

    ``confidence`` is the companion's own confidence in the citation
    (how strongly the cited chunk supports the surrounding answer
    text), distinct from the chunk's own extraction confidence.
    """

    chunk_id: str
    document_id: str
    version_id: str
    span: CitationSpan | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    validation_status: str | None = None
    is_source_backed: bool = False
    source_url: str | None = None
    snippet: str


class TrustSummary(BaseModel):
    """Aggregate trust signal for a grounded answer.

    Computed from the citations the answer carries — surfaced as a
    single chip in the companion UI so users see the answer's trust
    posture at a glance without scanning every citation.
    """

    citation_count: int = Field(ge=0)
    validated_citation_count: int = Field(ge=0)
    source_backed_citation_count: int = Field(ge=0)
    candidate_citation_count: int = Field(ge=0)
    """Citations whose source chunk is neither validated nor source-backed."""
    trust_gate_filtered_count: int = Field(ge=0)
    """Number of candidate citations dropped by the default-deny trust
    gate (#372). Zero when the gate is widened or no candidates would
    have qualified."""


class GroundedAnswer(BaseModel):
    """The companion's response envelope for ``POST /companion/answer``.

    The route itself is not yet implemented (EPIC #373 / future PR);
    this is the contract every grounded response will conform to.

    ``answer_id`` is the addressable handle for the per-citation
    feedback bridge (#371): a downstream
    ``POST /companion/feedback {answer_id, citation_index, ...}``
    targets a specific past response without the consumer having to
    cache the full body.

    ``schema_version`` is the back-compat anchor — bump it when a
    breaking change forces the wire shape to evolve.
    """

    schema_version: CitationSchemaVersion = "v0.1"
    answer_id: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    trust_summary: TrustSummary
    generated_at: datetime
    model: str
    """Identifier of the LLM that generated the answer (e.g. ``claude-sonnet-4-5``)."""


__all__ = [
    "Citation",
    "CitationSchemaVersion",
    "CitationSpan",
    "GroundedAnswer",
    "TrustSummary",
]
