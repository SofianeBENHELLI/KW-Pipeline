"""Tests for the DOCX parser (issue #46)."""

from __future__ import annotations

import io
from pathlib import Path

from docx import Document as build_docx
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.schemas.extraction import RawExtraction, RawSection
from app.services.document_parser import Parser
from app.services.document_service import DocumentService
from app.services.parsers import DocxParser
from app.services.parsers.docx import DOCX_CONTENT_TYPE
from app.services.storage_service import InMemoryStorageService

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample.docx"


def _make_docx(
    *,
    paragraphs: list[str] | None = None,
    tables: list[list[list[str]]] | None = None,
) -> bytes:
    """Build a DOCX in-memory and return its bytes for parser tests."""
    document = build_docx()
    for paragraph in paragraphs or []:
        document.add_paragraph(paragraph)
    for table_rows in tables or []:
        if not table_rows:
            continue
        table = document.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for row_index, row in enumerate(table_rows):
            for col_index, cell_text in enumerate(row):
                table.rows[row_index].cells[col_index].text = cell_text
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _upload(documents: DocumentService, content: bytes, filename: str = "doc.docx"):
    return documents.upload(filename, DOCX_CONTENT_TYPE, content)


def test_docx_parser_paragraphs_become_source_references_in_order():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_docx(paragraphs=["Procurement Policy", "Review annually", "Approve over 100k"])
    version = _upload(documents, content)

    extraction = DocxParser().parse(version=version, storage=documents.storage)

    assert extraction.parser_name == "docx"
    assert extraction.parser_version == "0.1"
    assert [section.text for section in extraction.sections] == [
        "Procurement Policy",
        "Review annually",
        "Approve over 100k",
    ]
    assert all(section.heading == "Paragraph" for section in extraction.sections)
    # One reference per paragraph, with stable IDs and DOCX-specific metadata.
    assert len(extraction.source_references) == len(extraction.sections)
    for index, section in enumerate(extraction.sections):
        assert section.id == f"para-{index}"
        assert section.parser_metadata == {"paragraph_index": str(index)}
        assert section.source_reference_ids == [extraction.source_references[index].id]
    # DOCX-specific reference fields: pagination is not exposed.
    for ref in extraction.source_references:
        assert ref.page_number is None
        assert ref.line_start is None
        assert ref.line_end is None


def test_docx_parser_renders_table_into_section():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_docx(
        tables=[[["Risk", "Mitigation"], ["Late delivery", "Penalty"]]],
    )
    version = _upload(documents, content)

    extraction = DocxParser().parse(version=version, storage=documents.storage)

    assert len(extraction.sections) == 1
    section = extraction.sections[0]
    assert section.heading == "Table 1"
    assert section.text == "Risk\tMitigation\nLate delivery\tPenalty"
    assert section.parser_metadata == {"table_index": "0"}
    assert section.id == "table-0"
    assert extraction.source_references[0].snippet.startswith("Risk\tMitigation")


def test_docx_parser_emits_paragraphs_then_tables_deterministically():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_docx(
        paragraphs=["Intro", "Body"],
        tables=[
            [["A", "B"], ["1", "2"]],
            [["X"], ["Y"]],
        ],
    )
    version = _upload(documents, content)

    extraction = DocxParser().parse(version=version, storage=documents.storage)

    headings = [section.heading for section in extraction.sections]
    assert headings == ["Paragraph", "Paragraph", "Table 1", "Table 2"]
    ids = [section.id for section in extraction.sections]
    assert ids == ["para-0", "para-1", "table-0", "table-1"]
    # Aggregated text combines paragraphs and tables in the same order.
    assert "Intro" in extraction.text
    assert "A\tB" in extraction.text
    assert "X" in extraction.text


def test_docx_parser_skips_whitespace_only_paragraphs():
    """Word inserts blank paragraphs liberally; these must not produce
    references that point at empty text."""
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_docx(paragraphs=["First", "   ", "", "Second"])
    version = _upload(documents, content)

    extraction = DocxParser().parse(version=version, storage=documents.storage)

    texts = [section.text for section in extraction.sections]
    assert texts == ["First", "Second"]
    # paragraph_index reflects the original DOCX position (0 and 3), so
    # downstream tooling can map back to the on-disk element even after
    # blank paragraphs are dropped.
    assert [s.parser_metadata["paragraph_index"] for s in extraction.sections] == ["0", "3"]


def test_docx_parser_empty_document_warns_without_raising():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_docx()  # No paragraphs added beyond the empty default.
    version = _upload(documents, content)

    extraction = DocxParser().parse(version=version, storage=documents.storage)

    assert extraction.source_references == []
    assert extraction.sections == []
    assert "No paragraphs or tables in document." in extraction.warnings


def test_docx_parser_round_trips_through_json():
    documents = DocumentService(storage=InMemoryStorageService())
    content = _make_docx(
        paragraphs=["Hello"],
        tables=[[["c1", "c2"]]],
    )
    version = _upload(documents, content)
    extraction = DocxParser().parse(version=version, storage=documents.storage)

    payload = extraction.model_dump_json()
    restored = RawExtraction.model_validate_json(payload)

    assert restored == extraction
    assert all(isinstance(section, RawSection) for section in restored.sections)


def test_docx_parser_conforms_to_parser_protocol():
    parser = DocxParser()

    assert isinstance(parser, Parser)
    assert parser.supported_content_types == frozenset({DOCX_CONTENT_TYPE})


def test_registry_resolves_docx_content_type_to_docx_parser():
    services = build_services()

    parser = services.parsers.for_content_type(DOCX_CONTENT_TYPE)

    assert isinstance(parser, DocxParser)


def test_committed_fixture_parses_with_expected_paragraphs_and_table():
    """The .docx checked into the repo is the source of truth for downstream
    integration tests; assert it still produces the structure we generated."""
    documents = DocumentService(storage=InMemoryStorageService())
    version = _upload(documents, FIXTURE_PATH.read_bytes(), filename="sample.docx")

    extraction = DocxParser().parse(version=version, storage=documents.storage)

    paragraph_sections = [s for s in extraction.sections if s.heading == "Paragraph"]
    table_sections = [s for s in extraction.sections if s.heading.startswith("Table ")]
    assert [s.text for s in paragraph_sections] == [
        "Procurement Policy",
        "Suppliers must be evaluated annually.",
        "Contracts above 100k require dual approval.",
    ]
    assert len(table_sections) == 1
    assert table_sections[0].text == "Risk\tMitigation\nLate delivery\tPenalty clause"


def test_docx_upload_extract_http_flow(monkeypatch):
    """End-to-end: HTTP upload + extract for the DOCX MIME, with the
    content-type allowlist widened via env var."""
    monkeypatch.setenv("ALLOWED_CONTENT_TYPES", f"text/plain,{DOCX_CONTENT_TYPE}")
    client = TestClient(create_app())

    content = _make_docx(paragraphs=["Alpha", "Beta", "Gamma"])

    upload_response = client.post(
        "/documents/upload",
        files={"file": ("policy.docx", content, DOCX_CONTENT_TYPE)},
    )
    assert upload_response.status_code == 200, upload_response.text
    version = upload_response.json()
    assert version["content_type"] == DOCX_CONTENT_TYPE

    extract_response = client.post(
        f"/documents/{version['document_id']}/versions/{version['id']}/extract"
    )
    assert extract_response.status_code == 200, extract_response.text
    extraction = extract_response.json()
    assert extraction["parser_name"] == "docx"
    paragraphs = [
        section["text"] for section in extraction["sections"] if section["heading"] == "Paragraph"
    ]
    assert paragraphs == ["Alpha", "Beta", "Gamma"]
    assert len(extraction["source_references"]) == 3
