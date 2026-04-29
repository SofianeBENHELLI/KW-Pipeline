from app.schemas.document import DocumentVersion
from app.schemas.extraction import RawExtraction, SourceReference
from app.services.storage_service import InMemoryStorageService


class PlainTextParser:
    name = "plain_text"
    version = "0.1"

    def parse(self, version: DocumentVersion, storage: InMemoryStorageService) -> RawExtraction:
        content = storage.get(version.storage_uri)
        text = content.decode("utf-8", errors="replace")
        lines = [line for line in text.splitlines() if line.strip()]
        source_references = [
            SourceReference(
                document_version_id=version.id,
                section_id=f"line-{index}",
                line_start=index,
                line_end=index,
                snippet=line.strip()[:240],
            )
            for index, line in enumerate(lines, start=1)
        ]
        sections = [
            {
                "id": ref.section_id,
                "heading": "Extracted Text",
                "text": ref.snippet,
                "source_reference_ids": [ref.id],
            }
            for ref in source_references
        ]
        warnings = []
        if not source_references:
            warnings.append("No non-empty text lines were extracted.")
        return RawExtraction(
            document_version_id=version.id,
            parser_name=self.name,
            parser_version=self.version,
            text=text,
            sections=sections,
            source_references=source_references,
            warnings=warnings,
        )
