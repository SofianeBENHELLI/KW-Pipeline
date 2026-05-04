"""Semantic enricher boundary for optional LLM-backed extractors.

See ADR-009. The Protocol defined here is the only contract a future LLM
provider integration must satisfy. The boundary's failure-isolation
guarantees (validate every returned asset, force ``review_status =
"needs_review"``, log and skip on error) are enforced inside
``SemanticExtractor`` — not by the enricher itself — so a misbehaving or
prompt-injected enricher cannot bypass them.
"""

from typing import Protocol, runtime_checkable

from app.schemas.extraction import RawExtraction
from app.schemas.semantic_document import SemanticAsset
from app.services.enrichers.rule_based_entities import RuleBasedEntityEnricher
from app.services.enrichers.spacy_ner import SpacyNerEnricher


@runtime_checkable
class SemanticEnricher(Protocol):
    """Optional extractor that produces *additional* semantic assets.

    Implementations receive the immutable ``RawExtraction`` and the list of
    assets produced so far (rule-based output plus any prior enrichers in
    the chain). They return *additional* assets to append; they do not
    return the merged list.

    Implementations MUST NOT assume their declared ``review_status`` will
    be honoured. ``SemanticExtractor`` overwrites it to ``"needs_review"``
    after the call returns.
    """

    name: str

    def enrich(
        self,
        raw_extraction: RawExtraction,
        existing_assets: list[SemanticAsset],
    ) -> list[SemanticAsset]:
        """Return additional ``SemanticAsset`` rows for this extraction."""
        ...


class NoOpEnricher:
    """Test double / placeholder that returns no additional assets.

    Useful for asserting that the boundary itself is wired correctly
    without exercising any provider-specific logic.
    """

    name = "noop"

    def enrich(
        self,
        raw_extraction: RawExtraction,
        existing_assets: list[SemanticAsset],
    ) -> list[SemanticAsset]:
        return []


__all__ = [
    "NoOpEnricher",
    "RuleBasedEntityEnricher",
    "SemanticEnricher",
    "SpacyNerEnricher",
]
