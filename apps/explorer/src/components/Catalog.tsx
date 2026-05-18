/**
 * Knowledge Explorer "Catalog" tab — sortable table of every
 * document the user has access to.
 *
 * Columns: filename, type icon, status badge, version count, latest
 * version_number, ingested_at (relative). Filter chips for status,
 * filename text search. Cursor-paginated via the existing
 * ``/documents`` endpoint (same envelope the widget already
 * consumes).
 *
 * Selection delegates to the parent through ``onSelectDocument`` so
 * the existing DetailPanel renders the row's metadata with no new
 * data flow. The deep-link hash (``#catalog/<doc_id>``) is owned by
 * App.tsx — this component is otherwise stateless about routing.
 */

import React, { useEffect, useMemo, useState } from "react";

import { Icon } from "./icons";
import { useDocumentsCatalog } from "../state/use-documents-catalog";
import { DOC_TYPES, type DocTypeKey } from "../state/explorer-data";
import type { Document, DocumentVersionStatus } from "../api/types";

const SEARCH_DEBOUNCE_MS = 250;

// The full set of statuses the API exposes. We surface a small
// curated subset as filter chips — the long tail (HASHED, STORED,
// EXTRACTING, EXTRACTED, SEMANTIC_READY) is internal pipeline state
// that an operator-facing catalog rarely filters on. The ``All``
// chip means no ``?status=`` query param.
const STATUS_FILTERS: { id: string; label: string; statuses: DocumentVersionStatus[] }[] = [
  { id: "all", label: "All", statuses: [] },
  { id: "uploaded", label: "Uploaded", statuses: ["UPLOADED"] },
  { id: "review", label: "Needs review", statuses: ["NEEDS_REVIEW", "DUPLICATE_DETECTED"] },
  { id: "validated", label: "Validated", statuses: ["VALIDATED"] },
  { id: "rejected", label: "Rejected/Failed", statuses: ["REJECTED", "FAILED"] },
];

type SortKey = "filename" | "status" | "versions" | "latest" | "ingested";
type SortDir = "asc" | "desc";

interface Props {
  apiBaseUrl: string;
  refreshTick: number;
  /** Currently selected document id (highlights the matching row). */
  selectedId: string | null;
  /** Click handler — opens the doc in the DetailPanel via the App's selectById. */
  onSelectDocument: (doc: Document) => void;
  /**
   * Open the version-history modal for the supplied API document.
   * Optional so the table degrades gracefully if the host doesn't
   * route the modal — clicking the v{N} badge then becomes inert.
   */
  onOpenLineage?: (doc: Document) => void;
  /**
   * When non-null, scope the catalog to a single document — only
   * that row renders (plus an empty state if it isn't in the
   * current page). Wired by App.tsx to the active ``focusRoot``
   * when its ``kind === "doc"`` so a double-click in the table or
   * the graph keeps the catalog in sync with the focus chip.
   */
  focusedDocumentId?: string | null;
  /**
   * Double-click handler — scopes the catalog (and the focus
   * chip) to just this document. Single click still
   * ``onSelectDocument``s as before; the two fire together on a
   * double-click and the App handles the ordering.
   */
  onFocusDocument?: (doc: Document) => void;
}

function classifyExt(filename: string): DocTypeKey {
  const dot = filename.lastIndexOf(".");
  if (dot < 0 || dot === filename.length - 1) return "unknown";
  const ext = filename.slice(dot + 1).toLowerCase();
  if (ext === "pdf") return "pdf";
  if (ext === "doc" || ext === "docx") return "doc";
  if (ext === "ppt" || ext === "pptx") return "ppt";
  if (ext === "md" || ext === "markdown") return "md";
  if (ext === "html" || ext === "htm") return "web";
  if (ext === "wiki") return "wiki";
  return "unknown";
}

function formatRelative(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffMs = Date.now() - d.getTime();
  const diffMin = Math.round(diffMs / 60_000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr} h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay === 1) return "yesterday";
  if (diffDay < 7) return `${diffDay} d ago`;
  return d.toLocaleDateString();
}

function statusClass(status: DocumentVersionStatus): string {
  switch (status) {
    case "VALIDATED":
      return "kx-stat-good";
    case "NEEDS_REVIEW":
    case "DUPLICATE_DETECTED":
      return "kx-stat-warn";
    case "REJECTED":
    case "FAILED":
      return "kx-stat-bad";
    default:
      return "kx-stat-info";
  }
}

export const Catalog: React.FC<Props> = ({
  apiBaseUrl,
  refreshTick,
  selectedId,
  onSelectDocument,
  onOpenLineage,
  focusedDocumentId,
  onFocusDocument,
}) => {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [filterId, setFilterId] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey>("ingested");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQuery(query), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [query]);

  const filterStatuses = useMemo(
    () => STATUS_FILTERS.find((f) => f.id === filterId)?.statuses ?? [],
    [filterId],
  );

  const { items, loading, error, hasMore, loadMore } = useDocumentsCatalog({
    apiBaseUrl,
    refreshTick,
    statuses: filterStatuses,
    q: debouncedQuery,
  });

  // Client-side sort. The API doesn't expose a sort knob today —
  // catalog volumes are bounded by the 25-row page so an in-memory
  // sort is fine. When the API grows ``?sort=`` the column header
  // can switch to firing a re-fetch.
  const sortedItems = useMemo(() => {
    const sign = sortDir === "asc" ? 1 : -1;
    const next = [...items];
    next.sort((a, b) => {
      switch (sortKey) {
        case "filename":
          return sign * a.original_filename.localeCompare(b.original_filename);
        case "versions":
          return sign * (a.versions.length - b.versions.length);
        case "latest": {
          const al =
            a.versions.find((v) => v.id === a.latest_version_id)?.version_number ?? 0;
          const bl =
            b.versions.find((v) => v.id === b.latest_version_id)?.version_number ?? 0;
          return sign * (al - bl);
        }
        case "status": {
          const al =
            a.versions.find((v) => v.id === a.latest_version_id)?.status ?? "";
          const bl =
            b.versions.find((v) => v.id === b.latest_version_id)?.status ?? "";
          return sign * al.localeCompare(bl);
        }
        case "ingested":
        default:
          return sign * (new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
      }
    });
    return next;
  }, [items, sortKey, sortDir]);

  // When the App's ``focusRoot`` is a single document, scope the
  // table to just that row so the catalog mirrors the focus chip —
  // the user double-clicked to "see only this document". Falls back
  // to the full sorted list whenever the focus is null or scoped to
  // a non-doc kind (cluster / chunk / concept).
  const visibleItems = useMemo(() => {
    if (!focusedDocumentId) return sortedItems;
    return sortedItems.filter((d) => d.id === focusedDocumentId);
  }, [sortedItems, focusedDocumentId]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "filename" ? "asc" : "desc");
    }
  };

  const sortIndicator = (key: SortKey) =>
    sortKey === key ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  return (
    <div className="kx-catalog" data-testid="kx-catalog">
      <div className="kx-cat-toolbar">
        <div className="kx-cat-search">
          <Icon name="search" size={13} />
          <input
            type="search"
            placeholder="Search filenames…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search filenames"
          />
        </div>
        <div className="kx-cat-chips" role="tablist" aria-label="Filter by status">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              role="tab"
              aria-selected={filterId === f.id}
              className={"kx-cat-chip" + (filterId === f.id ? " kx-on" : "")}
              onClick={() => setFilterId(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="kx-warn kx-cat-error">{error}</div>}

      {!error && sortedItems.length === 0 && !loading && (
        <div className="kx-cat-empty" data-testid="kx-catalog-empty">
          <Icon name="doc" size={32} />
          <div className="kx-cat-empty-t">
            {filterId === "all" && debouncedQuery.trim() === ""
              ? "No documents in this corpus yet"
              : "Nothing matches"}
          </div>
          <div className="kx-cat-empty-s">
            {filterId === "all" && debouncedQuery.trim() === ""
              ? "Upload one to get started — the widget's upload pane drops files into this catalog."
              : "Try a different search or filter to see more rows."}
          </div>
        </div>
      )}

      {sortedItems.length > 0 && (
        <table className="kx-cat-table" data-testid="kx-catalog-table">
          <thead>
            <tr>
              <th
                className="kx-cat-col-name"
                onClick={() => toggleSort("filename")}
                aria-sort={
                  sortKey === "filename" ? (sortDir === "asc" ? "ascending" : "descending") : "none"
                }
              >
                Filename{sortIndicator("filename")}
              </th>
              <th className="kx-cat-col-type">Type</th>
              <th
                className="kx-cat-col-status"
                onClick={() => toggleSort("status")}
                aria-sort={
                  sortKey === "status" ? (sortDir === "asc" ? "ascending" : "descending") : "none"
                }
              >
                Status{sortIndicator("status")}
              </th>
              <th
                className="kx-cat-col-versions"
                onClick={() => toggleSort("versions")}
                aria-sort={
                  sortKey === "versions"
                    ? sortDir === "asc"
                      ? "ascending"
                      : "descending"
                    : "none"
                }
              >
                Versions{sortIndicator("versions")}
              </th>
              <th
                className="kx-cat-col-latest"
                onClick={() => toggleSort("latest")}
                aria-sort={
                  sortKey === "latest" ? (sortDir === "asc" ? "ascending" : "descending") : "none"
                }
              >
                Latest{sortIndicator("latest")}
              </th>
              <th
                className="kx-cat-col-date"
                onClick={() => toggleSort("ingested")}
                aria-sort={
                  sortKey === "ingested"
                    ? sortDir === "asc"
                      ? "ascending"
                      : "descending"
                    : "none"
                }
              >
                Ingested{sortIndicator("ingested")}
              </th>
            </tr>
          </thead>
          <tbody>
            {visibleItems.map((doc) => {
              const latest =
                doc.versions.find((v) => v.id === doc.latest_version_id) ??
                doc.versions[doc.versions.length - 1];
              const status = latest?.status ?? ("UPLOADED" as DocumentVersionStatus);
              const ext = classifyExt(doc.original_filename);
              const meta = DOC_TYPES[ext];
              const isSelected = selectedId === doc.id;
              const versionCount = doc.versions.length;
              return (
                <tr
                  key={doc.id}
                  className={"kx-cat-row" + (isSelected ? " kx-on" : "")}
                  data-doc-id={doc.id}
                  aria-selected={isSelected}
                  onClick={() => onSelectDocument(doc)}
                  onDoubleClick={
                    onFocusDocument ? () => onFocusDocument(doc) : undefined
                  }
                  title={
                    onFocusDocument
                      ? "Click to select · double-click to scope to this document"
                      : undefined
                  }
                >
                  <td className="kx-cat-name" title={doc.original_filename}>
                    <span className="kx-cat-fname">{doc.original_filename}</span>
                    <VersionBadges
                      versionCount={versionCount}
                      latest={latest?.version_number ?? 1}
                      onOpenLineage={
                        onOpenLineage ? () => onOpenLineage(doc) : undefined
                      }
                    />
                  </td>
                  <td className="kx-cat-type">
                    <span
                      className="kx-doc-chip kx-sm"
                      style={{ background: meta?.color ?? "#888" }}
                      title={meta?.label ?? ext}
                    >
                      {meta?.short ?? "DOC"}
                    </span>
                  </td>
                  <td className="kx-cat-status">
                    <span className={"kx-cat-badge " + statusClass(status)}>{status}</span>
                  </td>
                  <td className="kx-cat-versions">
                    <span className="kx-mono">{versionCount}</span>
                    {versionCount > 1 ? (
                      <span className="kx-mute"> versions</span>
                    ) : null}
                  </td>
                  <td className="kx-cat-latest">
                    <span className="kx-mono">v{latest?.version_number ?? 1}</span>
                  </td>
                  <td className="kx-cat-date">{formatRelative(doc.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {hasMore && (
        <button
          type="button"
          className="kx-tool-btn kx-cat-loadmore"
          onClick={loadMore}
          disabled={loading}
        >
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
};

/**
 * Compact "v{N}" badge + "(N versions)" text affordance for a doc
 * row. Surfaced separately so the catalog and the cluster rail can
 * both render the same combo without duplicating the conditional.
 *
 * When ``onOpenLineage`` is provided AND ``versionCount > 1`` the
 * badge becomes interactive (button) and clicking it opens the
 * version-history modal at the App layer. The badge stays static
 * (single span) when there's nothing to show — single-version docs
 * don't have a history to surface.
 */
export const VersionBadges: React.FC<{
  versionCount: number;
  latest: number;
  onOpenLineage?: () => void;
}> = ({ versionCount, latest, onOpenLineage }) => {
  const interactive = versionCount > 1 && typeof onOpenLineage === "function";
  return (
    <span className="kx-ver-wrap">
      {interactive ? (
        <button
          type="button"
          className="kx-ver-badge kx-mono kx-ver-badge--button"
          title={`View version history (latest v${latest})`}
          aria-label={`View version history (${versionCount} versions, latest v${latest})`}
          onClick={(e) => {
            // Prevent the parent row's onClick from firing — the
            // catalog row + the cluster-rail doc row both delegate
            // selection on click, and we want the badge to be a
            // pure modal-open affordance.
            e.stopPropagation();
            onOpenLineage?.();
          }}
          data-testid="kx-version-badge-button"
        >
          v{latest}
        </button>
      ) : (
        <span className="kx-ver-badge kx-mono" title={`Latest version v${latest}`}>
          v{latest}
        </span>
      )}
      {versionCount > 1 && (
        <span className="kx-ver-count kx-mute">({versionCount} versions)</span>
      )}
    </span>
  );
};
