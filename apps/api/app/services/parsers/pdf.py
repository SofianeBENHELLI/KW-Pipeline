"""PDF parser backed by pdfplumber.

Closes #45. Conforms to the ``Parser`` Protocol declared in
``app.services.document_parser`` so the ``ParserRegistry`` can dispatch
``application/pdf`` uploads to it without any service-layer changes.

pdfplumber is MIT-licensed; the dependency is pinned in
``apps/api/pyproject.toml``. See ``docs/adr/ADR-010-pdf-parser.md`` for the
rationale behind picking pdfplumber over Docling for the MVP.
"""

from __future__ import annotations

import io

import pdfplumber

from app.schemas.document import DocumentVersion
from app.schemas.extraction import RawExtraction, RawSection, SourceReference
from app.services.storage_service import StorageService

PDF_CONTENT_TYPE = "application/pdf"


def _table_to_text(table: list[list[str | None]]) -> str:
    """Render a pdfplumber table (list of row lists) into deterministic text.

    Matches the DOCX parser's tab/newline convention so downstream tooling
    sees the same shape regardless of source format. ``None`` cells (which
    pdfplumber emits for empty cells) become empty strings.
    """
    return "\n".join("\t".join((cell or "") for cell in row) for row in table)


class PdfParser:
    """Parser for ``application/pdf`` documents.

    Each PDF page becomes one text ``RawSection`` (when the page has any
    extracted text) and one ``RawSection`` per table pdfplumber finds on
    that page. Sections are emitted in document order — page 1 text, page 1
    tables, page 2 text, etc. Every ``SourceReference`` carries
    ``page_number`` so the Markdown lineage can render ``(page N, …)``
    correctly.

    Empty PDFs (no extractable text on any page and no tables) produce an
    extraction with zero sources and a single warning; the lifecycle FSM is
    responsible for surfacing that to the reviewer. The parser never raises
    on empty input.

    pdfplumber's text extraction is exact for selectable text but does not
    OCR scanned images — those PDFs will surface as empty pages with a
    warning. OCR support is tracked separately in #47.
    """

    name = "pdf"
    # Tied to pdfplumber's API; bump alongside the dependency floor.
    version = "0.1"
    supported_content_types: frozenset[str] = frozenset({PDF_CONTENT_TYPE})

    def parse(self, version: DocumentVersion, storage: StorageService) -> RawExtraction:
        """Read stored PDF bytes and return a typed ``RawExtraction``."""
        content = storage.get(version.storage_uri)

        sections: list[RawSection] = []
        source_references: list[SourceReference] = []
        text_parts: list[str] = []
        warnings: list[str] = []
        empty_pages: list[int] = []

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page_index, page in enumerate(pdf.pages):
                # Pages are 1-indexed in user-facing references; the schema
                # already documents page_number as 1-based.
                page_number = page_index + 1

                page_text = (page.extract_text() or "").strip()
                if page_text:
                    section_id = f"page-{page_number}"
                    reference = SourceReference(
                        document_version_id=version.id,
                        section_id=section_id,
                        page_number=page_number,
                        line_start=None,
                        line_end=None,
                        snippet=page_text[:240],
                    )
                    section = RawSection(
                        id=section_id,
                        heading=f"Page {page_number}",
                        text=page_text,
                        source_reference_ids=[reference.id],
                        parser_metadata={"page_number": str(page_number)},
                    )
                    sections.append(section)
                    source_references.append(reference)
                    text_parts.append(page_text)
                else:
                    empty_pages.append(page_number)

                # Tables come after the page's text section so reviewers see
                # narrative first, structured data second.
                page_tables = page.extract_tables() or []
                for table_index, table in enumerate(page_tables):
                    table_text = _table_to_text(table)
                    if not table_text.strip():
                        continue
                    section_id = f"page-{page_number}-table-{table_index}"
                    reference = SourceReference(
                        document_version_id=version.id,
                        section_id=section_id,
                        page_number=page_number,
                        line_start=None,
                        line_end=None,
                        snippet=table_text[:240],
                    )
                    section = RawSection(
                        id=section_id,
                        heading=f"Page {page_number} — Table {table_index + 1}",
                        text=table_text,
                        source_reference_ids=[reference.id],
                        parser_metadata={
                            "page_number": str(page_number),
                            "table_index": str(table_index),
                        },
                    )
                    sections.append(section)
                    source_references.append(reference)
                    text_parts.append(table_text)

        if not sections:
            warnings.append("No text or tables extracted from PDF.")
        if empty_pages:
            # Surface empty pages as a hint that OCR (issue #47) might be
            # needed — common signature of scanned-image PDFs.
            warnings.append(
                f"Pages with no extractable text: {empty_pages}. "
                "If the PDF is a scanned image, OCR is required (#47)."
            )

        return RawExtraction(
            document_version_id=version.id,
            parser_name=self.name,
            parser_version=self.version,
            text="\n\n".join(text_parts),
            sections=sections,
            source_references=source_references,
            warnings=warnings,
        )
