/**
 * useDocumentDetail — fetch a single document by id.
 *
 * Wraps `getDocument(documentId)`. Used by the main pane in the Review
 * Workspace to render the title, status, scope, and version metadata.
 * 404s surface as `status: "not-found"` so the page can render the
 * "deep-link error" banner without a generic error fallback.
 */

import { useEffect, useState } from "react";

import { ApiError, getDocument } from "../../api/client";
import type { ApiDocument } from "../../api/types";

export type DocumentDetailStatus =
  | "idle"
  | "loading"
  | "ok"
  | "not-found"
  | "error";

export interface UseDocumentDetailResult {
  status: DocumentDetailStatus;
  document: ApiDocument | null;
  error: Error | null;
  refetch: () => void;
}

export function useDocumentDetail(
  documentId: string | null | undefined,
): UseDocumentDetailResult {
  const [state, setState] = useState<Omit<UseDocumentDetailResult, "refetch">>({
    status: documentId ? "loading" : "idle",
    document: null,
    error: null,
  });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!documentId) {
      setState({ status: "idle", document: null, error: null });
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    setState((s) => ({ ...s, status: "loading", error: null }));

    getDocument(documentId, { signal: controller.signal })
      .then((doc) => {
        if (cancelled) return;
        setState({ status: "ok", document: doc, error: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError && err.status === 404) {
          setState({ status: "not-found", document: null, error: err });
          return;
        }
        const error =
          err instanceof ApiError || err instanceof Error
            ? err
            : new Error(String(err));
        setState({ status: "error", document: null, error });
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [documentId, tick]);

  return { ...state, refetch: () => setTick((n) => n + 1) };
}
