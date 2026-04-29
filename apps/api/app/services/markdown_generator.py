from app.schemas.document import DocumentVersion
from app.schemas.extraction import RawExtraction
from app.schemas.semantic_document import SemanticDocument


class MarkdownGenerator:
    def render(
        self,
        version: DocumentVersion,
        semantic: SemanticDocument,
        raw_extraction: RawExtraction,
    ) -> str:
        lines = [
            "---",
            f'document_id: "{version.document_id}"',
            f'version_id: "{version.id}"',
            f'filename: "{version.filename}"',
            f'sha256: "{version.sha256}"',
            f'parser: "{raw_extraction.parser_name}"',
            f'parser_version: "{raw_extraction.parser_version}"',
            f'extraction_date: "{raw_extraction.created_at.isoformat()}"',
            f'validation_status: "{semantic.validation_status}"',
            f'source_uri: "{version.storage_uri}"',
            f'schema_version: "{semantic.schema_version}"',
            "---",
            "",
            f"# {semantic.document_profile.title}",
            "",
            "## Document Profile",
            "",
            f"- Document type: {semantic.document_profile.document_type}",
            f"- Purpose: {semantic.document_profile.purpose or 'Needs review'}",
            f"- Audience: {semantic.document_profile.audience or 'Needs review'}",
            "",
            "## Executive Summary",
            "",
            semantic.document_profile.executive_summary or "None identified.",
            "",
            "## Semantic Sections",
            "",
        ]
        if semantic.sections:
            for section in semantic.sections:
                lines.extend([f"### {section.heading}", "", section.text or "None identified.", ""])
        else:
            lines.extend(["None identified.", ""])
        lines.extend(["## Warnings", ""])
        if semantic.warnings:
            lines.extend([f"- {warning}" for warning in semantic.warnings])
        else:
            lines.append("None identified.")
        lines.extend(["", "## Source Lineage", ""])
        if semantic.source_references:
            for ref in semantic.source_references:
                location = self._format_location(ref)
                lines.append(f"- `{ref['id']}` {location}: {ref['snippet']}")
        else:
            lines.append("No source lineage available.")
        lines.append("")
        return "\n".join(lines)

    def _format_location(self, ref: dict) -> str:
        parts = []
        if ref.get("page_number") is not None:
            parts.append(f"page {ref['page_number']}")
        if ref.get("line_start") is not None:
            line = f"line {ref['line_start']}"
            if ref.get("line_end") and ref["line_end"] != ref["line_start"]:
                line += f"-{ref['line_end']}"
            parts.append(line)
        return f"({', '.join(parts)})" if parts else "(location unavailable)"
