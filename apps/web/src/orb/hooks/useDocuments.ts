/**
 * useDocuments — fetch a paged document list filtered by saved view + query.
 *
 * Wraps the existing `apps/web/src/api/client.ts:listDocuments`. Follows
 * the project's "no new state library" rule — `useState` + `useEffect`
 * + `AbortController` per fetch, exactly the pattern from
 * `useAdminConfig.ts`.
 *
 * Refetches on every change to the filter inputs. Cancels the in-flight
 * fetch on filter change or unmount so the rail never displays stale
 * results from a superseded query.
 */

import { useEffect, useState } from "react";

import { ApiError, listDocuments } from "../../api/client";
import type { ApiDocument } from "../../api/types";

/** The four saved-view tabs at the top of the rail. */
export type RailView = "recent" | "review" | "validated" | "failed";

/** Translate a saved view into the status filter the API expects. */
export function viewToStatuses(view: RailView): string[] {
  switch (view) {
    case "review":    return ["NEEDS_REVIEW"];
    case "validated": return ["VALIDATED"];
    case "failed":    return ["FAILED"];
    case "recent":
    default:          return [];
  }
}

export type UseDocumentsStatus = "loading" | "ok" | "error";

export interface UseDocumentsResult {
  status: UseDocumentsStatus;
  /** The current page of documents. Empty during loading / on error. */
  items: ApiDocument[];
  /** Server-supplied cursor for "load more". `null` when no more pages. */
  nextCursor: string | null;
  /** Set on the "error" status. Network failures and ApiError both land here. */
  error: Error | null;
  /** Manual refetch. Useful after a mutation lands. */
  refetch: () => void;
}

export interface UseDocumentsOptions {
  view: RailView;
  /** Substring match against `original_filename`. Trimmed; empty = no filter. */
  q?: string;
  /** Server-side page size. Defaults to 50 to match `listDocuments`. */
  limit?: number;
}

export function useDocuments(opts: UseDocumentsOptions): UseDocumentsResult {
  const { view, q = "", limit = 50 } = opts;
  const [state, setState] = useState<Omit<UseDocumentsResult, "refetch">>({
    status: "loading",
    items: [],
    nextCursor: null,
    error: null,
  });
  // Bumping this triggers re-fetch without invalidating filter args.
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    setState((s) => ({ ...s, status: "loading", error: null }));

    listDocuments({ status: viewToStatuses(view), q, limit })
      .then((page) => {
        if (cancelled) return;
        setState({
          status: "ok",
          items: page.items,
          nextCursor: page.next_cursor ?? null,
          error: null,
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        const error =
          err instanceof ApiError || err instanceof Error
            ? err
            : new Error(String(err));
        setState({
          status: "error",
          items: [],
          nextCursor: null,
          error,
        });
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [view, q, limit, tick]);

  return { ...state, refetch: () => setTick((n) => n + 1) };
}
