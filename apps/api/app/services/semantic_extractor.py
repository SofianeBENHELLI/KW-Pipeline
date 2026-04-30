import logging

from pydantic import ValidationError

from app.schemas.document import DocumentVersion
from app.schemas.extraction import RawExtraction
from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticAsset,
    SemanticDocument,
    SemanticSection,
)
from app.services.enrichers import SemanticEnricher

logger = logging.getLogger(__name__)


class SemanticExtractor:
    """Builds schema-validated semantic JSON from raw parser output.

    The current implementation is intentionally conservative: it preserves
    parser sections and marks the whole semantic document as `needs_review`.

    Optional :class:`SemanticEnricher` instances can be registered via the
    constructor. They run after the rule-based pass and may produce
    additional semantic assets. The boundary enforces three guarantees on
    every enricher's output (see ADR-009):

    1. Each returned item is re-validated as a ``SemanticAsset``;
       malformed items are dropped with a warning log.
    2. ``review_status`` is forced to ``"needs_review"`` regardless of
       what the enricher claims.
    3. An enricher that raises is logged and skipped; subsequent enrichers
       still run and the catalog state is unchanged.
    """

    def __init__(self, enrichers: list[SemanticEnricher] | None = None) -> None:
        self._enrichers: list[SemanticEnricher] = list(enrichers) if enrichers else []

    def extract(self, version: DocumentVersion, raw_extraction: RawExtraction) -> SemanticDocument:
        """Transform raw extraction output into a governed semantic document."""
        title = self._title_from_filename(version.filename)
        sections = [
            SemanticSection(
                id=section.id,
                heading=section.heading,
                text=section.text,
                source_reference_ids=list(section.source_reference_ids),
            )
            for section in raw_extraction.sections
        ]
        warnings = list(raw_extraction.warnings)
        if any(not section.source_reference_ids for section in sections):
            warnings.append("One or more semantic sections are missing source lineage.")

        assets: list[SemanticAsset] = []
        for enricher in self._enrichers:
            assets.extend(self._run_enricher(enricher, raw_extraction, assets))

        return SemanticDocument(
            document_version_id=version.id,
            document_profile=DocumentProfile(
                title=title,
                document_type="unknown",
                executive_summary=self._summary(raw_extraction.text),
            ),
            sections=sections,
            assets=assets,
            warnings=warnings,
            source_references=[
                ref.model_dump(mode="json") for ref in raw_extraction.source_references
            ],
            validation_status="needs_review",
        )

    def _run_enricher(
        self,
        enricher: SemanticEnricher,
        raw_extraction: RawExtraction,
        existing_assets: list[SemanticAsset],
    ) -> list[SemanticAsset]:
        """Invoke one enricher and apply the boundary guarantees.

        Anything the enricher returns is re-validated against the
        ``SemanticAsset`` schema and forced to ``review_status =
        "needs_review"``. Exceptions raised by the enricher are logged and
        swallowed so a single bad enricher cannot abort the pipeline.
        """
        name = getattr(enricher, "name", enricher.__class__.__name__)
        try:
            raw_results = enricher.enrich(raw_extraction, existing_assets)
        except Exception:
            logger.exception("Semantic enricher %r raised; skipping its output.", name)
            return []

        validated: list[SemanticAsset] = []
        for item in raw_results:
            try:
                asset = SemanticAsset.model_validate(item)
            except ValidationError as exc:
                logger.warning(
                    "Semantic enricher %r produced an invalid asset; dropping it: %s",
                    name,
                    exc,
                )
                continue
            # Force the boundary guarantee regardless of what the enricher
            # claimed. A compromised or over-confident model cannot
            # self-promote past human review.
            validated.append(asset.model_copy(update={"review_status": "needs_review"}))
        return validated

    def _title_from_filename(self, filename: str) -> str:
        name = filename.rsplit("/", 1)[-1]
        stem = name.rsplit(".", 1)[0]
        return stem.replace("_", " ").replace("-", " ").strip().title() or "Untitled"

    def _summary(self, text: str) -> str | None:
        compact = " ".join(text.split())
        if not compact:
            return None
        return compact[:280]
