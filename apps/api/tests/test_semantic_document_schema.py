import pytest
from pydantic import ValidationError

from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticAsset,
    SemanticDocument,
    SemanticSection,
)


def test_source_backed_asset_requires_source_reference():
    with pytest.raises(ValidationError):
        SemanticAsset(
            type="requirement",
            text="All documents must be reviewed.",
            confidence=0.9,
            review_status="source_backed",
            source_reference_ids=[],
        )


def test_needs_review_asset_can_exist_without_source_reference():
    asset = SemanticAsset(
        type="open_question",
        text="Audience is unclear.",
        confidence=0.4,
        review_status="needs_review",
        source_reference_ids=[],
    )

    assert asset.review_status == "needs_review"


class TestSemanticAssetConfidenceBounds:
    @pytest.mark.parametrize("confidence", [-0.01, 1.01, 2.0, -1.0])
    def test_confidence_outside_zero_to_one_is_rejected(self, confidence):
        with pytest.raises(ValidationError):
            SemanticAsset(
                type="t",
                text="x",
                confidence=confidence,
                review_status="needs_review",
            )

    @pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0])
    def test_confidence_within_bounds_is_accepted(self, confidence):
        asset = SemanticAsset(
            type="t",
            text="x",
            confidence=confidence,
            review_status="needs_review",
        )
        assert asset.confidence == confidence


class TestSemanticAssetReviewStatusValues:
    @pytest.mark.parametrize("status", ["needs_review", "source_backed", "validated", "rejected"])
    def test_known_review_statuses_are_accepted(self, status):
        asset = SemanticAsset(
            type="t",
            text="x",
            confidence=0.5,
            review_status=status,
            source_reference_ids=["r1"] if status == "source_backed" else [],
        )
        assert asset.review_status == status

    def test_unknown_review_status_is_rejected(self):
        with pytest.raises(ValidationError):
            SemanticAsset(
                type="t",
                text="x",
                confidence=0.5,
                review_status="approved",  # not in the literal set
            )


class TestSemanticDocumentDefaults:
    def test_minimal_construction_uses_safe_defaults(self):
        doc = SemanticDocument(
            document_version_id="v1",
            document_profile=DocumentProfile(title="T"),
        )

        assert doc.validation_status == "needs_review"
        assert doc.schema_version == "v0.1"
        assert doc.assets == []
        assert doc.sections == []
        assert doc.warnings == []
        assert doc.markdown is None

    @pytest.mark.parametrize("status", ["needs_review", "validated", "rejected"])
    def test_known_validation_statuses_are_accepted(self, status):
        doc = SemanticDocument(
            document_version_id="v1",
            document_profile=DocumentProfile(title="T"),
            validation_status=status,
        )
        assert doc.validation_status == status

    def test_unknown_validation_status_is_rejected(self):
        with pytest.raises(ValidationError):
            SemanticDocument(
                document_version_id="v1",
                document_profile=DocumentProfile(title="T"),
                validation_status="pending",
            )


class TestSemanticSection:
    def test_default_source_reference_ids_is_empty(self):
        section = SemanticSection(id="s", heading="h", text="t")

        assert section.source_reference_ids == []

    def test_text_is_required(self):
        with pytest.raises(ValidationError):
            SemanticSection(id="s", heading="h")  # missing text


class TestDocumentProfile:
    def test_optional_fields_default_to_none(self):
        profile = DocumentProfile(title="T")

        assert profile.purpose is None
        assert profile.audience is None
        assert profile.executive_summary is None
        assert profile.document_type == "unknown"
