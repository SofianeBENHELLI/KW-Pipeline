/**
 * DocRail — 380px left rail of the Knowledge Forge Review Workspace.
 *
 * The single most-touched surface in the app. Per the design handoff:
 *
 *   ┌ search   ┐
 *   │ views(4) │
 *   │ batchbar │   ← only when a row is checked
 *   │ listhead │   ← sortable cols
 *   │ count    │
 *   │ rows…    │
 *   └──────────┘
 *
 * Selection model:
 *   - Clicking a row sets the *active* doc (drives the main pane).
 *   - Clicking the row's checkbox toggles it into the *batch* set
 *     without changing the active doc.
 *   - The batch bar appears only when the batch set is non-empty.
 */

import type { CSSProperties, ReactElement } from "react";

import { Btn, Kbd, OrbI, ScopeChip, StatusBadge } from "../index";
import {
  distinctScopeKinds,
  formatBytes,
  latestStatus,
  splitIsoTimestamp,
} from "./format";
import type { ApiDocument } from "../../api/types";
import type { RailView } from "../hooks/useDocuments";

export type RailSortColumn = "filename" | "uploaded" | "status";
export type RailSortDir = "asc" | "desc";

export interface RailSort {
  col: RailSortColumn;
  dir: RailSortDir;
}

export interface RailViewDef {
  id: RailView;
  label: string;
  count?: number;
}

export const DEFAULT_VIEWS: ReadonlyArray<RailViewDef> = [
  { id: "recent",    label: "Recent" },
  { id: "review",    label: "Review" },
  { id: "validated", label: "Validated" },
  { id: "failed",    label: "Failed" },
];

export interface DocRailProps {
  /** Currently-active saved view. */
  view: RailView;
  onView: (view: RailView) => void;
  /** Filename filter input value. */
  query: string;
  onQuery: (q: string) => void;
  /** Documents in the current view (server-filtered). */
  documents: ApiDocument[];
  /** Loading / empty / error indicators. */
  loading?: boolean;
  errorMessage?: string | null;
  /** Active document id (drives main pane). */
  activeDocId: string | null;
  onSelect: (docId: string) => void;
  /** Multi-select (batch) checkbox set. */
  selected: ReadonlySet<string>;
  onToggleSelect: (docId: string) => void;
  onClearSelection: () => void;
  onRunBatch?: () => void;
  /** Optional per-view counts. Surfaced after the view label. */
  counts?: Partial<Record<RailView, number>>;
  /** Total documents in the catalog (drives "showing N of M"). */
  totalForView?: number | null;
  /** Sort. */
  sort: RailSort;
  onToggleSort: (col: RailSortColumn) => void;
}

export function DocRail({
  view,
  onView,
  query,
  onQuery,
  documents,
  loading = false,
  errorMessage = null,
  activeDocId,
  onSelect,
  selected,
  onToggleSelect,
  onClearSelection,
  onRunBatch,
  counts,
  totalForView,
  sort,
  onToggleSort,
}: DocRailProps): ReactElement {
  const sortArrow = (col: RailSortColumn) =>
    sort.col !== col ? "" : sort.dir === "asc" ? " ↑" : " ↓";

  const showing = documents.length;
  const totalLabel =
    totalForView != null && totalForView !== showing
      ? ` of ${totalForView.toLocaleString()}`
      : "";

  return (
    <aside className="kf-rail orb-scroll" aria-label="Document picker">
      <div className="kf-rail__head">
        <div className="kf-rail__search">
          <span className="kf-rail__search-icon" aria-hidden="true">
            {OrbI.search}
          </span>
          <input
            className="kf-rail__search-input"
            placeholder="Filter filename…"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            aria-label="Filter documents by filename"
          />
          <span className="kf-rail__search-kbd">
            <Kbd>/</Kbd>
          </span>
        </div>

        <div className="kf-rail__views" role="tablist" aria-label="Saved views">
          {DEFAULT_VIEWS.map((v) => {
            const c = counts?.[v.id];
            const active = view === v.id;
            return (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={active}
                aria-current={active ? "page" : undefined}
                className={`kf-rail__view ${active ? "is-active" : ""}`}
                onClick={() => onView(v.id)}
              >
                <span className="kf-rail__view-label">{v.label}</span>
                {typeof c === "number" && (
                  <span className="kf-rail__view-count orb-mono">
                    {c.toLocaleString()}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {selected.size > 0 && (
        <div className="kf-rail__batchbar" role="region" aria-label="Batch selection">
          <div className="kf-rail__batchbar-l">
            <span className="orb-mono kf-rail__batchbar-count">
              {selected.size} selected
            </span>
            <button
              type="button"
              className="kf-rail__link"
              onClick={onClearSelection}
            >
              clear
            </button>
          </div>
          {onRunBatch && (
            <Btn xs kind="primary" icon={OrbI.bolt} onClick={onRunBatch}>
              Run pipeline
            </Btn>
          )}
        </div>
      )}

      <div className="kf-rail__listhead" role="rowgroup">
        <span style={sortHeaderCheckStyle} aria-hidden="true" />
        <button
          type="button"
          className={`kf-rail__sortbtn ${sort.col === "filename" ? "is-on" : ""}`}
          style={{ flex: 1 }}
          onClick={() => onToggleSort("filename")}
        >
          FILENAME{sortArrow("filename")}
        </button>
        <button
          type="button"
          className={`kf-rail__sortbtn ${sort.col === "uploaded" ? "is-on" : ""}`}
          style={{ width: 96 }}
          onClick={() => onToggleSort("uploaded")}
        >
          UPLOADED{sortArrow("uploaded")}
        </button>
        <button
          type="button"
          className={`kf-rail__sortbtn ${sort.col === "status" ? "is-on" : ""}`}
          style={{ width: 96, textAlign: "right" }}
          onClick={() => onToggleSort("status")}
        >
          STATUS{sortArrow("status")}
        </button>
      </div>

      <div className="kf-rail__listcount orb-mono" data-testid="kf-rail-count">
        showing <b>{showing}</b>
        {totalLabel}
        {" · scroll for more"}
      </div>

      <div className="kf-rail__list">
        {loading && documents.length === 0 && (
          <RailSkeletonRows count={6} />
        )}
        {!loading && errorMessage && (
          <div className="kf-rail__empty" role="alert">
            <p>{errorMessage}</p>
          </div>
        )}
        {!loading && !errorMessage && documents.length === 0 && (
          <div className="kf-rail__empty">
            <p>No documents match this view.</p>
            <Btn
              xs
              kind="ghost"
              onClick={() => {
                onQuery("");
                onView("recent");
              }}
            >
              Clear filters
            </Btn>
          </div>
        )}

        {documents.map((doc) => (
          <DocRow
            key={doc.id}
            doc={doc}
            active={doc.id === activeDocId}
            checked={selected.has(doc.id)}
            onSelect={onSelect}
            onToggleCheck={onToggleSelect}
          />
        ))}
      </div>
    </aside>
  );
}

const sortHeaderCheckStyle: CSSProperties = { width: 20 };

interface DocRowProps {
  doc: ApiDocument;
  active: boolean;
  checked: boolean;
  onSelect: (docId: string) => void;
  onToggleCheck: (docId: string) => void;
}

function DocRow({
  doc,
  active,
  checked,
  onSelect,
  onToggleCheck,
}: DocRowProps): ReactElement {
  const status = latestStatus(doc);
  const ver = doc.versions[doc.versions.length - 1];
  const { day, time } = splitIsoTimestamp(ver?.created_at ?? doc.created_at);
  const scopes = distinctScopeKinds(doc);
  const versionsCount = doc.versions.length;
  const bytes = formatBytes(ver?.file_size ?? null);

  return (
    <div
      className={`kf-rail__row ${active ? "is-sel" : ""}`}
      onClick={() => onSelect(doc.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(doc.id);
        }
      }}
      role="button"
      tabIndex={0}
      aria-pressed={active}
      aria-label={`Open ${doc.original_filename}`}
    >
      <span
        className="kf-rail__check"
        onClick={(e) => {
          e.stopPropagation();
          onToggleCheck(doc.id);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            e.stopPropagation();
            onToggleCheck(doc.id);
          }
        }}
        role="checkbox"
        aria-checked={checked}
        aria-label={checked ? `Deselect ${doc.original_filename}` : `Select ${doc.original_filename}`}
        tabIndex={0}
      >
        <span className={`kf-rail__checkbox ${checked ? "is-on" : ""}`}>
          {checked && OrbI.check}
        </span>
      </span>

      <div className="kf-rail__rowmain">
        <div className="kf-rail__fname" title={doc.original_filename}>
          {doc.original_filename}
        </div>
        <div className="kf-rail__rowmeta">
          <span className="orb-mono">{doc.id}</span>
          <span aria-hidden="true">·</span>
          <span>v{versionsCount}</span>
          <span aria-hidden="true">·</span>
          <span>{bytes}</span>
          {scopes.slice(0, 1).map((s) => (
            <ScopeChip key={s} scope={s} />
          ))}
        </div>
      </div>

      <div className="kf-rail__rowuploaded orb-mono" title={ver?.created_at ?? ""}>
        <span className="kf-rail__up-day">{day}</span>
        <span className="kf-rail__up-time">{time}</span>
      </div>

      <div className="kf-rail__rowstatus">
        <StatusBadge status={status} />
      </div>
    </div>
  );
}

function RailSkeletonRows({ count }: { count: number }): ReactElement {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="kf-rail__row kf-rail__row--skeleton" aria-hidden="true">
          <span style={{ width: 20 }} />
          <div className="kf-rail__rowmain">
            <div className="kf-rail__skel kf-rail__skel--bar" />
            <div className="kf-rail__skel kf-rail__skel--bar2" />
          </div>
          <div className="kf-rail__skel kf-rail__skel--col" style={{ width: 96 }} />
          <div className="kf-rail__skel kf-rail__skel--col" style={{ width: 96 }} />
        </div>
      ))}
    </>
  );
}
