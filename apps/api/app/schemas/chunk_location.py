"""Read-model for the PDF-viewer chunk-locations route.

Returned by ``GET /documents/{id}/versions/{v}/chunks`` (operation id
``list_document_chunks``). One :class:`ChunkLocation` per parser-emitted
section, carrying the normalised page rectangles the viewer overlays on
top of EmbedPDF plus a short summary surface drawn from any LLM
artefacts that cite the chunk.

This shape is deliberately read-only and denormalised: the viewer
fetches it once per opened document, indexes by ``chunk_id``, and
renders both panels (PDF overlay, side list) from a single payload —
no second round-trip for hover or click events.

Field-by-field intent:

* ``chunk_id`` mirrors :class:`RawSection.id` so the side-panel /
  overlay handshake travels on a stable identity that survives
  re-fetches and persists across viewer sessions.
* ``document_hash`` is the SHA-256 of the original bytes (the same
  field gating the duplicate-detection FSM). The viewer asserts
  equality against the version row it loaded before drawing rects —
  any mismatch means "this PDF is not the one these rects were
  computed against" and the highlight layer refuses to render.
* ``rects`` is a flat list spanning whichever pages the chunk
  touches; each :class:`NormalizedRect` already carries its own
  ``page`` so multi-page chunks render correctly without grouping.
* ``source = "ai_extraction"`` whenever any document-topic (or, in a
  later phase, any claim/entity) cites the chunk; otherwise the
  chunk is parser-only (no LLM signal) and ``source = "parser"``.
  This lets the side panel render distinct affordances for "this
  chunk is summarised by AI" vs "this chunk has only the raw
  parser-extracted text".
* ``pipeline_version`` is a compact concatenation of parser version
  + extractor versions (e.g. ``"parser=0.2;topic=v0.1"``) for the
  traceability requirement: every highlight knows which pipeline
  version produced it.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel
from app.schemas.extraction import NormalizedRect

CHUNK_LOCATION_SCHEMA_VERSION: Final[Literal["v0.1"]] = "v0.1"

# Hard ceiling per request — the viewer fetches all of a document's
# chunks at once, but a single PDF should not blow past this. Bigger
# documents typically split per page; the cap is there to bound the
# JSON envelope. The route returns 400 if the caller asks for more.
MAX_CHUNK_LOCATIONS_LIMIT: Final[int] = 2000


ChunkSource = Literal["ai_extraction", "parser"]


class ChunkLocation(BaseModel):
    """One row in the chunk-locations payload."""

    chunk_id: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1, max_length=200)
    document_version_id: str = Field(min_length=1, max_length=200)
    document_hash: str = Field(min_length=1, max_length=128)
    page: int = Field(ge=1)
    rects: list[NormalizedRect] = Field(default_factory=list)
    heading: str = Field(max_length=400)
    snippet: str = Field(max_length=2000)
    summary: str | None = Field(default=None, max_length=2000)
    topic_id: str | None = Field(default=None, max_length=200)
    topic_label: str | None = Field(default=None, max_length=400)
    source: ChunkSource
    confidence: float = Field(ge=0.0, le=1.0)
    pipeline_version: str = Field(min_length=1, max_length=200)


class ChunkLocationsResponse(BaseModel):
    """Envelope returned by ``list_document_chunks``.

    ``parser_version`` is duplicated at the envelope level so the
    viewer can gate rect-level rendering on it without inspecting
    every item — pre-0.2 versions ship with empty ``rects`` lists.
    """

    schema_version: Literal["v0.1"] = CHUNK_LOCATION_SCHEMA_VERSION
    document_id: str
    document_version_id: str
    document_hash: str
    parser_version: str
    items: list[ChunkLocation] = Field(default_factory=list)


__all__ = [
    "CHUNK_LOCATION_SCHEMA_VERSION",
    "MAX_CHUNK_LOCATIONS_LIMIT",
    "ChunkLocation",
    "ChunkLocationsResponse",
    "ChunkSource",
]
