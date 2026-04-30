from app.schemas.document import DocumentVersion
from app.schemas.extraction import RawExtraction
from app.schemas.semantic_document import DocumentProfile, SemanticDocument, SemanticSection


class SemanticExtractor:
    """Builds schema-validated semantic JSON from raw parser output.

    The current implementation is intentionally conservative: it preserves
    parser sections and marks the whole semantic document as `needs_review`.
    """

    def extract(self, version: DocumentVersion, raw_extraction: RawExtraction) -> SemanticDocument:
        """Transform raw extraction output into a governed semantic document."""
        title = self._title_from_filename(version.filename)
        sections = [
            SemanticSection(
                id=section["id"],
                heading=section.get("heading", "Extracted Text"),
                text=section.get("text", ""),
                source_reference_ids=section.get("source_reference_ids", []),
            )
            for section in raw_extraction.sections
        ]
        warnings = list(raw_extraction.warnings)
        if any(not section.source_reference_ids for section in sections):
            warnings.append("One or more semantic sections are missing source lineage.")
        return SemanticDocument(
            document_version_id=version.id,
            document_profile=DocumentProfile(
                title=title,
                document_type="unknown",
                executive_summary=self._summary(raw_extraction.text),
            ),
            sections=sections,
            assets=[],
            warnings=warnings,
            source_references=[
                ref.model_dump(mode="json") for ref in raw_extraction.source_references
            ],
            validation_status="needs_review",
        )

    def _title_from_filename(self, filename: str) -> str:
        name = filename.rsplit("/", 1)[-1]
        stem = name.rsplit(".", 1)[0]
        return stem.replace("_", " ").replace("-", " ").strip().title() or "Untitled"

    def _summary(self, text: str) -> str | None:
        compact = " ".join(text.split())
        if not compact:
            return None
        return compact[:280]
