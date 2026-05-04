/**
 * Cursor-paginated documents catalog hook for the Explorer's Catalog
 * tab.
 *
 * The widget already has a similar pattern (apps/widget/src/sections/
 * DocumentsList.tsx); we keep the explorer's version self-contained
 * because the explorer's column set differs (versions count, latest
 * version_number, sort columns) and we want to evolve the two
 * surfaces independently while #229 still owns the App.tsx split.
 *
 * Filters:
 *   * ``status`` — repeated as ``?status=`` query params (server-side
 *     OR semantics, per the API).
 *   * ``q`` — debounced filename search; the input is debounced at
 *     the call-site (the hook re-fetches whenever ``q`` changes).
 *
 * Pagination uses the catalog's existing ``next_cursor`` envelope —
 * ``loadMore()`` appends to ``items``, ``reload()`` resets and
 * re-fetches the first page (e.g. on filter change).
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, listDocuments } from "../api/client";
import type { Document, DocumentVersionStatus } from "../api/types";

const PAGE_LIMIT = 25;

export interface DocumentsCatalogOptions {
  apiBaseUrl: string;
  /** Bumps to force a refresh (e.g. after the user uploads a doc). */
  refreshTick?: number;
  /** Server-side ``?status=`` filter. Empty array = no filter. */
  statuses: DocumentVersionStatus[];
  /** Server-side ``?q=`` filename search. Pass debounced value. */
  q: string;
}

export interface DocumentsCatalogState {
  items: Document[];
  cursor: string | null;
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  loadMore: () => void;
  reload: () => void;
}

export function useDocumentsCatalog(opts: DocumentsCatalogOptions): DocumentsCatalogState {
  const { apiBaseUrl, refreshTick = 0, statuses, q } = opts;

  const [items, setItems] = useState<Document[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef<AbortController | null>(null);

  // Stable string keys for the filter inputs so the effect deps don't
  // re-run on every render (the parent passes a fresh array literal
  // each time). We compare on a sorted, joined form because the
  // server treats ``?status=A&status=B`` and ``?status=B&status=A``
  // identically.
  const statusKey = [...statuses].sort().join("|");

  const loadFirstPage = useCallback(() => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    setLoading(true);
    setError(null);
    listDocuments({
      limit: PAGE_LIMIT,
      baseUrl: apiBaseUrl,
      signal: controller.signal,
      status: statuses.length > 0 ? statuses : undefined,
      q,
    })
      .then((page) => {
        if (controller.signal.aborted) return;
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
              : "Failed to load catalog",
        );
        setItems([]);
        setCursor(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    // The ``statuses`` array itself is excluded from the dep list on
    // purpose — its derived ``statusKey`` represents the filter
    // identity. ``apiBaseUrl`` / ``q`` / ``refreshTick`` change in a
    // way React can compare, so they stay.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBaseUrl, q, refreshTick, statusKey]);

  useEffect(() => {
    loadFirstPage();
    return () => inFlight.current?.abort();
  }, [loadFirstPage]);

  const loadMore = useCallback(() => {
    if (!cursor) return;
    const controller = new AbortController();
    inFlight.current = controller;
    setLoading(true);
    listDocuments({
      limit: PAGE_LIMIT,
      cursor,
      baseUrl: apiBaseUrl,
      signal: controller.signal,
      status: statuses.length > 0 ? statuses : undefined,
      q,
    })
      .then((page) => {
        if (controller.signal.aborted) return;
        setItems((prev) => [...prev, ...page.items]);
        setCursor(page.next_cursor);
      })
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === "AbortError") return;
        setError(
          err instanceof ApiError
            ? `${err.code}: ${err.detail}`
            : err instanceof Error
              ? err.message
              : "Failed to load more",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
  }, [apiBaseUrl, cursor, q, statuses]);

  return {
    items,
    cursor,
    loading,
    error,
    hasMore: cursor !== null,
    loadMore,
    reload: loadFirstPage,
  };
}
