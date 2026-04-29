import pytest

from app.models.document import DocumentVersionStatus
from app.schemas.document import DocumentVersion
from app.schemas.extraction import RawExtraction, SourceReference
from app.services.semantic_extractor import SemanticExtractor


def _version(filename: str = "policy.txt") -> DocumentVersion:
    return DocumentVersion(
        id="ver-1",
        document_id="doc-1",
        version_number=1,
        filename=filename,
        content_type="text/plain",
        file_size=10,
        sha256="a" * 64,
        storage_uri="memory://documents/ver-1/" + filename,
        status=DocumentVersionStatus.EXTRACTED,
    )


def _extraction(text: str = "x", sections=None, source_refs=None, warnings=None) -> RawExtraction:
    return RawExtraction(
        document_version_id="ver-1",
        parser_name="plain_text",
        parser_version="0.1",
        text=text,
        sections=sections or [],
        source_references=source_refs or [],
        warnings=warnings or [],
    )


@pytest.fixture
def extractor():
    return SemanticExtractor()


class TestSemanticExtractorTitle:
    @pytest.mark.parametrize(
        "filename, expected_title",
        [
            ("policy.txt", "Policy"),
            ("my_policy_v2.txt", "My Policy V2"),
            ("annual-report-2024.docx", "Annual Report 2024"),
            ("/long/path/to/file.txt", "File"),
            ("README", "Readme"),
            ("noext", "Noext"),
            ("__init__.py", "Init"),  # leading/trailing _ collapse to spaces, stripped
        ],
    )
    def test_titles_are_human_friendly(self, extractor, filename, expected_title):
        version = _version(filename=filename)

        semantic = extractor.extract(version=version, raw_extraction=_extraction())

        assert semantic.document_profile.title == expected_title

    def test_empty_filename_falls_back_to_untitled(self, extractor):
        version = _version(filename="")

        semantic = extractor.extract(version=version, raw_extraction=_extraction())

        assert semantic.document_profile.title == "Untitled"


class TestSemanticExtractorSummary:
    def test_summary_is_none_for_empty_text(self, extractor):
        version = _version()

        semantic = extractor.extract(version=version, raw_extraction=_extraction(text=""))

        assert semantic.document_profile.executive_summary is None

    def test_summary_collapses_internal_whitespace(self, extractor):
        version = _version()
        raw = _extraction(text="line one\n\n  line   two   with\tspaces")

        semantic = extractor.extract(version=version, raw_extraction=raw)

        assert semantic.document_profile.executive_summary == "line one line two with spaces"

    def test_summary_truncates_at_280_chars(self, extractor):
        version = _version()
        raw = _extraction(text="a" * 500)

        semantic = extractor.extract(version=version, raw_extraction=raw)

        assert len(semantic.document_profile.executive_summary) == 280


class TestSemanticExtractorWarnings:
    def test_propagates_raw_warnings(self, extractor):
        version = _version()
        raw = _extraction(warnings=["No non-empty text lines were extracted."])

        semantic = extractor.extract(version=version, raw_extraction=raw)

        assert "No non-empty text lines were extracted." in semantic.warnings

    def test_warns_when_a_section_has_no_source_lineage(self, extractor):
        version = _version()
        raw = _extraction(
            sections=[
                {"id": "s1", "heading": "h", "text": "t", "source_reference_ids": []},
            ],
        )

        semantic = extractor.extract(version=version, raw_extraction=raw)

        assert any("missing source lineage" in w for w in semantic.warnings)

    def test_no_lineage_warning_when_all_sections_carry_refs(self, extractor):
        version = _version()
        ref = SourceReference(document_version_id=version.id, section_id="s1", snippet="t")
        raw = _extraction(
            sections=[
                {
                    "id": "s1",
                    "heading": "h",
                    "text": "t",
                    "source_reference_ids": [ref.id],
                }
            ],
            source_refs=[ref],
        )

        semantic = extractor.extract(version=version, raw_extraction=raw)

        assert all("missing source lineage" not in w for w in semantic.warnings)


class TestSemanticExtractorOutput:
    def test_default_validation_status_is_needs_review(self, extractor):
        semantic = extractor.extract(version=_version(), raw_extraction=_extraction())

        assert semantic.validation_status == "needs_review"

    def test_assets_default_to_empty(self, extractor):
        semantic = extractor.extract(version=_version(), raw_extraction=_extraction())

        assert semantic.assets == []

    def test_source_references_serialize_to_dicts(self, extractor):
        version = _version()
        ref = SourceReference(
            document_version_id=version.id,
            section_id="s1",
            snippet="hello",
            line_start=1,
            line_end=1,
        )
        raw = _extraction(source_refs=[ref])

        semantic = extractor.extract(version=version, raw_extraction=raw)

        assert isinstance(semantic.source_references, list)
        assert all(isinstance(r, dict) for r in semantic.source_references)
        assert semantic.source_references[0]["snippet"] == "hello"
