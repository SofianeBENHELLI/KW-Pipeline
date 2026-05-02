import React, { useCallback, useEffect, useState } from "react";

import { ApiError, listDocuments } from "../api/client";
import type { Document, DocumentVersionStatus } from "../api/types";

const PAGE_LIMIT = 25;

interface Props {
  apiBaseUrl: string;
  refreshTick: number;
}

function statusBadgeClass(status: DocumentVersionStatus): string {
  switch (status) {
    case "VALIDATED":
      return "kw-badge kw-badge--ok";
    case "REJECTED":
    case "FAILED":
      return "kw-badge kw-badge--err";
    case "DUPLICATE_DETECTED":
    case "NEEDS_REVIEW":
      return "kw-badge kw-badge--warn";
    case "SEMANTIC_READY":
    case "EXTRACTED":
      return "kw-badge kw-badge--info";
    default:
      return "kw-badge";
  }
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export const DocumentsList: React.FC<Props> = ({ apiBaseUrl, refreshTick }) => {
  const [items, setItems] = useState<Document[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <section className="kw-card" aria-label="Recent documents">
      <h3 className="kw-card__title">Recent documents</h3>
      {error && <div className="kw-error">{error}</div>}
      {!error && items.length === 0 && !loading && (
        <div className="kw-status">No documents yet — upload one to get started.</div>
      )}
      <ul className="kw-doc-list">
        {items.map((doc) => {
          const latest = doc.versions.find((v) => v.id === doc.latest_version_id) ?? doc.versions[0];
          const status = latest?.status ?? ("UPLOADED" as DocumentVersionStatus);
          return (
            <li key={doc.id} className="kw-doc-list__item">
              <div>
                <div className="kw-doc-list__name" title={doc.original_filename}>
                  {doc.original_filename}
                </div>
                <div className="kw-doc-list__meta">
                  v{latest?.version_number ?? 1} · {formatTimestamp(doc.created_at)}
                </div>
              </div>
              <span className={statusBadgeClass(status)}>{status}</span>
            </li>
          );
        })}
      </ul>
      {cursor && (
        <div style={{ marginTop: 6 }}>
          <button
            type="button"
            className="kw-btn"
            onClick={loadMore}
            disabled={loading}
          >
            {loading ? "Loading…" : "Load more"}
          </button>
        </div>
      )}
    </section>
  );
};
