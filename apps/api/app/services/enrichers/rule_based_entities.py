"""Rule-based ``SemanticEnricher`` that extracts typed entities (#48).

Scope per the issue: deterministic-first, no LLM, no NER model. Three
entity types covered here — ``date``, ``monetary_amount``, and
``requirement_phrase``. Person and organization NER is deferred to a
follow-up that introduces an opt-in ``spaCy`` integration; the
infrastructure for that lives behind the same ``SemanticEnricher``
Protocol so it lands as an additional enricher in the chain rather
than a rewrite.

Design notes

* Every emitted asset carries its source section's
  ``source_reference_ids`` so reviewers can navigate back to the
  exact section the entity came from. Empty reference lists are
  preserved on assets that came from sections without lineage —
  ``SemanticExtractor`` boundary still forces ``review_status =
  "needs_review"`` so the schema validator's ``source_backed``
  rule never fires on rule-based output.
* Confidence scores follow the issue's recommendation: 0.9 for
  high-precision regex (specific date / currency patterns), 0.5
  for the looser modal-verb requirement heuristic.
* Patterns are case-insensitive where it doesn't widen false
  positives. Matches are deduplicated within a section so the
  same date written twice doesn't emit two identical assets.
"""

from __future__ import annotations

import re

from app.schemas.extraction import RawExtraction
from app.schemas.semantic_document import SemanticAsset

# ─── Date patterns ─────────────────────────────────────────────────────
# Anchored to word boundaries so embedded numbers don't match. Covers:
#  - ISO 8601: 2026-05-03
#  - US numeric: 5/3/2026, 5/3/26
#  - Long-form English: "May 3, 2026", "3 May 2026", "3 May, 2026"
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ISO 8601 (YYYY-MM-DD)
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    # Numeric M/D/YYYY or D/M/YYYY (and 2-digit years)
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    # "May 3, 2026" / "May 3 2026"
    re.compile(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+\d{1,2},?\s+\d{4}\b",
        re.IGNORECASE,
    ),
    # "3 May 2026" / "3 May, 2026"
    re.compile(
        r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r",?\s+\d{4}\b",
        re.IGNORECASE,
    ),
)

# ─── Monetary amount pattern ───────────────────────────────────────────
# Matches a currency marker (symbol or 3-letter code) followed by an
# optional space and a numeric value with thousands separators and an
# optional decimal part. Examples: "€42,000", "USD 1,234.56", "$10",
# "£3.50", "JPY 100000".
_MONETARY_PATTERN = re.compile(
    r"(?:[€$£¥]\s*|\b(?:USD|EUR|GBP|JPY|CHF|CAD|AUD)\s+)"
    r"\d{1,3}(?:[,. ]\d{3})*(?:\.\d+)?",
    re.IGNORECASE,
)

# ─── Requirement phrase pattern ────────────────────────────────────────
# Captures a clause containing a compliance modal verb. Stops at the
# next sentence boundary or newline so we don't run on indefinitely.
# Lower confidence (0.5) because the heuristic over-matches in
# narrative prose.
_REQUIREMENT_PATTERN = re.compile(
    r"[^.;\n]*\b(?:must|shall|required to|requires?|mandatory|prohibited)\b[^.;\n]*[.;\n]",
    re.IGNORECASE,
)


class RuleBasedEntityEnricher:
    """Deterministic ``SemanticEnricher`` for date / monetary / requirement.

    Conforms to the :class:`SemanticEnricher` Protocol. Stateless — a
    single instance is reused across calls; safe to share between threads
    because :mod:`re` patterns are thread-safe and no instance state is
    mutated.
    """

    name: str = "rule_based_entities"

    def enrich(
        self,
        raw_extraction: RawExtraction,
        existing_assets: list[SemanticAsset],  # noqa: ARG002 - protocol shape
    ) -> list[SemanticAsset]:
        out: list[SemanticAsset] = []
        for section in raw_extraction.sections:
            text = section.text
            if not text:
                continue
            ref_ids = list(section.source_reference_ids)
            out.extend(_extract_dates(text, ref_ids))
            out.extend(_extract_monetary(text, ref_ids))
            out.extend(_extract_requirements(text, ref_ids))
        return out


# ─── Per-type helpers ─────────────────────────────────────────────────


def _extract_dates(text: str, ref_ids: list[str]) -> list[SemanticAsset]:
    seen: set[str] = set()
    out: list[SemanticAsset] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0).strip()
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(
                SemanticAsset(
                    type="date",
                    text=value,
                    confidence=0.9,
                    review_status="needs_review",
                    source_reference_ids=list(ref_ids),
                )
            )
    return out


def _extract_monetary(text: str, ref_ids: list[str]) -> list[SemanticAsset]:
    seen: set[str] = set()
    out: list[SemanticAsset] = []
    for match in _MONETARY_PATTERN.finditer(text):
        value = " ".join(match.group(0).split())
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            SemanticAsset(
                type="monetary_amount",
                text=value,
                confidence=0.9,
                review_status="needs_review",
                source_reference_ids=list(ref_ids),
            )
        )
    return out


def _extract_requirements(text: str, ref_ids: list[str]) -> list[SemanticAsset]:
    seen: set[str] = set()
    out: list[SemanticAsset] = []
    for match in _REQUIREMENT_PATTERN.finditer(text):
        # Strip leading whitespace and the trailing terminator (., ;, \n).
        phrase = match.group(0).strip().rstrip(".;").strip()
        if not phrase:
            continue
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            SemanticAsset(
                type="requirement_phrase",
                text=phrase,
                confidence=0.5,
                review_status="needs_review",
                source_reference_ids=list(ref_ids),
            )
        )
    return out
