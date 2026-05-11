import { useMemo, useState } from "react";

import type { components } from "../api/generated/schema";
import { latestVersion } from "../domain/document";
import { OrbScopeChip, OrbStatusBadge } from "../ui/orb";
import { Mono } from "../ui/orb/atoms";

type ApiDocument = components["schemas"]["Document"];

type SortColumn = "filename" | "status" | "uploaded" | "versions";
type SortDir = "asc" | "desc";

export interface CatalogTableProps {
  documents: ApiDocument[];
  loading?: boolean;
  error?: string | null;
  selectedId?: string | null;
  onSelect?: (documentId: string) => void;
}

function compare(a: ApiDocument, b: ApiDocument, col: SortColumn, dir: SortDir): number {
  let av: string | number = "";
  let bv: string | number = "";
  switch (col) {
    case "filename":
      av = a.original_filename.toLowerCase();
      bv = b.original_filename.toLowerCase();
      break;
    case "status":
      av = (latestVersion(a)?.status ?? "").toLowerCase();
      bv = (latestVersion(b)?.status ?? "").toLowerCase();
      break;
    case "versions":
      av = a.versions?.length ?? 0;
      bv = b.versions?.length ?? 0;
      break;
    case "uploaded":
      av = a.created_at ?? "";
      bv = b.created_at ?? "";
      break;
  }
  if (av < bv) return dir === "asc" ? -1 : 1;
  if (av > bv) return dir === "asc" ? 1 : -1;
  return 0;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toISOString().replace("T", " ").slice(0, 16) + "Z";
}

/**
 * Phase-1 catalog table. Sortable columns, sticky header, hover + selection
 * states. Reads `ApiDocument` shape straight from the OpenAPI schema so it
 * picks up backend changes for free.
 */
export function CatalogTable({ documents, loading, error, selectedId, onSelect }: CatalogTableProps) {
  const [sort, setSort] = useState<{ col: SortColumn; dir: SortDir }>({
    col: "uploaded",
    dir: "desc",
  });

  const toggleSort = (col: SortColumn) =>
    setSort((current) =>
      current.col === col
        ? { col, dir: current.dir === "asc" ? "desc" : "asc" }
        : { col, dir: col === "filename" ? "asc" : "desc" },
    );

  const arrow = (col: SortColumn) =>
    sort.col !== col ? "" : sort.dir === "asc" ? " ↑" : " ↓";

  const sorted = useMemo(
    () => [...documents].sort((a, b) => compare(a, b, sort.col, sort.dir)),
    [documents, sort],
  );

  if (error) {
    return <div className="orb-catalog__error" role="alert">Failed to load catalog: {error}</div>;
  }

  if (loading && documents.length === 0) {
    return <div className="orb-catalog__loading">Loading documents…</div>;
  }

  if (!loading && documents.length === 0) {
    return <div className="orb-catalog__empty">No documents match the current filter.</div>;
  }

  return (
    <table className="orb-catalog__table">
      <thead>
        <tr>
          <th style={{ width: 60 }}>
            <button type="button" onClick={() => toggleSort("versions")}>
              Versions{arrow("versions")}
            </button>
          </th>
          <th>
            <button type="button" onClick={() => toggleSort("filename")}>
              Filename{arrow("filename")}
            </button>
          </th>
          <th style={{ width: 160 }}>
            <button type="button" onClick={() => toggleSort("status")}>
              Status{arrow("status")}
            </button>
          </th>
          <th style={{ width: 240 }}>Scopes</th>
          <th style={{ width: 180 }}>
            <button type="button" onClick={() => toggleSort("uploaded")}>
              Created{arrow("uploaded")}
            </button>
          </th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((doc) => {
          const selected = selectedId === doc.id;
          const scopes = doc.scopes ?? [];
          return (
            <tr
              key={doc.id}
              className={`orb-catalog__row ${selected ? "is-selected" : ""}`.trim()}
              onClick={() => onSelect?.(doc.id)}
            >
              <td className="orb-catalog__cell-versions">v{doc.versions?.length ?? 0}</td>
              <td>
                <div className="orb-catalog__filename">
                  <span>{doc.original_filename}</span>
                  <Mono className="orb-catalog__filename-id">{doc.id.slice(0, 8)}</Mono>
                </div>
              </td>
              <td>
                <OrbStatusBadge status={latestVersion(doc)?.status} />
              </td>
              <td>
                <div className="orb-catalog__cell-scopes">
                  {scopes.length === 0 && <span style={{ color: "var(--orb-fg-faint)" }}>—</span>}
                  {scopes.map((scope, index) => (
                    <OrbScopeChip
                      key={`${scope.kind}:${scope.ref}:${index}`}
                      scope={scope.kind}
                      title={`${scope.kind}: ${scope.ref}`}
                    />
                  ))}
                </div>
              </td>
              <td>
                <Mono>{formatDate(doc.created_at)}</Mono>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
