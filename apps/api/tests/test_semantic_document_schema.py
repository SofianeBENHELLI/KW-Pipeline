import pytest
from pydantic import ValidationError

from app.schemas.semantic_document import SemanticAsset


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

