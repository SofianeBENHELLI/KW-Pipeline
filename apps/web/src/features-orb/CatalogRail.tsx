import { useMemo } from "react";

import type { components } from "../api/generated/schema";
import { latestVersion } from "../domain/document";
import { Icon, Kbd, OrbStatusBadge } from "../ui/orb";

import type { BatchProgressEntry } from "./batch";

type ApiDocument = components["schemas"]["Document"];

export type CatalogView = "recent" | "review" | "validated" | "failed";

type SortCol = "filename" | "uploaded" | "status";
type SortDir = "asc" | "desc";

const VIEWS: { id: CatalogView; label: string; hint: string }[] = [
  { id: "recent", label: "Recent", hint: "all" },
  { id: "review", label: "Review", hint: "NEEDS_REVIEW" },
  { id: "validated", label: "Validated", hint: "VALIDATED" },
  { id: "failed", label: "Failed", hint: "FAILED" },
];

const SCOPE_VAR: Record<string, string> = {
  personal: "var(--orb-info)",
  swym_community: "var(--orb-purple)",
  project: "var(--orb-ok)",
};

const SCOPE_LABEL: Record<string, string> = {
  personal: "personal",
  swym_community: "community",
  project: "project",
};

function formatUploaded(iso: string | null | undefined): { day: string; time: string } {
  if (!iso) return { day: "—", time: "" };
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return { day: iso, time: "" };
  const day = date.toISOString().slice(0, 10);
  const time = date.toISOString().slice(11, 16);
  return { day, time };
}

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export interface CatalogRailProps {
  documents: ApiDocument[];
  loading?: boolean;
  view: CatalogView;
  onView: (next: CatalogView) => void;
  query: string;
  onQuery: (next: string) => void;
  counts?: Partial<Record<CatalogView, number>>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  selection: ReadonlySet<string>;
  onToggleBatch: (id: string, next: boolean) => void;
  onClearBatch: () => void;
  onRunBatch: () => void;
  batchRunning: boolean;
  batchProgress?: Record<string, BatchProgressEntry>;
  sort: { col: SortCol; dir: SortDir };
  onSort: (col: SortCol) => void;
}

/**
 * Variant-A catalog rail. Drops the inline class names from the mockup
 * (`rwA-*`) verbatim so the CSS in `rwA.css` paints the rail without
 * any extra mapping. Custom checkbox uses the mockup's draw style, not
 * a native `<input>`, so it looks consistent across themes.
 */
export function CatalogRail({
  documents,
  loading,
  view,
  onView,
  query,
  onQuery,
  counts,
  selectedId,
  onSelect,
  selection,
  onToggleBatch,
  onClearBatch,
  onRunBatch,
  batchRunning,
  batchProgress,
  sort,
  onSort,
}: CatalogRailProps) {
  const sortArrow = (col: SortCol) =>
    sort.col !== col ? "" : sort.dir === "asc" ? " ↑" : " ↓";

  const sorted = useMemo(() => {
    const cmp = (a: ApiDocument, b: ApiDocument): number => {
      let av: string | number = "";
      let bv: string | number = "";
      switch (sort.col) {
        case "filename":
          av = a.original_filename.toLowerCase();
          bv = b.original_filename.toLowerCase();
          break;
        case "uploaded":
          av = a.created_at ?? "";
          bv = b.created_at ?? "";
          break;
        case "status":
          av = (latestVersion(a)?.status ?? "").toLowerCase();
          bv = (latestVersion(b)?.status ?? "").toLowerCase();
          break;
      }
      if (av < bv) return -1;
      if (av > bv) return 1;
      return 0;
    };
    return [...documents].sort((a, b) => (sort.dir === "asc" ? cmp(a, b) : -cmp(a, b)));
  }, [documents, sort]);

  return (
    <>
      <div className="rwA-railhead">
        <div className="rwA-search">
          <span className="rwA-search-i" aria-hidden="true">
            <Icon name="search" />
          </span>
          <input
            className="rwA-search-i-input"
            type="search"
            placeholder="Filter filename…"
            aria-label="Filter documents by filename"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
          />
          <Kbd>/</Kbd>
        </div>
        <nav className="rwA-views" aria-label="Saved views">
          {VIEWS.map((definition) => {
            const active = definition.id === view;
            const count = counts?.[definition.id];
            return (
              <button
                key={definition.id}
                type="button"
                className={`rwA-view ${active ? "is-active" : ""}`.trim()}
                aria-current={active ? "page" : undefined}
                onClick={() => onView(definition.id)}
              >
                <span>{definition.label}</span>
                <span className="rwA-view-count">{count?.toLocaleString() ?? ""}</span>
              </button>
            );
          })}
        </nav>
      </div>

      {selection.size > 0 && (
        <div className="rwA-batchbar">
          <div className="rwA-batchbar-l">
            <span className="orb-mono" style={{ fontSize: 11, color: "var(--orb-fg-muted)" }}>
              {selection.size} selected
            </span>
            <button type="button" className="rwA-link" onClick={onClearBatch} disabled={batchRunning}>
              clear
            </button>
          </div>
          <button
            type="button"
            className="orb-btn orb-btn--primary orb-btn--xs"
            onClick={onRunBatch}
            disabled={batchRunning}
          >
            <Icon name="bolt" /> {batchRunning ? "Running…" : "Run pipeline"}
          </button>
        </div>
      )}

      <div className="rwA-listhead">
        <span style={{ width: 20 }}></span>
        <button
          type="button"
          className={`rwA-sortbtn ${sort.col === "filename" ? "is-on" : ""}`.trim()}
          style={{ flex: 1 }}
          onClick={() => onSort("filename")}
        >
          FILENAME{sortArrow("filename")}
        </button>
        <button
          type="button"
          className={`rwA-sortbtn ${sort.col === "uploaded" ? "is-on" : ""}`.trim()}
          style={{ width: 96 }}
          onClick={() => onSort("uploaded")}
        >
          UPLOADED{sortArrow("uploaded")}
        </button>
        <button
          type="button"
          className={`rwA-sortbtn ${sort.col === "status" ? "is-on" : ""}`.trim()}
          style={{ width: 96, textAlign: "right" }}
          onClick={() => onSort("status")}
        >
          STATUS{sortArrow("status")}
        </button>
      </div>

      <div className="rwA-listcount orb-mono">
        showing <b>{sorted.length}</b> of {documents.length.toLocaleString()}
        {loading ? " · loading" : " · scroll for more"}
      </div>

      <div className="rwA-list">
        {sorted.length === 0 && (
          <div style={{ padding: 16, color: "var(--orb-fg-muted)", fontSize: 12 }}>
            {loading ? "Loading documents…" : "No documents match this filter."}
          </div>
        )}
        {sorted.map((doc) => {
          const status = latestVersion(doc)?.status;
          const isSel = selectedId === doc.id;
          const checked = selection.has(doc.id);
          const progress = batchProgress?.[doc.id];
          const upload = formatUploaded(doc.created_at);
          const bytes = formatBytes(latestVersion(doc)?.file_size);
          const firstScope = (doc.scopes ?? [])[0];
          return (
            <div
              key={doc.id}
              className={`rwA-row ${isSel ? "is-sel" : ""}`.trim()}
              onClick={() => onSelect(doc.id)}
              role="button"
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect(doc.id);
                }
              }}
            >
              <span className="rwA-check">
                <button
                  type="button"
                  className={`rwA-checkbox ${checked ? "is-on" : ""}`.trim()}
                  onClick={(event) => {
                    event.stopPropagation();
                    onToggleBatch(doc.id, !checked);
                  }}
                  aria-label={`Select ${doc.original_filename} for batch`}
                  aria-pressed={checked}
                >
                  {checked && <Icon name="check" size={10} />}
                </button>
              </span>
              <div className="rwA-rowmain">
                <div className="rwA-fname" title={doc.original_filename}>
                  {doc.original_filename}
                </div>
                <div className="rwA-rowmeta">
                  <span className="orb-mono">{doc.id.slice(0, 8)}</span>
                  <span>·</span>
                  <span>v{doc.versions.length}</span>
                  <span>·</span>
                  <span>{bytes}</span>
                  {firstScope && (
                    <span
                      className="rwA-scope"
                      style={{ color: SCOPE_VAR[firstScope.kind] ?? "var(--orb-fg-dim)" }}
                    >
                      ● {SCOPE_LABEL[firstScope.kind] ?? firstScope.kind}
                    </span>
                  )}
                </div>
              </div>
              <div className="rwA-rowuploaded orb-mono" title={doc.created_at ?? ""}>
                <span className="rwA-up-day">{upload.day}</span>
                <span className="rwA-up-time">{upload.time}</span>
              </div>
              <div className="rwA-rowstatus">
                {progress ? (
                  <span className={`rwA-prog rwA-prog--${progress.stage}`} title={progress.reason}>
                    {progress.stage}
                  </span>
                ) : (
                  <OrbStatusBadge status={status} />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

/** Resolve the active view to a list of `DocumentVersionStatus` strings. */
export function viewToStatuses(view: CatalogView): string[] {
  switch (view) {
    case "review":
      return ["NEEDS_REVIEW"];
    case "validated":
      return ["VALIDATED"];
    case "failed":
      return ["FAILED"];
    case "recent":
    default:
      return [];
  }
}
