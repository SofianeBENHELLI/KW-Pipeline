from datetime import UTC, datetime

import pytest

from app.models.document import DocumentVersionStatus
from app.schemas.document import DocumentVersion
from app.schemas.extraction import RawExtraction
from app.schemas.semantic_document import DocumentProfile, SemanticDocument, SemanticSection
from app.services.document_parser import PlainTextParser
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionJobService
from app.services.markdown_generator import MarkdownGenerator
from app.services.semantic_extractor import SemanticExtractor
from app.services.storage_service import InMemoryStorageService


def test_semantic_extraction_and_markdown_include_required_frontmatter():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("risk-register.txt", "text/plain", b"Risk: supplier delay")
    jobs = ExtractionJobService(documents=documents, parser=PlainTextParser())
    raw = jobs.extract(document_id=version.document_id, version_id=version.id)

    semantic = SemanticExtractor().extract(version=version, raw_extraction=raw)
    markdown = MarkdownGenerator().render(version=version, semantic=semantic, raw_extraction=raw)

    assert 'document_id: "' in markdown
    assert f'version_id: "{version.id}"' in markdown
    assert f'sha256: "{version.sha256}"' in markdown
    assert 'parser: "plain_text"' in markdown
    assert 'validation_status: "needs_review"' in markdown
    assert "## Source Lineage" in markdown
    assert "Risk: supplier delay" in markdown


def _stub_version(filename: str = "doc.txt") -> DocumentVersion:
    return DocumentVersion(
        id="ver-1",
        document_id="doc-1",
        version_number=1,
        filename=filename,
        content_type="text/plain",
        file_size=1,
        sha256="a" * 64,
        storage_uri="memory://documents/ver-1/" + filename,
        status=DocumentVersionStatus.EXTRACTED,
    )


def _stub_raw() -> RawExtraction:
    return RawExtraction(
        document_version_id="ver-1",
        parser_name="plain_text",
        parser_version="0.1",
        text="any",
        sections=[],
        source_references=[],
        warnings=[],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _stub_semantic(**overrides) -> SemanticDocument:
    defaults = {
        "document_version_id": "ver-1",
        "document_profile": DocumentProfile(title="Doc"),
        "sections": [],
        "warnings": [],
        "source_references": [],
        "validation_status": "needs_review",
    }
    defaults.update(overrides)
    return SemanticDocument(**defaults)


class TestMarkdownRenderEmptyStates:
    def test_empty_sections_render_none_identified(self):
        markdown = MarkdownGenerator().render(
            version=_stub_version(),
            semantic=_stub_semantic(sections=[]),
            raw_extraction=_stub_raw(),
        )

        assert "## Semantic Sections\n\nNone identified." in markdown

    def test_empty_warnings_render_none_identified(self):
        markdown = MarkdownGenerator().render(
            version=_stub_version(),
            semantic=_stub_semantic(warnings=[]),
            raw_extraction=_stub_raw(),
        )

        assert "## Warnings\n\nNone identified." in markdown

    def test_empty_source_references_render_no_lineage_message(self):
        markdown = MarkdownGenerator().render(
            version=_stub_version(),
            semantic=_stub_semantic(source_references=[]),
            raw_extraction=_stub_raw(),
        )

        assert "No source lineage available." in markdown

    def test_executive_summary_falls_back_when_missing(self):
        semantic = _stub_semantic(
            document_profile=DocumentProfile(title="Doc", executive_summary=None),
        )
        markdown = MarkdownGenerator().render(
            version=_stub_version(), semantic=semantic, raw_extraction=_stub_raw()
        )

        assert "## Executive Summary\n\nNone identified." in markdown


class TestMarkdownRenderPopulated:
    def test_warnings_render_as_bullet_list(self):
        semantic = _stub_semantic(warnings=["A", "B"])
        markdown = MarkdownGenerator().render(
            version=_stub_version(), semantic=semantic, raw_extraction=_stub_raw()
        )

        assert "- A" in markdown
        assert "- B" in markdown

    def test_sections_render_with_heading_and_text(self):
        semantic = _stub_semantic(
            sections=[
                SemanticSection(id="s1", heading="Risks", text="Supplier delay."),
            ]
        )
        markdown = MarkdownGenerator().render(
            version=_stub_version(), semantic=semantic, raw_extraction=_stub_raw()
        )

        assert "### Risks" in markdown
        assert "Supplier delay." in markdown

    def test_section_with_empty_text_falls_back_to_none_identified(self):
        semantic = _stub_semantic(sections=[SemanticSection(id="s1", heading="H", text="")])
        markdown = MarkdownGenerator().render(
            version=_stub_version(), semantic=semantic, raw_extraction=_stub_raw()
        )

        assert "### H" in markdown
        # The "Semantic Sections" body falls back to "None identified." for empty text.
        assert "None identified." in markdown


class TestFormatLocation:
    @pytest.mark.parametrize(
        "ref, expected",
        [
            ({"page_number": 5}, "(page 5)"),
            ({"line_start": 10}, "(line 10)"),
            ({"line_start": 10, "line_end": 10}, "(line 10)"),
            ({"line_start": 10, "line_end": 12}, "(line 10-12)"),
            ({"page_number": 3, "line_start": 7}, "(page 3, line 7)"),
            ({"page_number": 3, "line_start": 7, "line_end": 9}, "(page 3, line 7-9)"),
            ({}, "(location unavailable)"),
            ({"page_number": None, "line_start": None}, "(location unavailable)"),
        ],
    )
    def test_location_formats(self, ref, expected):
        assert MarkdownGenerator()._format_location(ref) == expected

    def test_lineage_section_uses_format_location(self):
        semantic = _stub_semantic(
            source_references=[
                {
                    "id": "r1",
                    "page_number": 2,
                    "line_start": 4,
                    "line_end": 6,
                    "snippet": "hello",
                }
            ]
        )
        markdown = MarkdownGenerator().render(
            version=_stub_version(), semantic=semantic, raw_extraction=_stub_raw()
        )

        assert "- `r1` (page 2, line 4-6): hello" in markdown
