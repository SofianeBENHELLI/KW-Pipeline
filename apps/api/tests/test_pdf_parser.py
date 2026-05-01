"""Tests for the PDF parser (issue #45)."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from fpdf import FPDF

from app.dependencies import build_services
from app.main import create_app
from app.schemas.extraction import RawExtraction, RawSection
from app.services.document_parser import Parser
from app.services.document_service import DocumentService
from app.services.parsers import PdfParser
from app.services.parsers.pdf import PDF_CONTENT_TYPE
from app.services.storage_service import InMemoryStorageService

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample.pdf"


def _make_pdf(
    *, pages: list[list[str]] | None = None, table: list[list[str]] | None = None
) -> bytes:
    """Build a PDF in memory.

    Each entry in ``pages`` is a list of paragraphs to render on its own
    PDF page. If ``table`` is provided it is appended after the paragraphs
    on the FIRST page, with one-cell-thick borders so pdfplumber detects
    it as a table. Returns the rendered PDF bytes.
    """
    pdf = FPDF(format="letter")
    pdf.set_auto_page_break(True, margin=15)
    for page_index, paragraphs in enumerate(pages or [[]]):
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        for paragraph in paragraphs:
            pdf.cell(0, 7, paragraph, new_x="LMARGIN", new_y="NEXT")
        if page_index == 0 and table:
            pdf.ln(4)
            pdf.set_font("Helvetica", size=11)
            col_w = 60
            for row in table:
                for cell_text in row:
                    pdf.cell(col_w, 8, cell_text, border=1)
                pdf.ln(8)
    buffer = io.BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()


def _make_blank_pdf(num_pages: int = 1) -> bytes:
    """Build a PDF with N pages and no text on any of them."""
    pdf = FPDF(format="letter")
    for _ in range(num_pages):
        pdf.add_page()
    buffer = io.BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()


def _upload(documents: DocumentService, content: bytes, filename: str = "doc.pdf"):
    return documents.upload(filename, PDF_CONTENT_TYPE, content)


def test_pdf_parser_each_page_becomes_one_text_section_with_page_number():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_pdf(pages=[["Intro paragraph"], ["Body paragraph"]])
    version = _upload(documents, content)

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    assert extraction.parser_name == "pdf"
    assert extraction.parser_version == "0.1"
    assert [s.heading for s in extraction.sections] == ["Page 1", "Page 2"]
    assert [s.id for s in extraction.sections] == ["page-1", "page-2"]
    assert [r.page_number for r in extraction.source_references] == [1, 2]
    # Each page has exactly one source reference (no tables in this fixture).
    assert len(extraction.source_references) == 2
    for section in extraction.sections:
        assert section.parser_metadata["page_number"] == section.id.removeprefix("page-")


def test_pdf_parser_emits_table_section_after_page_text():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_pdf(
        pages=[["Procurement Policy"]],
        table=[["Risk", "Mitigation"], ["Late delivery", "Penalty"]],
    )
    version = _upload(documents, content)

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    headings = [s.heading for s in extraction.sections]
    # Text section first, then the table section for the same page.
    assert headings == ["Page 1", "Page 1 — Table 1"]
    table_section = extraction.sections[1]
    assert table_section.id == "page-1-table-0"
    assert table_section.parser_metadata == {"page_number": "1", "table_index": "0"}
    # Tab/newline shape matches DocxParser so downstream tooling sees the
    # same convention regardless of source format.
    assert table_section.text == "Risk\tMitigation\nLate delivery\tPenalty"
    # The table section's reference also carries page_number.
    table_ref = next(r for r in extraction.source_references if r.section_id == "page-1-table-0")
    assert table_ref.page_number == 1


def test_pdf_parser_empty_document_warns_without_raising():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_blank_pdf(num_pages=1)
    version = _upload(documents, content)

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    assert extraction.sections == []
    assert extraction.source_references == []
    assert any("No text or tables extracted" in w for w in extraction.warnings)


def test_pdf_parser_flags_pages_without_text_with_ocr_hint():
    """A multi-page PDF where some pages have no extractable text should
    surface those page numbers with a hint pointing at the OCR backlog
    item — that signature is the giveaway of a scanned-image PDF."""
    documents = DocumentService(storage=InMemoryStorageService())
    # Page 1 has text; pages 2-3 are blank.
    pdf = FPDF(format="letter")
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 7, "Page one has text", new_x="LMARGIN", new_y="NEXT")
    pdf.add_page()
    pdf.add_page()
    buffer = io.BytesIO()
    pdf.output(buffer)
    version = _upload(documents, buffer.getvalue())

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    # Only page 1 produced a section.
    assert [s.id for s in extraction.sections] == ["page-1"]
    ocr_warning = next(w for w in extraction.warnings if "OCR" in w)
    assert "[2, 3]" in ocr_warning
    assert "#47" in ocr_warning


def test_pdf_parser_round_trips_through_json():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_pdf(pages=[["Hello"]], table=[["c1", "c2"]])
    version = _upload(documents, content)

    extraction = PdfParser().parse(version=version, storage=documents.storage)
    payload = extraction.model_dump_json()
    restored = RawExtraction.model_validate_json(payload)

    assert restored == extraction
    assert all(isinstance(s, RawSection) for s in restored.sections)


def test_pdf_parser_conforms_to_parser_protocol():
    parser = PdfParser()

    assert isinstance(parser, Parser)
    assert parser.supported_content_types == frozenset({PDF_CONTENT_TYPE})


def test_registry_resolves_pdf_content_type_to_pdf_parser():
    services = build_services()

    parser = services.parsers.for_content_type(PDF_CONTENT_TYPE)

    assert isinstance(parser, PdfParser)


def test_committed_fixture_parses_with_expected_pages_and_table():
    """The .pdf checked into the repo is the source of truth for downstream
    integration tests; assert it still produces the structure we generated."""
    documents = DocumentService(storage=InMemoryStorageService())
    version = _upload(documents, FIXTURE_PATH.read_bytes(), filename="sample.pdf")

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    page_sections = [
        s for s in extraction.sections if s.heading.startswith("Page ") and "Table" not in s.heading
    ]
    table_sections = [s for s in extraction.sections if "Table" in s.heading]

    assert [s.heading for s in page_sections] == ["Page 1", "Page 2"]
    assert "Procurement Policy" in page_sections[0].text
    assert "Suppliers must be evaluated annually." in page_sections[0].text
    assert "Annex A: Reviewers must record dissent." in page_sections[1].text
    assert len(table_sections) == 1
    assert table_sections[0].text == "Risk\tMitigation\nLate delivery\tPenalty clause"
    assert table_sections[0].parser_metadata["page_number"] == "1"


def test_pdf_upload_extract_http_flow(monkeypatch):
    """End-to-end: HTTP upload + extract for the PDF MIME, with the
    content-type allowlist widened via env var."""
    monkeypatch.setenv("ALLOWED_CONTENT_TYPES", f"text/plain,{PDF_CONTENT_TYPE}")
    client = TestClient(create_app())

    content = _make_pdf(pages=[["Alpha", "Beta"], ["Gamma"]])

    upload_response = client.post(
        "/documents/upload",
        files={"file": ("policy.pdf", content, PDF_CONTENT_TYPE)},
    )
    assert upload_response.status_code == 200, upload_response.text
    version = upload_response.json()
    assert version["content_type"] == PDF_CONTENT_TYPE

    extract_response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract"
    )
    assert extract_response.status_code == 200, extract_response.text
    extraction = extract_response.json()
    assert extraction["parser_name"] == "pdf"
    headings = [s["heading"] for s in extraction["sections"]]
    assert headings == ["Page 1", "Page 2"]
    page_numbers = [r["page_number"] for r in extraction["source_references"]]
    assert page_numbers == [1, 2]
