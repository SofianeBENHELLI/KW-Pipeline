"""Tests for the PDF parser (issue #45)."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from fpdf import FPDF

from app.dependencies import build_services
from app.main import create_app
from app.schemas.extraction import NormalizedRect, RawExtraction, RawSection
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


def test_pdf_parser_emits_section_per_paragraph_group_with_page_number():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_pdf(pages=[["Intro paragraph"], ["Body paragraph"]])
    version = _upload(documents, content)

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    assert extraction.parser_name == "pdf"
    # Bumped to 0.2 with the line-level + rect rewrite.
    assert extraction.parser_version == "0.2"
    # Each page is one tightly-spaced paragraph here, so each yields a
    # single section with id ``page-{N}-sec-0`` and the friendly default
    # heading ``Page {N}``.
    assert [s.heading for s in extraction.sections] == ["Page 1", "Page 2"]
    assert [s.id for s in extraction.sections] == ["page-1-sec-0", "page-2-sec-0"]
    assert [r.page_number for r in extraction.source_references] == [1, 2]
    assert len(extraction.source_references) == 2
    for section in extraction.sections:
        assert section.parser_metadata["page_number"] == section.id.split("-")[1]
        assert section.parser_metadata["section_index"] == "0"


def test_pdf_parser_emits_table_section_after_page_text():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_pdf(
        pages=[["Procurement Policy"]],
        table=[["Risk", "Mitigation"], ["Late delivery", "Penalty"]],
    )
    version = _upload(documents, content)

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    headings = [s.heading for s in extraction.sections]
    # The single short paragraph on page 1 yields one text section with
    # the default ``Page 1`` heading; the table section follows.
    assert "Page 1 — Table 1" in headings
    table_sections = [s for s in extraction.sections if "Table" in s.heading]
    assert len(table_sections) == 1
    table_section = table_sections[0]
    assert table_section.id == "page-1-table-0"
    assert table_section.parser_metadata == {"page_number": "1", "table_index": "0"}
    # Tab/newline shape matches DocxParser so downstream tooling sees the
    # same convention regardless of source format.
    assert table_section.text == "Risk\tMitigation\nLate delivery\tPenalty"
    # The table section's reference carries page_number and a rect that
    # covers the table bbox (one normalised rect for the whole table).
    table_ref = next(r for r in extraction.source_references if r.section_id == "page-1-table-0")
    assert table_ref.page_number == 1
    assert len(table_ref.rects) == 1
    assert table_ref.rects[0].page == 1
    # Text sections all come before any table sections (reading order).
    text_indices = [i for i, s in enumerate(extraction.sections) if "Table" not in s.heading]
    table_indices = [i for i, s in enumerate(extraction.sections) if "Table" in s.heading]
    assert max(text_indices) < min(table_indices)


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

    # Only page 1 produced a section (single paragraph → one section).
    assert [s.id for s in extraction.sections] == ["page-1-sec-0"]
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
    integration tests; assert the line-level parser still surfaces the
    expected title, body, and table content with rects populated."""
    documents = DocumentService(storage=InMemoryStorageService())
    version = _upload(documents, FIXTURE_PATH.read_bytes(), filename="sample.pdf")

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    text_sections = [s for s in extraction.sections if "Table" not in s.heading]
    table_sections = [s for s in extraction.sections if "Table" in s.heading]

    # Page 1: title detected as the heading-eligible first line; page 2:
    # single body paragraph, default heading.
    page_1_text = next(s for s in text_sections if s.id.startswith("page-1-"))
    page_2_text = next(s for s in text_sections if s.id.startswith("page-2-"))
    assert page_1_text.heading == "Procurement Policy"
    assert "Suppliers must be evaluated annually." in page_1_text.text
    assert "Annex A: Reviewers must record dissent." in page_2_text.text

    # Single table on page 1, still tab/newline-shaped for downstream
    # tooling parity with DocxParser.
    assert len(table_sections) == 1
    assert table_sections[0].text == "Risk\tMitigation\nLate delivery\tPenalty clause"
    assert table_sections[0].parser_metadata["page_number"] == "1"

    # Every emitted section has at least one rect attached to its
    # source reference — that is the load-bearing contract for the
    # PDF viewer downstream.
    refs_by_section = {r.section_id: r for r in extraction.source_references}
    for section in extraction.sections:
        ref = refs_by_section[section.id]
        assert ref.rects, f"section {section.id} produced no rects"
        for rect in ref.rects:
            assert 0.0 <= rect.x <= 1.0 and 0.0 <= rect.y <= 1.0


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
    # parser_version surfaces on the HTTP wire so the viewer can gate
    # rect-level rendering on it.
    assert extraction["parser_version"] == "0.2"
    # Rects come back as a list on every source reference — empty
    # would mean the viewer cannot draw highlights.
    for ref in extraction["source_references"]:
        assert ref["rects"], f"empty rects on reference {ref['id']}"
        for rect in ref["rects"]:
            assert 0.0 <= rect["x"] <= 1.0
            assert 0.0 <= rect["y"] <= 1.0
            assert 0.0 < rect["width"] <= 1.0
            assert 0.0 < rect["height"] <= 1.0


def test_pdf_parser_emits_one_rect_per_line_in_a_multiline_section():
    documents = DocumentService(storage=InMemoryStorageService())
    # Three tightly-spaced lines fall into a single section group.
    content = _make_pdf(pages=[["Line one of the paragraph", "Line two", "Line three"]])
    version = _upload(documents, content)

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    assert len(extraction.sections) == 1
    ref = extraction.source_references[0]
    # One rect per pdfplumber-detected text line. Order matches reading
    # order so the viewer can draw highlights top-to-bottom.
    assert len(ref.rects) == 3
    ys = [rect.y for rect in ref.rects]
    assert ys == sorted(ys), "rects should be in reading order (top → bottom)"


def test_pdf_parser_normalises_rects_into_the_unit_square():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_pdf(pages=[["Single line"]])
    version = _upload(documents, content)

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    rect = extraction.source_references[0].rects[0]
    assert isinstance(rect, NormalizedRect)
    assert 0.0 <= rect.x <= 1.0
    assert 0.0 <= rect.y <= 1.0
    assert 0.0 < rect.width <= 1.0
    assert 0.0 < rect.height <= 1.0


def test_pdf_parser_handles_landscape_pages():
    """Landscape PDFs (width > height) must still yield rects normalised
    against the page's rendered dimensions — pdfplumber's ``page.width``
    / ``page.height`` already reflect orientation, so this is mainly a
    regression test that the normaliser does not assume portrait."""
    documents = DocumentService(storage=InMemoryStorageService())
    pdf = FPDF(orientation="L", format="letter")
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 7, "Wide page content", new_x="LMARGIN", new_y="NEXT")
    buffer = io.BytesIO()
    pdf.output(buffer)
    version = _upload(documents, buffer.getvalue())

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    rect = extraction.source_references[0].rects[0]
    assert 0.0 <= rect.x <= 1.0
    assert 0.0 <= rect.y <= 1.0
    assert 0.0 < rect.width <= 1.0
    assert 0.0 < rect.height <= 1.0


def test_pdf_parser_splits_paragraphs_separated_by_large_gap_into_distinct_sections():
    """A paragraph break (large vertical gap) should yield two sections
    on the same page so the viewer can highlight them independently."""
    documents = DocumentService(storage=InMemoryStorageService())
    pdf = FPDF(format="letter")
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 7, "First paragraph body content", new_x="LMARGIN", new_y="NEXT")
    # Force a generous vertical gap so the grouping heuristic splits.
    pdf.ln(30)
    pdf.cell(0, 7, "Second paragraph body content", new_x="LMARGIN", new_y="NEXT")
    buffer = io.BytesIO()
    pdf.output(buffer)
    version = _upload(documents, buffer.getvalue())

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    text_sections = [s for s in extraction.sections if "Table" not in s.heading]
    assert len(text_sections) == 2
    assert [s.id for s in text_sections] == ["page-1-sec-0", "page-1-sec-1"]
    # Each section owns its own rect on the same page.
    refs_by_section = {r.section_id: r for r in extraction.source_references}
    rect_0 = refs_by_section["page-1-sec-0"].rects[0]
    rect_1 = refs_by_section["page-1-sec-1"].rects[0]
    assert rect_0.page == rect_1.page == 1
    assert rect_0.y < rect_1.y, "first paragraph rect should sit above the second"


def test_pdf_parser_table_rect_covers_table_area_only():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_pdf(
        pages=[["Heading line above the table"]],
        table=[["A", "B"], ["C", "D"]],
    )
    version = _upload(documents, content)

    extraction = PdfParser().parse(version=version, storage=documents.storage)

    table_section = next(s for s in extraction.sections if "Table" in s.heading)
    table_ref = next(r for r in extraction.source_references if r.section_id == table_section.id)
    assert len(table_ref.rects) == 1
    table_rect = table_ref.rects[0]
    # Table sits below the heading line, so its top is non-trivial.
    assert table_rect.y > 0.05
    assert 0.0 < table_rect.width <= 1.0
    assert 0.0 < table_rect.height <= 1.0
