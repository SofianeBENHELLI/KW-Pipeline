"""Coverage for the PDF-viewer rect schema additions on
:class:`SourceReference` (Phase 0 of the PDF-viewer roadmap).

Validates that:

* :class:`NormalizedRect` enforces the [0, 1] / page>=1 invariants the
  viewer relies on for CSS percentage positioning.
* :class:`SourceReference.rects` is an optional list that defaults to
  empty (so legacy parser output keeps the same wire shape) and
  round-trips through ``model_dump_json`` / ``model_validate_json``.
* Legacy ``raw_extractions.payload`` JSON without a ``rects`` field
  still deserialises cleanly — important because no data migration
  runs over existing rows.
"""

import pytest
from pydantic import ValidationError

from app.schemas.extraction import (
    NormalizedRect,
    RawExtraction,
    RawSection,
    SourceReference,
)


def test_normalized_rect_accepts_valid_unit_coordinates():
    rect = NormalizedRect(page=1, x=0.0, y=0.0, width=1.0, height=1.0)
    assert (rect.x, rect.y, rect.width, rect.height) == (0.0, 0.0, 1.0, 1.0)


def test_normalized_rect_rejects_negative_or_oversized_coordinates():
    with pytest.raises(ValidationError):
        NormalizedRect(page=1, x=-0.01, y=0.0, width=0.5, height=0.5)
    with pytest.raises(ValidationError):
        NormalizedRect(page=1, x=0.0, y=0.0, width=1.01, height=0.5)


def test_normalized_rect_rejects_zero_area():
    # Width and height are gt=0 because a zero-area highlight is
    # useless to render and almost always a bug at the parser level.
    with pytest.raises(ValidationError):
        NormalizedRect(page=1, x=0.0, y=0.0, width=0.0, height=0.5)


def test_normalized_rect_rejects_page_zero_or_negative():
    with pytest.raises(ValidationError):
        NormalizedRect(page=0, x=0.0, y=0.0, width=0.5, height=0.5)


def test_source_reference_rects_default_to_empty_list():
    ref = SourceReference(
        document_version_id="v1",
        section_id="page-1",
        snippet="hello",
    )
    assert ref.rects == []


def test_source_reference_with_rects_round_trips_through_json():
    ref = SourceReference(
        document_version_id="v1",
        section_id="page-1-sec-2",
        page_number=1,
        snippet="multi-line chunk",
        rects=[
            NormalizedRect(page=1, x=0.1, y=0.2, width=0.5, height=0.03),
            NormalizedRect(page=1, x=0.1, y=0.24, width=0.4, height=0.03),
        ],
    )

    payload = ref.model_dump_json()
    restored = SourceReference.model_validate_json(payload)

    assert restored.rects == ref.rects


def test_legacy_payload_without_rects_field_still_deserialises():
    # Mirrors the shape of v0.1 rows currently sitting in the
    # `raw_extractions.payload` JSON column.
    legacy_payload = (
        '{"id":"sr-1","document_version_id":"v1",'
        '"section_id":"page-1","page_number":1,'
        '"line_start":null,"line_end":null,"snippet":"x"}'
    )
    restored = SourceReference.model_validate_json(legacy_payload)
    assert restored.rects == []


def test_raw_extraction_round_trips_with_mixed_rect_payload():
    rect = NormalizedRect(page=2, x=0.05, y=0.1, width=0.9, height=0.02)
    rich_ref = SourceReference(
        document_version_id="v1",
        section_id="page-2-sec-1",
        page_number=2,
        snippet="rich",
        rects=[rect],
    )
    plain_ref = SourceReference(
        document_version_id="v1",
        section_id="page-1",
        page_number=1,
        snippet="plain",
    )

    extraction = RawExtraction(
        document_version_id="v1",
        parser_name="pdf",
        parser_version="0.2",
        text="rich\nplain",
        sections=[
            RawSection(
                id="page-2-sec-1",
                heading="Page 2",
                text="rich",
                source_reference_ids=[rich_ref.id],
            ),
            RawSection(
                id="page-1",
                heading="Page 1",
                text="plain",
                source_reference_ids=[plain_ref.id],
            ),
        ],
        source_references=[rich_ref, plain_ref],
    )

    restored = RawExtraction.model_validate_json(extraction.model_dump_json())

    rich_restored = next(r for r in restored.source_references if r.section_id == "page-2-sec-1")
    plain_restored = next(r for r in restored.source_references if r.section_id == "page-1")
    assert rich_restored.rects == [rect]
    assert plain_restored.rects == []
