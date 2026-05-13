/**
 * Right-side chunk list for the PDF viewer split pane.
 *
 * Lists every :class:`ChunkLocation` the consumer hands in, with
 * client-side filters (page, source, min confidence) and a free-text
 * search across heading and summary. Clicking a row promotes it to
 * the sticky selection state the PDF overlay shares; hovering a row
 * sets the transient "ghost-highlight" state.
 *
 * Zero runtime dependencies beyond React: icons are inline SVGs so
 * the shared module stays installable across Orbital (Vite), Explorer
 * (Webpack), and future frontends without hoisting an icon library.
 * No virtualization library either — ``content-visibility: auto`` on
 * the row CSS class handles scroll-perf on multi-hundred-row docs.
 */

import { useMemo, useState } from "react";

import type { ChunkLocation, ChunkSource } from "./types";

// Inline 14px stroke icons in the lucide visual idiom. Inlined rather
// than imported so the shared module has no runtime dep on
// ``lucide-react``; each consuming app keeps lucide for the rest of
// its UI and pays no extra cost here.
const _SEARCH_ICON = (
  <svg
    width={14}
    height={14}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden
  >
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </svg>
);

const _SPARKLES_ICON = (
  <svg
    width={14}
    height={14}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden
  >
    <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
  </svg>
);

const _FILE_TEXT_ICON = (
  <svg
    width={14}
    height={14}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden
  >
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <path d="M14 2v6h6" />
    <path d="M8 13h8M8 17h5" />
  </svg>
);

interface ChunkSidePanelProps {
  readonly chunks: ChunkLocation[];
  readonly selectedChunkId: string | null;
  readonly hoveredChunkId: string | null;
  readonly onSelectChunk: (chunkId: string) => void;
  readonly onHoverChunk: (chunkId: string | null) => void;
}

interface Filters {
  page: number | null;
  source: ChunkSource | null;
  minConfidence: number;
  query: string;
}

const _INITIAL_FILTERS: Filters = {
  page: null,
  source: null,
  minConfidence: 0,
  query: "",
};

function _matches(chunk: ChunkLocation, filters: Filters): boolean {
  if (filters.page !== null && chunk.page !== filters.page) return false;
  if (filters.source !== null && chunk.source !== filters.source) return false;
  if (chunk.confidence < filters.minConfidence) return false;
  if (filters.query) {
    const needle = filters.query.trim().toLowerCase();
    if (needle) {
      const haystack =
        `${chunk.heading} ${chunk.summary ?? ""} ${chunk.snippet}`.toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
  }
  return true;
}

export function ChunkSidePanel({
  chunks,
  selectedChunkId,
  hoveredChunkId,
  onSelectChunk,
  onHoverChunk,
}: ChunkSidePanelProps) {
  const [filters, setFilters] = useState<Filters>(_INITIAL_FILTERS);

  const pages = useMemo(() => {
    const distinct = new Set(chunks.map((c) => c.page));
    return [...distinct].sort((a, b) => a - b);
  }, [chunks]);

  const visible = useMemo(
    () => chunks.filter((chunk) => _matches(chunk, filters)),
    [chunks, filters],
  );

  return (
    <aside className="pdf-side-panel" aria-label="Document chunks">
      <header className="pdf-side-panel-head">
        <div className="pdf-side-panel-search">
          {_SEARCH_ICON}
          <input
            type="search"
            placeholder="Search chunks…"
            value={filters.query}
            onChange={(e) => setFilters((f) => ({ ...f, query: e.target.value }))}
            aria-label="Search chunks"
          />
        </div>
        <div className="pdf-side-panel-filters">
          <label className="pdf-side-panel-filter">
            <span>Page</span>
            <select
              value={filters.page ?? ""}
              onChange={(e) =>
                setFilters((f) => ({
                  ...f,
                  page: e.target.value === "" ? null : Number(e.target.value),
                }))
              }
              aria-label="Filter by page"
            >
              <option value="">All</option>
              {pages.map((p) => (
                <option key={p} value={p}>
                  Page {p}
                </option>
              ))}
            </select>
          </label>
          <label className="pdf-side-panel-filter">
            <span>Source</span>
            <select
              value={filters.source ?? ""}
              onChange={(e) =>
                setFilters((f) => ({
                  ...f,
                  source: (e.target.value || null) as ChunkSource | null,
                }))
              }
              aria-label="Filter by source"
            >
              <option value="">All</option>
              <option value="ai_extraction">AI extraction</option>
              <option value="parser">Parser only</option>
            </select>
          </label>
          <label className="pdf-side-panel-filter">
            <span>Min confidence</span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={filters.minConfidence}
              onChange={(e) =>
                setFilters((f) => ({ ...f, minConfidence: Number(e.target.value) }))
              }
              aria-label="Minimum confidence"
            />
            <span className="pdf-side-panel-conf">
              {(filters.minConfidence * 100).toFixed(0)}%
            </span>
          </label>
        </div>
        <p className="pdf-side-panel-count" aria-live="polite">
          {visible.length} of {chunks.length} chunks
        </p>
      </header>
      <ul className="pdf-side-panel-list" role="listbox">
        {visible.map((chunk) => {
          const isSelected = chunk.chunk_id === selectedChunkId;
          const isHovered = chunk.chunk_id === hoveredChunkId;
          const classes = [
            "pdf-side-panel-row",
            isSelected ? "is-selected" : "",
            isHovered ? "is-hovered" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <li
              key={chunk.chunk_id}
              className={classes}
              role="option"
              aria-selected={isSelected}
              tabIndex={0}
              onClick={() => onSelectChunk(chunk.chunk_id)}
              onMouseEnter={() => onHoverChunk(chunk.chunk_id)}
              onMouseLeave={() => onHoverChunk(null)}
              onFocus={() => onHoverChunk(chunk.chunk_id)}
              onBlur={() => onHoverChunk(null)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelectChunk(chunk.chunk_id);
                }
              }}
            >
              <div className="pdf-side-panel-row-head">
                <span className="pdf-side-panel-row-icon" aria-hidden>
                  {chunk.source === "ai_extraction"
                    ? _SPARKLES_ICON
                    : _FILE_TEXT_ICON}
                </span>
                <span className="pdf-side-panel-row-title">{chunk.heading}</span>
                <span className="pdf-side-panel-row-page">p. {chunk.page}</span>
              </div>
              <p className="pdf-side-panel-row-summary">
                {chunk.summary ?? chunk.snippet}
              </p>
              <footer className="pdf-side-panel-row-meta">
                <span
                  className={
                    chunk.source === "ai_extraction"
                      ? "pdf-side-panel-tag is-ai"
                      : "pdf-side-panel-tag is-parser"
                  }
                >
                  {chunk.source === "ai_extraction" ? "AI" : "Parser"}
                </span>
                <span className="pdf-side-panel-tag is-conf">
                  {(chunk.confidence * 100).toFixed(0)}%
                </span>
                {chunk.topic_label ? (
                  <span className="pdf-side-panel-tag is-topic" title="Topic">
                    {chunk.topic_label}
                  </span>
                ) : null}
              </footer>
            </li>
          );
        })}
        {visible.length === 0 ? (
          <li className="pdf-side-panel-empty" role="status">
            No chunks match the current filters.
          </li>
        ) : null}
      </ul>
    </aside>
  );
}
