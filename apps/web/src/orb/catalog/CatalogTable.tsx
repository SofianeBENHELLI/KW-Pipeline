/**
 * CatalogTable — bulk-ops document table for `/kf/catalog`.
 *
 * Per design §4: a standalone table view (separate from the Review
 * Workspace's rail). Columns are toggleable, the header checkbox
 * selects-all on the visible page, and a sticky bulk-action bar at
 * the bottom shows the count + actions.
 *
 * The data layer is shared with the Review Workspace (`useDocuments`)
 * so filters and visibility match. Sorting is the same client-side
 * `sortDocs` from `review/ReviewWorkspace`.
 */

import { useMemo, useState } from "react";
import type { ReactElement } from "react";

import { Btn, OrbI, ScopeChip, StatusBadge } from "../index";
import {
  distinctScopeKinds,
  formatBytes,
  latestStatus,
  splitIsoTimestamp,
} from "../review/format";
import { sortDocs } from "../review/ReviewWorkspace";
import type { ApiDocument } from "../../api/types";
import type { RailSort } from "../review/DocRail";

export type ColumnId =
  | "filename"
  | "id"
  | "status"
  | "versions"
  | "bytes"
  | "scope"
  | "uploaded";

export interface ColumnDef {
  id: ColumnId;
  label: string;
  /** Whether the user can hide this column. Filename is always-on. */
  toggleable: boolean;
}

export const ALL_COLUMNS: ColumnDef[] = [
  { id: "filename", label: "Filename", toggleable: false },
  { id: "id", label: "ID", toggleable: true },
  { id: "status", label: "Status", toggleable: true },
  { id: "versions", label: "Versions", toggleable: true },
  { id: "bytes", label: "Bytes", toggleable: true },
  { id: "scope", label: "Scope", toggleable: true },
  { id: "uploaded", label: "Uploaded", toggleable: true },
];

const DEFAULT_VISIBLE: ColumnId[] = [
  "filename",
  "id",
  "status",
  "versions",
  "bytes",
  "scope",
  "uploaded",
];

export interface CatalogTableProps {
  documents: ApiDocument[];
  loading?: boolean;
  errorMessage?: string | null;
  /** Optional click handler for opening a row in the Review Workspace. */
  onOpen?: (docId: string) => void;
  /** Optional handler for the bulk-bar's "Run pipeline" button. */
  onRunBulk?: (docIds: string[]) => void;
  /** Optional handler for the bulk-bar's "Purge" button. */
  onPurgeBulk?: (docIds: string[]) => void;
  /** Initial sort. Defaults to `uploaded desc`. */
  initialSort?: RailSort;
}

export function CatalogTable({
  documents,
  loading = false,
  errorMessage = null,
  onOpen,
  onRunBulk,
  onPurgeBulk,
  initialSort = { col: "uploaded", dir: "desc" },
}: CatalogTableProps): ReactElement {
  const [sort, setSort] = useState<RailSort>(initialSort);
  const [visible, setVisible] = useState<ReadonlySet<ColumnId>>(
    new Set(DEFAULT_VISIBLE),
  );
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const sortedDocs = useMemo(() => sortDocs(documents, sort), [documents, sort]);

  const toggleSort = (col: RailSort["col"]) =>
    setSort((s) =>
      s.col === col
        ? { col, dir: s.dir === "asc" ? "desc" : "asc" }
        : { col, dir: col === "filename" ? "asc" : "desc" },
    );
  const sortArrow = (col: RailSort["col"]) =>
    sort.col !== col ? "" : sort.dir === "asc" ? " ↑" : " ↓";

  const allChecked =
    sortedDocs.length > 0 && sortedDocs.every((d) => selected.has(d.id));
  const someChecked =
    !allChecked && sortedDocs.some((d) => selected.has(d.id));

  const toggleAll = () => {
    setSelected((prev) => {
      if (allChecked) {
        const next = new Set(prev);
        sortedDocs.forEach((d) => next.delete(d.id));
        return next;
      }
      const next = new Set(prev);
      sortedDocs.forEach((d) => next.add(d.id));
      return next;
    });
  };

  const toggleOne = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleColumn = (id: ColumnId) => {
    setVisible((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <section className="kf-cat" aria-label="Document catalog">
      <header className="kf-cat__head">
        <ColumnToggles
          all={ALL_COLUMNS}
          visible={visible}
          onToggle={toggleColumn}
        />
        <span className="kf-cat__count orb-mono">
          {sortedDocs.length.toLocaleString()} docs
        </span>
      </header>

      <div className="kf-cat__tablewrap">
        <table className="kf-cat__table">
          <thead>
            <tr>
              <th className="kf-cat__th-check">
                <input
                  type="checkbox"
                  checked={allChecked}
                  ref={(el) => {
                    if (el) el.indeterminate = someChecked;
                  }}
                  onChange={toggleAll}
                  aria-label={allChecked ? "Deselect all" : "Select all on this page"}
                />
              </th>
              {ALL_COLUMNS.filter((c) => visible.has(c.id)).map((c) => (
                <th key={c.id}>
                  <button
                    type="button"
                    className={`kf-cat__sortbtn ${sort.col === c.id ? "is-on" : ""}`}
                    onClick={() => {
                      if (c.id === "id" || c.id === "scope" || c.id === "versions" || c.id === "bytes") return;
                      toggleSort(c.id as RailSort["col"]);
                    }}
                    disabled={
                      c.id === "id" ||
                      c.id === "scope" ||
                      c.id === "versions" ||
                      c.id === "bytes"
                    }
                  >
                    {c.label}
                    {sortArrow(c.id as RailSort["col"])}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && documents.length === 0 && (
              <tr>
                <td colSpan={visible.size + 1} className="kf-cat__msg">
                  Loading catalog…
                </td>
              </tr>
            )}
            {errorMessage && (
              <tr>
                <td
                  colSpan={visible.size + 1}
                  className="kf-cat__msg kf-cat__msg--err"
                >
                  <div role="alert">{errorMessage}</div>
                </td>
              </tr>
            )}
            {!loading && !errorMessage && sortedDocs.length === 0 && (
              <tr>
                <td colSpan={visible.size + 1} className="kf-cat__msg">
                  No documents match the current filters.
                </td>
              </tr>
            )}
            {sortedDocs.map((d) => (
              <CatalogRow
                key={d.id}
                doc={d}
                visible={visible}
                checked={selected.has(d.id)}
                onToggle={() => toggleOne(d.id)}
                onOpen={onOpen ? () => onOpen(d.id) : undefined}
              />
            ))}
          </tbody>
        </table>
      </div>

      {selected.size > 0 && (
        <footer className="kf-cat__bulkbar" role="region" aria-label="Bulk actions">
          <span className="orb-mono kf-cat__bulkbar-count">
            {selected.size} selected
          </span>
          <button
            type="button"
            className="kf-cat__bulkbar-link"
            onClick={() => setSelected(new Set())}
          >
            clear
          </button>
          <span style={{ flex: 1 }} />
          {onRunBulk && (
            <Btn xs kind="primary" icon={OrbI.bolt} onClick={() => onRunBulk([...selected])}>
              Run pipeline
            </Btn>
          )}
          {onPurgeBulk && (
            <Btn xs kind="danger" icon={OrbI.trash} onClick={() => onPurgeBulk([...selected])}>
              Purge
            </Btn>
          )}
        </footer>
      )}
    </section>
  );
}

function CatalogRow({
  doc,
  visible,
  checked,
  onToggle,
  onOpen,
}: {
  doc: ApiDocument;
  visible: ReadonlySet<ColumnId>;
  checked: boolean;
  onToggle: () => void;
  onOpen?: () => void;
}): ReactElement {
  const ver = doc.versions[doc.versions.length - 1];
  const status = latestStatus(doc);
  const { day, time } = splitIsoTimestamp(ver?.created_at ?? doc.created_at);
  const scopes = distinctScopeKinds(doc);

  return (
    <tr className={`kf-cat__row ${checked ? "is-sel" : ""}`}>
      <td className="kf-cat__td-check">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          aria-label={`Select ${doc.original_filename}`}
        />
      </td>
      {visible.has("filename") && (
        <td className="kf-cat__td-fname">
          {onOpen ? (
            <button
              type="button"
              className="kf-cat__open"
              onClick={onOpen}
              title={doc.original_filename}
            >
              {doc.original_filename}
            </button>
          ) : (
            <span title={doc.original_filename}>{doc.original_filename}</span>
          )}
        </td>
      )}
      {visible.has("id") && (
        <td className="orb-mono kf-cat__td-id">{doc.id}</td>
      )}
      {visible.has("status") && (
        <td>
          <StatusBadge status={status} />
        </td>
      )}
      {visible.has("versions") && (
        <td className="orb-mono kf-cat__td-num">{doc.versions.length}</td>
      )}
      {visible.has("bytes") && (
        <td className="orb-mono kf-cat__td-num">
          {formatBytes(ver?.file_size ?? null)}
        </td>
      )}
      {visible.has("scope") && (
        <td className="kf-cat__td-scope">
          {scopes.length === 0 ? (
            <span className="kf-cat__td-dim">—</span>
          ) : (
            scopes.map((s) => <ScopeChip key={s} scope={s} />)
          )}
        </td>
      )}
      {visible.has("uploaded") && (
        <td className="orb-mono kf-cat__td-up">
          {day}
          {time ? ` ${time}` : ""}
        </td>
      )}
    </tr>
  );
}

function ColumnToggles({
  all,
  visible,
  onToggle,
}: {
  all: ColumnDef[];
  visible: ReadonlySet<ColumnId>;
  onToggle: (id: ColumnId) => void;
}): ReactElement {
  return (
    <div className="kf-cat__cols" role="group" aria-label="Toggle columns">
      <span className="orb-mono kf-cat__cols-h">columns</span>
      {all
        .filter((c) => c.toggleable)
        .map((c) => {
          const on = visible.has(c.id);
          return (
            <button
              key={c.id}
              type="button"
              className={`kf-cat__col ${on ? "is-on" : ""}`}
              onClick={() => onToggle(c.id)}
              aria-pressed={on}
            >
              {c.label}
            </button>
          );
        })}
    </div>
  );
}
