/**
 * useExtraction — fetch the cached `extraction.json` for a (doc, ver).
 *
 * Returns a discriminated `status` so the consumer can branch
 * unambiguously into "no extraction yet" (404 → "absent") versus a
 * real fetch failure ("error"). Most documents only have one
 * extraction per version, so we eagerly fetch on every (doc, ver)
 * change.
 */

import { useEffect, useState } from "react";

import { ApiError, getExtraction } from "../../api/client";
import type { ApiRawExtraction } from "../../api/types";

export type ExtractionStatus =
  | "idle"
  | "loading"
  | "ok"
  | "absent"
  | "error";

export interface UseExtractionResult {
  status: ExtractionStatus;
  extraction: ApiRawExtraction | null;
  error: Error | null;
  refetch: () => void;
}

export function useExtraction(
  documentId: string | null | undefined,
  versionId: string | null | undefined,
): UseExtractionResult {
  const [state, setState] = useState<Omit<UseExtractionResult, "refetch">>({
    status: documentId && versionId ? "loading" : "idle",
    extraction: null,
    error: null,
  });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!documentId || !versionId) {
      setState({ status: "idle", extraction: null, error: null });
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    setState((s) => ({ ...s, status: "loading", error: null }));

    getExtraction(documentId, versionId, { signal: controller.signal })
      .then((extraction) => {
        if (cancelled) return;
        setState({ status: "ok", extraction, error: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError && err.status === 404) {
          setState({ status: "absent", extraction: null, error: null });
          return;
        }
        const error =
          err instanceof ApiError || err instanceof Error
            ? err
            : new Error(String(err));
        setState({ status: "error", extraction: null, error });
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [documentId, versionId, tick]);

  return { ...state, refetch: () => setTick((n) => n + 1) };
}
