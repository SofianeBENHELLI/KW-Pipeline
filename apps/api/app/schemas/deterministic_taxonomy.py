"""Layer-1 deterministic taxonomy types (EPIC-1 slice 1.1, issue #338).

The deterministic taxonomy is the **content-derived** half of the
hybrid taxonomy model (ADR-017). Per chunk, the system extracts:

- keywords (already in :class:`ChunkRecord`)
- noun phrases (capitalized word sequences)
- acronyms (2-5 uppercase letter sequences)
- standard references (ISO / IEC / ASTM / … — already in
  :class:`ChunkRecord`)
- heading anchor (the section heading itself)
- optional NER candidates (when the spaCy enricher is wired)

This module owns the **wire shape**. The service layer in
:mod:`app.services.knowledge.deterministic_taxonomy` runs the
extraction; downstream consumers (slice 1.3 LLM allocation, slice
1.4 gap analysis, slice 1.5 emerging-corpus aggregator, slice 1.13
chunk-inspector UI) read these types verbatim.

Schema-version policy mirrors ADR-008: every persisted record
carries an explicit ``schema_version`` ``Literal`` so the loader
can fan out on shape mismatches. ``v0.1`` is the first cut shipped
with this slice.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel

DETERMINISTIC_TAXONOMY_SCHEMA_VERSION: Final[Literal["v0.1"]] = "v0.1"


class DeterministicTaxonomyConcept(BaseModel):
    """One concept extracted deterministically from a chunk.

    The ``kind`` discriminator lets downstream code group concepts
    by extraction method without keeping parallel lists per kind:
    UI filters / gap analysis / LLM-prompt assembly can all walk a
    single ``concepts`` list and switch on ``kind`` when they need
    to weight (e.g. acronyms vs noun phrases) differently.

    ``confidence`` is a heuristic in ``[0, 1]``; the deterministic
    layer can't know whether a candidate is *actually* a useful
    domain term, only how strong the structural signal is (token
    frequency for keywords, capitalisation + length for acronyms,
    pattern strictness for standards, etc.). 1.0 means "the
    extractor's strongest signal"; lower values are operator-tunable
    floors per kind.
    """

    kind: Literal[
        "keyword",
        "noun_phrase",
        "acronym",
        "standard",
        "heading_anchor",
        "ner_candidate",
    ]
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    evidence: str | None = Field(
        default=None,
        description=(
            "Optional short snippet from the chunk that triggered "
            "the concept. The UI's chunk-inspector (slice 1.13) "
            "renders this so operators can see WHY the extractor "
            "fired without re-reading the entire chunk."
        ),
    )


class DeterministicTaxonomyForChunk(BaseModel):
    """Per-chunk deterministic taxonomy projection.

    Carries the chunk identifiers + the flat list of concepts. The
    same chunk runs through every extractor in turn; the output is
    deduplicated by ``(kind, text.lower())`` so a token that appears
    as both a keyword and a noun phrase isn't double-counted in
    downstream weighting.
    """

    schema_version: Literal["v0.1"] = DETERMINISTIC_TAXONOMY_SCHEMA_VERSION
    chunk_id: str
    section_id: str
    heading: str
    concepts: list[DeterministicTaxonomyConcept] = Field(default_factory=list)


__all__ = [
    "DETERMINISTIC_TAXONOMY_SCHEMA_VERSION",
    "DeterministicTaxonomyConcept",
    "DeterministicTaxonomyForChunk",
]
