"""DOCX parser backed by python-docx.

Closes #46. Conforms to the ``Parser`` Protocol declared in
``app.services.document_parser`` so the ``ParserRegistry`` can dispatch
``application/vnd.openxmlformats-officedocument.wordprocessingml.document``
uploads to it without any service-layer changes.

python-docx is MIT-licensed; the dependency is pinned in
``apps/api/pyproject.toml``.
"""

from __future__ import annotations

import io

from docx import Document as load_docx

from app.schemas.document import DocumentVersion
from app.schemas.extraction import RawExtraction, RawSection, SourceReference
from app.services.storage_service import StorageService

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _table_to_text(table) -> str:
    """Render a python-docx table into a deterministic tab/newline blob."""
    return "\n".join("\t".join(cell.text for cell in row.cells) for row in table.rows)


class DocxParser:
    """Parser for ``.docx`` Word documents.

    Each ``Paragraph`` becomes one ``RawSection`` (and one ``SourceReference``)
    in document order. Each ``Table`` is appended as an additional section
    whose ``text`` joins cells with ``\\t`` and rows with ``\\n``. Empty
    paragraphs (``paragraph.text.strip() == ""``) are skipped so we don't
    emit references that point at whitespace; the Word default style produces
    a few of these on save.

    DOCX does not expose pagination cheaply, so ``page_number`` is left
    ``None`` on every reference. ``parser_metadata`` carries the originating
    ``paragraph_index`` or ``table_index`` so downstream consumers can
    reconstruct which python-docx element a section came from.

    When the document is empty (no non-blank paragraphs and no tables) the
    parser emits a warning and returns an extraction with zero source
    references; the caller's lifecycle (``ExtractionJobService``) is
    responsible for surfacing that to the reviewer. The parser never raises
    on empty input.
    """

    name = "docx"
    # Tied to python-docx's API; bump alongside the dependency floor.
    version = "0.1"
    supported_content_types: frozenset[str] = frozenset({DOCX_CONTENT_TYPE})

    def parse(self, version: DocumentVersion, storage: StorageService) -> RawExtraction:
        """Read stored DOCX bytes and return a typed ``RawExtraction``."""
        content = storage.get(version.storage_uri)
        document = load_docx(io.BytesIO(content))

        sections: list[RawSection] = []
        source_references: list[SourceReference] = []
        text_parts: list[str] = []

        # Paragraphs first, in document order. Skip purely whitespace
        # paragraphs so reviewers don't see references pointing at "".
        for paragraph_index, paragraph in enumerate(document.paragraphs):
            paragraph_text = paragraph.text
            if not paragraph_text.strip():
                continue
            section_id = f"para-{paragraph_index}"
            reference = SourceReference(
                document_version_id=version.id,
                section_id=section_id,
                page_number=None,
                line_start=None,
                line_end=None,
                snippet=paragraph_text.strip()[:240],
            )
            section = RawSection(
                id=section_id,
                heading="Paragraph",
                text=paragraph_text,
                source_reference_ids=[reference.id],
                parser_metadata={"paragraph_index": str(paragraph_index)},
            )
            sections.append(section)
            source_references.append(reference)
            text_parts.append(paragraph_text)

        # Tables after paragraphs. ``Table N`` is 1-indexed for human
        # readability in the headings, while ``table_index`` retains the
        # 0-indexed integer for downstream tooling.
        for table_index, table in enumerate(document.tables):
            table_text = _table_to_text(table)
            section_id = f"table-{table_index}"
            reference = SourceReference(
                document_version_id=version.id,
                section_id=section_id,
                page_number=None,
                line_start=None,
                line_end=None,
                snippet=table_text[:240],
            )
            section = RawSection(
                id=section_id,
                heading=f"Table {table_index + 1}",
                text=table_text,
                source_reference_ids=[reference.id],
                parser_metadata={"table_index": str(table_index)},
            )
            sections.append(section)
            source_references.append(reference)
            text_parts.append(table_text)

        warnings: list[str] = []
        if not sections:
            warnings.append("No paragraphs or tables in document.")

        return RawExtraction(
            document_version_id=version.id,
            parser_name=self.name,
            parser_version=self.version,
            text="\n\n".join(text_parts),
            sections=sections,
            source_references=source_references,
            warnings=warnings,
        )
