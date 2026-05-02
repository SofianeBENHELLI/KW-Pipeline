import React, { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, listDocuments } from "../api/client";
import type { Document, DocumentVersionStatus } from "../api/types";
import { extOf, FileTypeIcon } from "../components/FileTypeIcon";
import { Icon } from "../components/icons";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";

const PAGE_LIMIT = 25;

interface Props {
  apiBaseUrl: string;
  refreshTick: number;
  /**
   * Optional click hook for opening a document in the full Orbital
   * review surface — wired by the parent when that integration lands.
   */
  onOpenDocument?: (doc: Document) => void;
}

type FilterId = "all" | "validated" | "review" | "failed";

const FILTERS: { id: FilterId; label: string }[] = [
  { id: "all", label: "All" },
  { id: "validated", label: "Validated" },
  { id: "review", label: "Review" },
  { id: "failed", label: "Failed" },
];

function statusBucket(status: DocumentVersionStatus): FilterId {
  switch (status) {
    case "VALIDATED":
      return "validated";
    case "NEEDS_REVIEW":
    case "DUPLICATE_DETECTED":
      return "review";
    case "REJECTED":
    case "FAILED":
      return "failed";
    default:
      return "all";
  }
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // Mockup style: relative-ish, mono-friendly. Falls back to locale.
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

export const DocumentsList: React.FC<Props> = ({
  apiBaseUrl,
  refreshTick,
  onOpenDocument,
}) => {
  const [items, setItems] = useState<Document[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterId>("all");

  const loadFirstPage = useCallback(
    (signal: AbortSignal) => {
      setLoading(true);
      setError(null);
      listDocuments({ limit: PAGE_LIMIT, baseUrl: apiBaseUrl, signal })
        .then((page) => {
          setItems(page.items);
          setCursor(page.next_cursor);
        })
        .catch((err: unknown) => {
          if ((err as { name?: string })?.name === "AbortError") return;
          setError(
            err instanceof ApiError
              ? `${err.code}: ${err.detail}`
              : err instanceof Error
                ? err.message
                : "Failed to load documents",
          );
        })
        .finally(() => setLoading(false));
    },
    [apiBaseUrl],
  );

  useEffect(() => {
    const controller = new AbortController();
    loadFirstPage(controller.signal);
    return () => controller.abort();
  }, [loadFirstPage, refreshTick]);

  const loadMore = useCallback(() => {
    if (!cursor) return;
    setLoading(true);
    listDocuments({ limit: PAGE_LIMIT, cursor, baseUrl: apiBaseUrl })
      .then((page) => {
        setItems((prev) => [...prev, ...page.items]);
        setCursor(page.next_cursor);
      })
      .catch((err: unknown) => {
        setError(
          err instanceof ApiError
            ? `${err.code}: ${err.detail}`
            : err instanceof Error
              ? err.message
              : "Failed to load more",
        );
      })
      .finally(() => setLoading(false));
  }, [cursor, apiBaseUrl]);

  // Counts derived from the currently-loaded rows. Approximate while
  // there are more pages to fetch; the badge in the header surface
  // ("25 of 142") makes that explicit.
  const counts = useMemo(() => {
    const base = { all: items.length, validated: 0, review: 0, failed: 0 };
    for (const doc of items) {
      const latest =
        doc.versions.find((v) => v.id === doc.latest_version_id) ?? doc.versions[0];
      const status = latest?.status ?? ("UPLOADED" as DocumentVersionStatus);
      const bucket = statusBucket(status);
      if (bucket !== "all") base[bucket] += 1;
    }
    return base;
  }, [items]);

  const visibleItems = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((doc) => {
      if (q && !doc.original_filename.toLowerCase().includes(q)) return false;
      if (filter !== "all") {
        const latest =
          doc.versions.find((v) => v.id === doc.latest_version_id) ?? doc.versions[0];
        const status = latest?.status ?? ("UPLOADED" as DocumentVersionStatus);
        if (statusBucket(status) !== filter) return false;
      }
      return true;
    });
  }, [items, query, filter]);

  return (
    <section className="kw-section" aria-label="Recent documents">
      <SectionHeader
        icon="docs"
        title="Recent documents"
        meta={
          items.length > 0
            ? cursor
              ? `${items.length} of more`
              : `${items.length} loaded`
            : undefined
        }
      />

      <div className="kw-search">
        <Icon name="search" />
        <input
          type="search"
          placeholder="Search filenames…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search filenames"
        />
      </div>

      <div className="kw-seg" role="tablist" aria-label="Filter by status">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            role="tab"
            aria-selected={filter === f.id}
            className={filter === f.id ? "kw-seg__btn kw-seg__btn--active" : "kw-seg__btn"}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
            <span className="kw-seg__count">{counts[f.id]}</span>
          </button>
        ))}
      </div>

      {error && <div className="kw-error">{error}</div>}

      {!error && visibleItems.length === 0 && !loading && (
        <div className="kw-empty">
          <span className="kw-empty__glyph" aria-hidden="true">
            <Icon name="files" size={18} />
          </span>
          <div className="kw-empty__title">
            {items.length === 0 ? "No documents yet" : "Nothing matches"}
          </div>
          <div className="kw-empty__body">
            {items.length === 0
              ? "Drop a file in the upload pane or use the buttons there to get started. Each ingestion runs through validation, extraction, and semantic enrichment."
              : "Try a different search or filter to see more rows."}
          </div>
        </div>
      )}

      {visibleItems.length > 0 && (
        <ul className="kw-doc-list">
          {visibleItems.map((doc) => {
            const latest =
              doc.versions.find((v) => v.id === doc.latest_version_id) ?? doc.versions[0];
            const status = latest?.status ?? ("UPLOADED" as DocumentVersionStatus);
            const ext = extOf(doc.original_filename);
            const onActivate = onOpenDocument
              ? () => onOpenDocument(doc)
              : undefined;
            return (
              <li
                key={doc.id}
                className="kw-doc-list__item"
                onClick={onActivate}
                style={onActivate ? { cursor: "pointer" } : undefined}
              >
                <FileTypeIcon ext={ext} />
                <div className="kw-doc-list__body">
                  <div className="kw-doc-list__name" title={doc.original_filename}>
                    {doc.original_filename}
                  </div>
                  <div className="kw-doc-list__meta">
                    v{latest?.version_number ?? 1} · {formatTimestamp(doc.created_at)}
                  </div>
                </div>
                <StatusBadge status={status} />
              </li>
            );
          })}
        </ul>
      )}

      {cursor && (
        <button
          type="button"
          className="kw-btn kw-btn--full"
          onClick={loadMore}
          disabled={loading}
        >
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </section>
  );
};
