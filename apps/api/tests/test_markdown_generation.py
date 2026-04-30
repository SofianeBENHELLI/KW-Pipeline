from datetime import UTC, datetime

import pytest
import yaml

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


def _extract_frontmatter(markdown: str) -> dict:
    """Parse the YAML frontmatter block out of a rendered Markdown document."""
    assert markdown.startswith("---\n"), markdown[:20]
    body = markdown[len("---\n") :]
    end = body.index("\n---\n")
    return yaml.safe_load(body[:end])


def test_semantic_extraction_and_markdown_include_required_frontmatter():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("risk-register.txt", "text/plain", b"Risk: supplier delay")
    jobs = ExtractionJobService(documents=documents, parser=PlainTextParser())
    raw = jobs.extract(document_id=version.document_id, version_id=version.id)

    semantic = SemanticExtractor().extract(version=version, raw_extraction=raw)
    markdown = MarkdownGenerator().render(version=version, semantic=semantic, raw_extraction=raw)

    front = _extract_frontmatter(markdown)
    assert front["document_id"] == version.document_id
    assert front["version_id"] == version.id
    assert front["sha256"] == version.sha256
    assert front["parser"] == "plain_text"
    assert front["validation_status"] == "needs_review"
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


def _stub_raw(parser_name: str = "plain_text") -> RawExtraction:
    return RawExtraction(
        document_version_id="ver-1",
        parser_name=parser_name,
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


class TestFrontmatterEscaping:
    """yaml.safe_dump must round-trip adversarial filename/parser/storage_uri values
    that would break a hand-rolled f-string YAML serializer (issue #64)."""

    @pytest.mark.parametrize(
        "filename",
        [
            'has "double" quotes.txt',
            "has\\backslash.txt",
            "has\nnewline.txt",
            "has\ttab.txt",
            "résumé-naïve-中文-😀.txt",
            "single 'quote' and \"double\" quote.txt",
            "ends with backslash\\",
            "---\nfake_key: injected\n",
        ],
    )
    def test_adversarial_filenames_round_trip_through_yaml(self, filename):
        version = _stub_version(filename=filename)
        markdown = MarkdownGenerator().render(
            version=version, semantic=_stub_semantic(), raw_extraction=_stub_raw()
        )

        front = _extract_frontmatter(markdown)
        assert front["filename"] == filename
        # storage_uri embeds the filename; it must round-trip too.
        assert front["source_uri"] == version.storage_uri
        # Injection attempts must not leak fake keys.
        assert "fake_key" not in front

    def test_adversarial_parser_name_round_trips(self):
        raw = _stub_raw(parser_name='evil"\nparser: hijacked')
        markdown = MarkdownGenerator().render(
            version=_stub_version(), semantic=_stub_semantic(), raw_extraction=raw
        )

        front = _extract_frontmatter(markdown)
        assert front["parser"] == 'evil"\nparser: hijacked'
        assert front["validation_status"] == "needs_review"

    def test_safe_filename_renders_as_plain_yaml_scalar(self):
        markdown = MarkdownGenerator().render(
            version=_stub_version(filename="doc.txt"),
            semantic=_stub_semantic(),
            raw_extraction=_stub_raw(),
        )

        # Plain ASCII filenames stay readable; the frontmatter parses identically
        # whether or not yaml decides to quote.
        front = _extract_frontmatter(markdown)
        assert front["filename"] == "doc.txt"
        assert front["source_uri"] == "memory://documents/ver-1/doc.txt"

    def test_frontmatter_block_is_well_formed(self):
        markdown = MarkdownGenerator().render(
            version=_stub_version(),
            semantic=_stub_semantic(),
            raw_extraction=_stub_raw(),
        )

        # Exactly one opening "---" line and one closing "---" line bracket the
        # frontmatter; the body that follows is the document title.
        lines = markdown.splitlines()
        assert lines[0] == "---"
        closing = lines.index("---", 1)
        assert lines[closing + 1] == ""
        assert lines[closing + 2].startswith("# ")
