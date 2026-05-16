"""Per-chunk deterministic taxonomy extractor (EPIC-1 slice 1.1, issue #338).

Layer-1 of the hybrid taxonomy model (ADR-017): content-derived
concepts only, no LLM, no business-taxonomy alignment. Produces the
input that slice 1.3 (LLM business-taxonomy allocation), slice 1.4
(gap analysis), slice 1.5 (corpus emerging taxonomy), and slice 1.13
(chunk-inspector UI) all consume verbatim.

Five extractors run per chunk:

1. **keywords** — reuses ``chunk_relations._top_keywords`` so the
   keyword vocabulary in this layer matches the relation /
   clustering signals already on the chunk.
2. **noun phrases** — capitalized-word sequences (Title-Case spans of
   length 2 to N). Quick proxy for proper-noun-ish multi-word terms
   like ``Battery Thermal Management`` or ``Engineering Change
   Request``. Single-word capitalized tokens are skipped on purpose
   (those overlap with keywords and would double-count).
3. **acronyms** — 2-5 uppercase letter sequences. The MVP rule
   accepts plain ``API`` / ``MCP`` / ``ISO`` shapes; mixed-case
   variants and dotted forms (``A.P.I.``) are out of scope.
4. **standards** — reuses ``chunk_relations._extract_standards`` so
   ``ISO 9001`` / ``IEC 62443`` / etc. surface as deterministic
   concepts with their canonical ``body-number`` form.
5. **heading anchor** — the section heading itself, when non-empty,
   tagged ``heading_anchor`` so the UI can render it distinctly
   from the body-derived concepts.

NER (named entity recognition) is intentionally absent from the
core extractor today. If the spaCy enricher is wired
(:mod:`app.services.enrichers.spacy_ner`, opt-in via
``KW_NER_ENABLED=true`` per #190), the entities it added to the
section's metadata are appended as ``ner_candidate`` concepts; if
not, the field is simply empty. The deterministic taxonomy contract
is the same either way — operators who turn on spaCy get richer
output, the rest get the regex-derived signals.

The output is deduplicated by ``(kind, text.lower())`` so a token
that appears as both ``keyword`` and ``noun_phrase`` doesn't
double-count in downstream weighting.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.schemas.deterministic_taxonomy import (
    DeterministicTaxonomyConcept,
    DeterministicTaxonomyForChunk,
)
from app.schemas.extraction import RawSection
from app.schemas.semantic_document import SemanticSection
from app.services.knowledge.chunk_relations import (
    ChunkRecord,
    _extract_standards,
    _format_standard,
    _tokenize,
    _top_keywords,
)

# Noun-phrase pattern — runs of 2+ capitalized words separated by a
# single space. Each word starts uppercase, the remainder is
# lowercase (``Battery Thermal``) or all-uppercase short
# (acronym-style runs like ``ISO 9001`` are caught by the standards
# extractor, not here). ``[A-Z][a-z]+`` deliberately excludes
# single-word title-cased tokens — those would otherwise dominate
# the noun-phrase list with noise from sentence-initial words.
_NOUN_PHRASE_PATTERN = re.compile(r"\b(?:[A-Z][a-z]+)(?:\s+[A-Z][a-z]+){1,4}\b")

# Acronym pattern — 2 to 5 uppercase letters, word-bounded. Excludes
# sentence-initial single-word matches like "A" or single-letter
# names. Numbers inside the run (``S3`` / ``EC2``) are out of scope
# for the MVP — they'd need a stricter rule to avoid catching
# pagination markers.
_ACRONYM_PATTERN = re.compile(r"\b[A-Z]{2,5}\b")

# Keyword default — same as the chunk-relations service uses for
# its relation-overlap signal. Keeping the two in sync means the
# clustering / relations / taxonomy layers all reason over the
# same top-N vocabulary.
_DEFAULT_KEYWORD_LIMIT = 12

# Confidence floors per kind. These are operator-tunable in a
# future revision; for now they're constants so the deterministic
# layer's output is reproducible across runs without configuration.
_CONFIDENCE_KEYWORD = 0.85
_CONFIDENCE_NOUN_PHRASE = 0.70
_CONFIDENCE_ACRONYM = 0.75
_CONFIDENCE_STANDARD = 0.95
_CONFIDENCE_HEADING_ANCHOR = 1.0
_CONFIDENCE_NER_CANDIDATE = 0.80


# ─── Public surface ────────────────────────────────────────────────────


def extract_deterministic_taxonomy(
    record: ChunkRecord,
    *,
    section: SemanticSection | RawSection | None = None,
    ner_entities: Iterable[str] | None = None,
    keyword_limit: int = _DEFAULT_KEYWORD_LIMIT,
) -> DeterministicTaxonomyForChunk:
    """Project one :class:`ChunkRecord` to its deterministic taxonomy.

    ``record`` carries the already-tokenized + keyword-extracted form
    from :class:`ChunkRelationService.chunks_for`. We re-tokenize the
    raw text to get the full token stream (needed for the
    keyword-limit override), then run the four pattern extractors
    over the original text (preserves the capitalisation that noun
    phrases and acronyms rely on).

    ``section`` is optional — when provided, the section's
    ``parser_metadata`` may carry a ``spacy_ner_entities`` list from
    the optional spaCy enricher (#190). When present, those entities
    are appended as ``ner_candidate`` concepts. The deterministic
    layer's output is identical regardless of whether NER ran.

    ``ner_entities`` is an explicit override — useful for tests and
    for callers that source entities from somewhere other than the
    parser metadata. Takes precedence over the section payload.

    Returns the typed projection; the caller persists it or fans it
    out to slice 1.3 / 1.4 / 1.5 consumers as appropriate.
    """
    text = record.text
    tokens = _tokenize(text)
    keywords = _top_keywords(tokens, limit=keyword_limit)

    concepts: list[DeterministicTaxonomyConcept] = []

    # Heading anchor — first so it tops the concept list when the
    # downstream consumer renders by order.
    heading = (record.heading or "").strip()
    if heading and heading.lower() != "extracted text":
        # ``"Extracted Text"`` is the default fallback the parser
        # uses when no real heading was found; it's noise as a
        # taxonomy anchor.
        concepts.append(
            DeterministicTaxonomyConcept(
                kind="heading_anchor",
                text=heading,
                confidence=_CONFIDENCE_HEADING_ANCHOR,
            )
        )

    for keyword in keywords:
        concepts.append(
            DeterministicTaxonomyConcept(
                kind="keyword",
                text=keyword,
                confidence=_CONFIDENCE_KEYWORD,
            )
        )

    for match in _NOUN_PHRASE_PATTERN.findall(text):
        concepts.append(
            DeterministicTaxonomyConcept(
                kind="noun_phrase",
                text=match,
                confidence=_CONFIDENCE_NOUN_PHRASE,
            )
        )

    for match in _ACRONYM_PATTERN.findall(text):
        concepts.append(
            DeterministicTaxonomyConcept(
                kind="acronym",
                text=match,
                confidence=_CONFIDENCE_ACRONYM,
            )
        )

    for standard in _extract_standards(text):
        concepts.append(
            DeterministicTaxonomyConcept(
                kind="standard",
                text=_format_standard(standard),
                confidence=_CONFIDENCE_STANDARD,
            )
        )

    # NER from explicit param OR section metadata. Explicit wins so
    # tests can override without forging a SemanticSection.
    ner_source = list(ner_entities) if ner_entities is not None else _ner_from_section(section)
    for entity in ner_source:
        concepts.append(
            DeterministicTaxonomyConcept(
                kind="ner_candidate",
                text=entity,
                confidence=_CONFIDENCE_NER_CANDIDATE,
            )
        )

    return DeterministicTaxonomyForChunk(
        chunk_id=record.chunk_id,
        section_id=record.section_id,
        heading=record.heading,
        concepts=_dedupe_concepts(concepts),
    )


# ─── Helpers ───────────────────────────────────────────────────────────


def _ner_from_section(section: object | None) -> list[str]:
    """Pull a ``list[str]`` of NER entities off a section's parser
    metadata, when the spaCy enricher (#190) populated it.

    The enricher writes a JSON-encoded list to
    ``section.parser_metadata["spacy_ner_entities"]``. The shape lives
    on :class:`RawSection` (parser output) — not on
    :class:`SemanticSection` (the published wire shape). Callers that
    have a ``RawSection`` in hand can pass it here; callers further
    down the pipeline (where the section has been projected to
    :class:`SemanticSection` and ``parser_metadata`` has been dropped)
    should use the explicit ``ner_entities`` parameter on
    :func:`extract_deterministic_taxonomy` instead.

    Implementation reads ``parser_metadata`` via ``getattr`` so a
    :class:`SemanticSection` (which lacks the field) returns an
    empty list cleanly. Fail-soft on malformed JSON.
    """
    if section is None:
        return []
    parser_metadata = getattr(section, "parser_metadata", None)
    if not parser_metadata:
        return []
    raw: Any = parser_metadata.get("spacy_ner_entities")
    if not raw:
        return []
    try:
        import json

        decoded = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded if isinstance(item, str) and item.strip()]


def _dedupe_concepts(
    concepts: Iterable[DeterministicTaxonomyConcept],
) -> list[DeterministicTaxonomyConcept]:
    """Deduplicate by ``(kind, text.lower())`` while preserving order.

    Concepts that compare equal keep the one with the **higher**
    confidence — so a noun phrase that's also been emitted by the
    NER extractor (e.g. ``Battery Thermal``) keeps its more reliable
    ``ner_candidate`` confidence instead of the heuristic fallback.
    """
    keyed: dict[tuple[str, str], DeterministicTaxonomyConcept] = {}
    for concept in concepts:
        key = (concept.kind, concept.text.lower())
        existing = keyed.get(key)
        if existing is None or concept.confidence > existing.confidence:
            keyed[key] = concept
    return list(keyed.values())


__all__ = [
    "extract_deterministic_taxonomy",
]
