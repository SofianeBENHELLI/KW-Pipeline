"""PDF parser backed by pdfplumber with line-level chunks + rects.

Each page is decomposed into one or more :class:`RawSection` instances,
where a section is a heading-run of consecutive lines separated from
neighbours by a paragraph-sized y-gap. Every section's
:class:`SourceReference` carries the per-line bounding boxes as
:class:`NormalizedRect` values (normalised to ``[0, 1]`` against the
rendered page size, top-left origin) so the PDF viewer can draw
overlay highlights with native CSS positioning — see the PDF viewer
plan (Phase 1) for the consumer contract.

Tables on a page emit their own section after the text sections so
reviewers see narrative first, structured data second; each table
section carries one rect covering the full table bbox.

This shape is ``parser_version = "0.2"``. Legacy rows persisted under
``parser_version = "0.1"`` continue to deserialise (empty
``rects``, ``section_id`` shape ``page-N``); they can be opted into
the new layout via the backfill CLI shipped in Phase 5.

pdfplumber is MIT-licensed; see docs/adr/ADR-010-pdf-parser.md for the
rationale behind it over Docling for the MVP.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from statistics import median
from typing import Any

import pdfplumber

from app.schemas.document import DocumentVersion
from app.schemas.extraction import (
    NormalizedRect,
    RawExtraction,
    RawSection,
    SourceReference,
)
from app.services.storage_service import StorageService

log = logging.getLogger(__name__)

PDF_CONTENT_TYPE = "application/pdf"

# A y-gap exceeding this many median line-heights starts a new section.
# 1.5 catches paragraph breaks without splitting normal line spacing.
_SECTION_GAP_MULTIPLIER = 1.5

# Cap on heading length. Anything longer is almost always body prose.
_HEADING_MAX_CHARS = 80

# Font-size ratio above page median for a line to qualify as a heading.
_HEADING_FONT_RATIO = 1.15

# Defensive lower bound for normalised width/height. The Pydantic
# validator requires ``gt=0``; a normalised value smaller than this
# is almost certainly a parse artefact (a stray descender, a
# zero-width glyph) and would never render visibly anyway.
_MIN_NORMALISED_DIMENSION = 1e-4


def _table_to_text(table: list[list[str | None]]) -> str:
    return "\n".join("\t".join((cell or "") for cell in row) for row in table)


@dataclass(slots=True)
class _Line:
    """A pdfplumber line adapted to the fields the parser cares about."""

    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    font_size: float

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 0.0)


def _line_inside_any_bbox(
    line: _Line, bboxes: list[tuple[float, float, float, float]]
) -> bool:
    """Return True when the line's centre falls inside any table bbox.

    Used to drop lines that pdfplumber's line extractor picks up *inside*
    a detected table — those cells would otherwise be emitted twice (once
    as a text section, once as the table section). Centre-based test is
    robust to small bbox padding differences between the two extractors.
    """
    if not bboxes:
        return False
    cx = (line.x0 + line.x1) / 2.0
    cy = (line.top + line.bottom) / 2.0
    return any(x0 <= cx <= x1 and top <= cy <= bottom for (x0, top, x1, bottom) in bboxes)


def _line_from_pdfplumber(raw: dict[str, Any]) -> _Line:
    chars = raw.get("chars") or []
    sizes = [c.get("size") for c in chars if isinstance(c.get("size"), (int, float))]
    font_size = float(median(sizes)) if sizes else 0.0
    return _Line(
        text=str(raw.get("text", "")),
        x0=float(raw["x0"]),
        top=float(raw["top"]),
        x1=float(raw["x1"]),
        bottom=float(raw["bottom"]),
        font_size=font_size,
    )


def _group_lines_into_sections(lines: list[_Line]) -> list[list[_Line]]:
    """Break a page's lines into sections at paragraph y-gaps.

    The threshold is derived per page from the median line height so the
    heuristic adapts to the document's actual leading rather than a
    hard-coded pt value.
    """
    if not lines:
        return []
    heights = [line.height for line in lines if line.height > 0]
    median_height = float(median(heights)) if heights else 0.0
    threshold = median_height * _SECTION_GAP_MULTIPLIER if median_height else None

    groups: list[list[_Line]] = [[lines[0]]]
    for prev, curr in zip(lines, lines[1:], strict=False):
        gap = curr.top - prev.bottom
        if threshold is not None and gap > threshold:
            groups.append([curr])
        else:
            groups[-1].append(curr)
    return groups


def _pick_heading(group: list[_Line], page_median_font: float, default: str) -> str:
    """Promote the group's first line to heading when it looks like one.

    Falls back to ``default`` when no font signal is available or the
    candidate line is too long to be a section title.
    """
    if not group:
        return default
    first = group[0]
    is_short = len(first.text.strip()) <= _HEADING_MAX_CHARS
    is_large = page_median_font > 0 and first.font_size > page_median_font * _HEADING_FONT_RATIO
    if is_short and is_large:
        stripped = first.text.strip()
        if stripped:
            return stripped
    return default


def _normalize_rect(
    *,
    x0: float,
    top: float,
    x1: float,
    bottom: float,
    page_w: float,
    page_h: float,
    page_number: int,
) -> NormalizedRect | None:
    """Map a pdfplumber bbox into ``[0, 1]`` with top-left origin.

    Returns ``None`` when the bbox would normalise to a zero-area rect
    or when page dimensions are missing — both cases mean the rect is
    not renderable and the caller should skip it.
    """
    if page_w <= 0 or page_h <= 0:
        return None
    width = (x1 - x0) / page_w
    height = (bottom - top) / page_h
    if width <= 0 or height <= 0:
        return None
    return NormalizedRect(
        page=page_number,
        x=min(max(x0 / page_w, 0.0), 1.0),
        y=min(max(top / page_h, 0.0), 1.0),
        width=min(max(width, _MIN_NORMALISED_DIMENSION), 1.0),
        height=min(max(height, _MIN_NORMALISED_DIMENSION), 1.0),
    )


class PdfParser:
    """Parser for ``application/pdf`` emitting line-level chunks + rects.

    Each PDF page splits into one or more text sections (heading-runs of
    consecutive lines) and optionally one section per detected table.
    Sections are emitted in reading order — page 1 text sections, page 1
    tables, page 2 text sections, etc. Every text section's
    ``SourceReference`` carries one :class:`NormalizedRect` per
    contributing line; table sections carry one rect for the whole
    table bbox.

    Empty pages (no extractable text and no tables) surface as a
    warning rather than raising; the lifecycle FSM is responsible for
    routing those to the reviewer. Scanned-image PDFs trip this path
    today — OCR is tracked in #47.

    pdfplumber respects ``page.rotation`` and ``page.cropbox``
    internally, so ``page.width`` / ``page.height`` and line
    coordinates are already in the visual (rendered) coordinate space
    — no extra rotation math is needed at this layer.
    """

    name = "pdf"
    # Bumped from 0.1 → 0.2 with the line-level + rect rewrite. Phase 5's
    # backfill CLI uses this to filter legacy rows for re-extraction.
    version = "0.2"
    supported_content_types: frozenset[str] = frozenset({PDF_CONTENT_TYPE})

    def parse(self, version: DocumentVersion, storage: StorageService) -> RawExtraction:
        content = storage.get(version.storage_uri)

        sections: list[RawSection] = []
        source_references: list[SourceReference] = []
        text_parts: list[str] = []
        warnings: list[str] = []
        empty_pages: list[int] = []

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page_index, page in enumerate(pdf.pages):
                page_number = page_index + 1
                page_w = float(page.width)
                page_h = float(page.height)

                raw_lines = page.extract_text_lines() or []
                lines = [_line_from_pdfplumber(raw) for raw in raw_lines]
                page_tables = list(page.find_tables() or [])

                # Drop lines that sit inside a detected table bbox so the
                # same content is not emitted twice (once as text, once as
                # the table section).
                # Construct fixed 4-tuples explicitly so the type matches
                # ``_line_inside_any_bbox``'s signature — a comprehension
                # over ``tuple(float(c) for c in t.bbox)`` produces a
                # variadic ``tuple[float, ...]``, which mypy refuses.
                table_bboxes: list[tuple[float, float, float, float]] = []
                for t in page_tables:
                    bbox = getattr(t, "bbox", None)
                    if not bbox or len(bbox) != 4:
                        continue
                    x0, y0, x1, y1 = bbox
                    table_bboxes.append(
                        (float(x0), float(y0), float(x1), float(y1))
                    )
                if table_bboxes:
                    lines = [
                        line
                        for line in lines
                        if not _line_inside_any_bbox(line, table_bboxes)
                    ]

                if lines:
                    page_font_sizes = [line.font_size for line in lines if line.font_size > 0]
                    page_median_font = float(median(page_font_sizes)) if page_font_sizes else 0.0
                    groups = _group_lines_into_sections(lines)
                    multiple_groups = len(groups) > 1

                    for sec_index, group in enumerate(groups):
                        section_id = f"page-{page_number}-sec-{sec_index}"
                        default_heading = (
                            f"Page {page_number} — Section {sec_index + 1}"
                            if multiple_groups
                            else f"Page {page_number}"
                        )
                        heading = _pick_heading(group, page_median_font, default_heading)
                        text = "\n".join(line.text for line in group)
                        rects = [
                            rect
                            for rect in (
                                _normalize_rect(
                                    x0=line.x0,
                                    top=line.top,
                                    x1=line.x1,
                                    bottom=line.bottom,
                                    page_w=page_w,
                                    page_h=page_h,
                                    page_number=page_number,
                                )
                                for line in group
                            )
                            if rect is not None
                        ]
                        reference = SourceReference(
                            document_version_id=version.id,
                            section_id=section_id,
                            page_number=page_number,
                            snippet=text[:240],
                            rects=rects,
                        )
                        section = RawSection(
                            id=section_id,
                            heading=heading,
                            text=text,
                            source_reference_ids=[reference.id],
                            page_number=page_number,
                            parser_metadata={
                                "page_number": str(page_number),
                                "section_index": str(sec_index),
                            },
                        )
                        sections.append(section)
                        source_references.append(reference)
                        text_parts.append(text)
                elif not page_tables:
                    empty_pages.append(page_number)

                for table_index, table in enumerate(page_tables):
                    rows = table.extract() or []
                    table_text = _table_to_text(rows)
                    if not table_text.strip():
                        continue
                    section_id = f"page-{page_number}-table-{table_index}"
                    table_rects: list[NormalizedRect] = []
                    bbox = getattr(table, "bbox", None)
                    if bbox is not None and len(bbox) == 4:
                        x0, top, x1, bottom = (float(coord) for coord in bbox)
                        rect = _normalize_rect(
                            x0=x0,
                            top=top,
                            x1=x1,
                            bottom=bottom,
                            page_w=page_w,
                            page_h=page_h,
                            page_number=page_number,
                        )
                        if rect is not None:
                            table_rects.append(rect)
                    reference = SourceReference(
                        document_version_id=version.id,
                        section_id=section_id,
                        page_number=page_number,
                        snippet=table_text[:240],
                        rects=table_rects,
                    )
                    section = RawSection(
                        id=section_id,
                        heading=f"Page {page_number} — Table {table_index + 1}",
                        text=table_text,
                        source_reference_ids=[reference.id],
                        page_number=page_number,
                        parser_metadata={
                            "page_number": str(page_number),
                            "table_index": str(table_index),
                        },
                    )
                    sections.append(section)
                    source_references.append(reference)
                    text_parts.append(table_text)

        if not sections:
            warnings.append("No text or tables extracted from PDF.")
        if empty_pages:
            warnings.append(
                f"Pages with no extractable text: {empty_pages}. "
                "If the PDF is a scanned image, OCR is required (#47)."
            )

        total_rects = sum(len(ref.rects) for ref in source_references)
        log.info(
            "pdf.parser.rects_emitted",
            extra={
                "document_version_id": version.id,
                "parser_version": self.version,
                "section_count": len(sections),
                "rect_count": total_rects,
                "empty_page_count": len(empty_pages),
            },
        )

        return RawExtraction(
            document_version_id=version.id,
            parser_name=self.name,
            parser_version=self.version,
            text="\n\n".join(text_parts),
            sections=sections,
            source_references=source_references,
            warnings=warnings,
        )
