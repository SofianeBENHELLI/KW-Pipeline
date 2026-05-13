/**
 * Absolute-positioned overlay rendered on top of one PDF page canvas.
 *
 * Highlights are positioned in CSS percentages drawn from the
 * backend-normalised :class:`NormalizedRect` values, so zoom and resize
 * stay aligned without a JS reflow per frame. Every rect is rendered
 * as a clickable div carrying ``data-chunk-id`` so the click handler
 * in :class:`PdfChunkViewer` can fire the selection event without
 * per-rect props plumbing.
 *
 * Visual variants:
 * - selected: high-opacity stroke, the currently-active chunk
 * - hovered: ghost-fill, the side-panel row under the user's cursor
 * - ai: AI-derived (claim/topic/entity citation)
 * - parser: parser-derived (no LLM signal)
 *
 * No PDF.js dependency at this layer — it knows only rects, percentages,
 * and CSS classes. The parent wires the renderer.
 */

import { useMemo } from "react";

import type { ChunkLocation, ChunkSource, NormalizedRect } from "./types";

interface HighlightLayerProps {
  readonly pageNumber: number;
  readonly chunks: ChunkLocation[];
  readonly selectedChunkId: string | null;
  readonly hoveredChunkId: string | null;
  /**
   * Multi-chunk hover set. When provided, every chunk whose id is in
   * the set renders with the "hovered" visual — useful for the
   * Knowledge Forge cross-highlight where hovering one Topic / Entity
   * card on the right pane should light up *all* of that object's
   * supporting chunks in the PDF. Coexists with the singleton
   * ``hoveredChunkId``: a rect is considered hovered when **either**
   * matches, so the internal pointer-hover on the rect itself keeps
   * working while an external set drives multi-rect highlight.
   */
  readonly hoveredChunkIds?: ReadonlySet<string> | null;
  /**
   * Multi-chunk selection set. Same precedence rules as
   * ``hoveredChunkIds`` — coexists with the singleton form so an
   * external "selected topic" can light up its rects without
   * disrupting the rect-click flow.
   */
  readonly selectedChunkIds?: ReadonlySet<string> | null;
  readonly onSelectChunk: (chunkId: string) => void;
  readonly onHoverChunk: (chunkId: string | null) => void;
}

interface RectWithChunk {
  readonly chunkId: string;
  readonly source: ChunkSource;
  readonly rect: NormalizedRect;
  readonly summary: string;
}

function _collectRectsForPage(
  pageNumber: number,
  chunks: ChunkLocation[],
): RectWithChunk[] {
  const out: RectWithChunk[] = [];
  for (const chunk of chunks) {
    for (const rect of chunk.rects) {
      if (rect.page === pageNumber) {
        out.push({
          chunkId: chunk.chunk_id,
          source: chunk.source,
          rect,
          summary: chunk.summary ?? chunk.heading,
        });
      }
    }
  }
  return out;
}

export function HighlightLayer({
  pageNumber,
  chunks,
  selectedChunkId,
  hoveredChunkId,
  hoveredChunkIds,
  selectedChunkIds,
  onSelectChunk,
  onHoverChunk,
}: HighlightLayerProps) {
  const rects = useMemo(() => _collectRectsForPage(pageNumber, chunks), [
    pageNumber,
    chunks,
  ]);

  return (
    <div
      className="pdf-highlight-layer"
      aria-hidden
      // Absolute-position over the page canvas; pointer events flow
      // to individual rects so clicking outside any rect falls back
      // to PDF.js text selection.
      style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
    >
      {rects.map(({ chunkId, source, rect, summary }, index) => {
        // Distinguish singleton hover (pointer is over THIS rect) from
        // set-based "cross-highlight" (the right pane lit up N rects
        // belonging to the hovered Topic / Entity). Both share the
        // visual ring, but only the singleton reveals the floating
        // summary tooltip — otherwise hovering a Topic that owns 17
        // chunks shows 17 overlapping tooltips at once.
        const isPointerHovered = chunkId === hoveredChunkId;
        const isCrossHighlit = hoveredChunkIds?.has(chunkId) === true;
        const isSelected =
          chunkId === selectedChunkId ||
          selectedChunkIds?.has(chunkId) === true;
        const classes = [
          "pdf-highlight",
          source === "ai_extraction" ? "is-ai" : "is-parser",
          isSelected ? "is-selected" : "",
          isPointerHovered ? "is-hovered" : "",
          isCrossHighlit ? "is-cross-hl" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <button
            key={`${chunkId}-${index}`}
            type="button"
            className={classes}
            data-chunk-id={chunkId}
            aria-label={`Highlight ${summary}`}
            onClick={(e) => {
              e.stopPropagation();
              onSelectChunk(chunkId);
            }}
            onMouseEnter={() => onHoverChunk(chunkId)}
            onMouseLeave={() => onHoverChunk(null)}
            onFocus={() => onHoverChunk(chunkId)}
            onBlur={() => onHoverChunk(null)}
            style={{
              position: "absolute",
              left: `${rect.x * 100}%`,
              top: `${rect.y * 100}%`,
              width: `${rect.width * 100}%`,
              height: `${rect.height * 100}%`,
              pointerEvents: "auto",
            }}
          >
            {/* Hover preview — CSS-driven floating card showing the
                chunk summary. ``pdf-highlight-tip`` is hidden by
                default and revealed via :hover / :focus in the CSS,
                so there is no JS state to coordinate. The text mirrors
                what the side panel shows so the user sees the same
                signal in either pane. */}
            <span className="pdf-highlight-tip" role="tooltip">
              {summary}
            </span>
          </button>
        );
      })}
    </div>
  );
}
