import logging

import pytest

from app.models.document import DocumentVersionStatus
from app.schemas.document import DocumentVersion
from app.schemas.extraction import RawExtraction, SourceReference
from app.schemas.semantic_document import SemanticAsset
from app.services.enrichers import NoOpEnricher, SemanticEnricher
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


class _StubEnricher:
    """Test enricher that returns whatever it was constructed with."""

    def __init__(self, name: str, results: list) -> None:
        self.name = name
        self._results = results
        self.calls: list[tuple] = []

    def enrich(self, raw_extraction, existing_assets):
        self.calls.append((raw_extraction, list(existing_assets)))
        return list(self._results)


class _RaisingEnricher:
    name = "boom"

    def enrich(self, raw_extraction, existing_assets):
        raise RuntimeError("simulated provider failure")


class TestSemanticEnricherProtocol:
    def test_noop_enricher_is_runtime_protocol_member(self):
        assert isinstance(NoOpEnricher(), SemanticEnricher)

    def test_noop_enricher_returns_no_assets(self):
        version = _version()
        raw = _extraction()
        extractor = SemanticExtractor(enrichers=[NoOpEnricher()])

        semantic = extractor.extract(version=version, raw_extraction=raw)

        assert semantic.assets == []

    def test_no_enrichers_means_no_extra_assets(self):
        version = _version()
        raw = _extraction()
        extractor = SemanticExtractor(enrichers=[])

        semantic = extractor.extract(version=version, raw_extraction=raw)

        assert semantic.assets == []

    def test_default_constructor_has_no_enrichers(self):
        # Backwards compatibility: existing call sites with no kwargs still work.
        version = _version()
        raw = _extraction()
        extractor = SemanticExtractor()

        semantic = extractor.extract(version=version, raw_extraction=raw)

        assert semantic.assets == []


class TestSemanticEnricherBoundary:
    def test_valid_asset_has_review_status_forced_to_needs_review(self):
        version = _version()
        ref = SourceReference(document_version_id=version.id, section_id="s1", snippet="t")
        raw = _extraction(source_refs=[ref])
        # Enricher claims a fully source-backed asset; the boundary must
        # downgrade it to needs_review regardless.
        claimed = SemanticAsset(
            type="claim",
            text="LLM-extracted claim",
            confidence=0.9,
            review_status="source_backed",
            source_reference_ids=[ref.id],
        )
        enricher = _StubEnricher(name="stub", results=[claimed])
        extractor = SemanticExtractor(enrichers=[enricher])

        semantic = extractor.extract(version=version, raw_extraction=raw)

        assert len(semantic.assets) == 1
        produced = semantic.assets[0]
        assert produced.text == "LLM-extracted claim"
        assert produced.review_status == "needs_review"

    def test_invalid_asset_is_dropped_and_logged(self, caplog):
        version = _version()
        valid = SemanticAsset(type="claim", text="ok", confidence=0.5)
        # confidence=2.0 violates the [0, 1] constraint on SemanticAsset.
        invalid = {"type": "claim", "text": "bad", "confidence": 2.0}
        enricher = _StubEnricher(name="lossy", results=[invalid, valid])
        extractor = SemanticExtractor(enrichers=[enricher])

        with caplog.at_level(logging.WARNING, logger="app.services.semantic_extractor"):
            semantic = extractor.extract(version=version, raw_extraction=_extraction())

        # Only the valid asset survives; pipeline keeps going.
        assert len(semantic.assets) == 1
        assert semantic.assets[0].text == "ok"
        assert semantic.assets[0].review_status == "needs_review"
        # And the drop was logged with the enricher's name.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("lossy" in r.getMessage() for r in warnings)

    def test_raising_enricher_is_logged_and_skipped(self, caplog):
        version = _version()
        ok_asset = SemanticAsset(type="claim", text="kept", confidence=0.4)
        raiser = _RaisingEnricher()
        survivor = _StubEnricher(name="survivor", results=[ok_asset])
        extractor = SemanticExtractor(enrichers=[raiser, survivor])

        with caplog.at_level(logging.ERROR, logger="app.services.semantic_extractor"):
            semantic = extractor.extract(version=version, raw_extraction=_extraction())

        # The raising enricher contributed nothing, but the next one ran.
        assert len(semantic.assets) == 1
        assert semantic.assets[0].text == "kept"
        # The exception was logged via logger.exception.
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("boom" in r.getMessage() for r in errors)
        assert any(r.exc_info for r in errors)
        # And the raiser's failure didn't poison the surviving enricher's input.
        assert survivor.calls and survivor.calls[0][1] == []

    def test_two_enrichers_run_in_registration_order(self):
        version = _version()
        first_asset = SemanticAsset(type="claim", text="first", confidence=0.1)
        second_asset = SemanticAsset(type="claim", text="second", confidence=0.2)
        first = _StubEnricher(name="first", results=[first_asset])
        second = _StubEnricher(name="second", results=[second_asset])
        extractor = SemanticExtractor(enrichers=[first, second])

        semantic = extractor.extract(version=version, raw_extraction=_extraction())

        assert [a.text for a in semantic.assets] == ["first", "second"]
        # The second enricher saw the first one's output as `existing_assets`.
        assert second.calls[0][1][0].text == "first"
