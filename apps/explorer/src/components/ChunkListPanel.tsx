/**
 * Chunks list pane — replaces the page-cards renderer in the right
 * column of Knowledge Explorer. Lists every chunk that belongs to
 * the open document so the operator can:
 *
 * 1. **See the chunks at a glance** — a denser list than the
 *    paragraph-card stack the old viewer rendered. No noise from
 *    page boundaries; just the chunks the graph already knows
 *    about.
 * 2. **Cross-highlight with the graph in both directions**:
 *    - Hover / click a chunk row → the graph node lights up via
 *      ``onHover`` / ``onSelectChunk``.
 *    - Hover / click a chunk node in the graph → the matching row
 *      lights up via the ``highlightChunkId`` and ``hoveredId``
 *      props the host (``App.tsx``) already maintains.
 *
 * Scope: this is the synthetic-sample + post-upload pre-PDF render
 * path. Real backend PDFs render through ``PdfChunkViewer`` which
 * has its own chunk side panel; that path is unchanged.
 */

import React from "react";

import { Icon } from "./icons";
import {
  chunksForDoc,
  type ExplorerChunk,
  type ExplorerDocument,
  type ExplorerSnapshot,
} from "../state/explorer-data";

interface ChunkListPanelProps {
  readonly snapshot: ExplorerSnapshot;
  readonly doc: ExplorerDocument | null;
  /** The single chunk currently "pinned" by the operator (graph
   *  selection / page navigator / arrow keys). Drives the
   *  ``kx-chunk-row--sel`` visual on the matching row. */
  readonly highlightChunkId: string | null;
  /** Transient graph-hover id. When non-null, the matching row
   *  picks up ``kx-chunk-row--hover`` so the operator can see the
   *  graph → list direction of the cross-highlight. */
  readonly hoveredChunkId: string | null;
  /** Click → set the pinned chunk. The host wires this to the
   *  same ``setHighlightChunk`` it already uses; the graph
   *  selection follows via the App's existing ``selectById``
   *  path so the cross-highlight is symmetric. */
  readonly onSelectChunk: (chunkId: string) => void;
  /** Hover → set the transient graph hover. ``null`` on
   *  pointer-leave restores the unhovered visual. */
  readonly onHoverChunk: (chunkId: string | null) => void;
}

export const ChunkListPanel: React.FC<ChunkListPanelProps> = ({
  snapshot,
  doc,
  highlightChunkId,
  hoveredChunkId,
  onSelectChunk,
  onHoverChunk,
}) => {
  if (!doc) {
    return (
      <div className="kx-chunklist-empty">
        <Icon name="doc" size={28} />
        <div className="kx-chunklist-empty-t">No document open</div>
        <div className="kx-chunklist-empty-s">
          Click a document or chunk in the graph to scope the list to its
          chunks.
        </div>
      </div>
    );
  }

  const chunks = chunksForDoc(snapshot, doc.id);
  if (chunks.length === 0) {
    return (
      <div className="kx-chunklist-empty">
        <Icon name="doc" size={28} />
        <div className="kx-chunklist-empty-t">{doc.title}</div>
        <div className="kx-chunklist-empty-s">
          No chunks yet — extraction has not produced any chunks for this
          version. The graph will pick them up after the next projection
          run.
        </div>
      </div>
    );
  }

  return (
    <section
      className="kx-chunklist"
      aria-label={`Chunks of ${doc.title}`}
      onMouseLeave={() => onHoverChunk(null)}
    >
      <header className="kx-chunklist-head">
        <div className="kx-chunklist-title" title={doc.title}>
          {doc.title}
        </div>
        <div className="kx-chunklist-sub">
          {doc.source} · {chunks.length}{" "}
          {chunks.length === 1 ? "chunk" : "chunks"}
        </div>
      </header>
      <ul className="kx-chunklist-rows" role="listbox">
        {chunks.map((c) => (
          <ChunkRow
            key={c.id}
            chunk={c}
            isSelected={c.id === highlightChunkId}
            isHovered={c.id === hoveredChunkId && c.id !== highlightChunkId}
            onSelect={() => onSelectChunk(c.id)}
            onHover={(hovering) => onHoverChunk(hovering ? c.id : null)}
          />
        ))}
      </ul>
    </section>
  );
};

interface ChunkRowProps {
  readonly chunk: ExplorerChunk;
  readonly isSelected: boolean;
  readonly isHovered: boolean;
  readonly onSelect: () => void;
  readonly onHover: (hovering: boolean) => void;
}

const ChunkRow: React.FC<ChunkRowProps> = ({
  chunk,
  isSelected,
  isHovered,
  onSelect,
  onHover,
}) => {
  // Scroll-into-view on selection so a graph click reveals the row
  // even when the list is scrolled past it. Plain block alignment
  // (no smooth) — the host pin → list reveal is expected to feel
  // instant, not animated.
  const ref = React.useRef<HTMLLIElement | null>(null);
  React.useEffect(() => {
    if (!isSelected || !ref.current) return;
    ref.current.scrollIntoView({ block: "nearest" });
  }, [isSelected]);

  const className = [
    "kx-chunk-row",
    isSelected ? "kx-chunk-row--sel" : "",
    isHovered ? "kx-chunk-row--hover" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <li
      ref={ref}
      className={className}
      role="option"
      aria-selected={isSelected}
      tabIndex={0}
      onClick={onSelect}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      onFocus={() => onHover(true)}
      onBlur={() => onHover(false)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <div className="kx-chunk-row-h">
        <span className="kx-chunk-row-page">p. {chunk.page}</span>
        <span className="kx-chunk-row-label" title={chunk.label}>
          {chunk.label}
        </span>
        <span
          className="kx-chunk-row-conf"
          title={`Confidence ${chunk.confidence.toFixed(2)}`}
          aria-label={`Confidence ${chunk.confidence.toFixed(2)}`}
        >
          {chunk.confidence.toFixed(2)}
        </span>
      </div>
      {chunk.summary ? (
        <div className="kx-chunk-row-summary">{chunk.summary}</div>
      ) : null}
    </li>
  );
};
