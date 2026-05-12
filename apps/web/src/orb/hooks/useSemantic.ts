/**
 * useSemantic — fetch the cached `semantic.json` (and rendered
 * Markdown) for a (doc, ver).
 *
 * Returns the parsed semantic document plus the rendered Markdown
 * string. 404 surfaces as `status: "absent"` so the Review tab can
 * gate the "Semantic" button correctly. Markdown is fetched in
 * parallel for the preview pane.
 */

import { useEffect, useState } from "react";

import { ApiError, getMarkdown, getSemantic } from "../../api/client";
import type { ApiSemanticDocument } from "../../api/types";

export type SemanticStatus =
  | "idle"
  | "loading"
  | "ok"
  | "absent"
  | "error";

export interface UseSemanticResult {
  status: SemanticStatus;
  semantic: ApiSemanticDocument | null;
  markdown: string | null;
  error: Error | null;
  refetch: () => void;
}

export function useSemantic(
  documentId: string | null | undefined,
  versionId: string | null | undefined,
): UseSemanticResult {
  const [state, setState] = useState<Omit<UseSemanticResult, "refetch">>({
    status: documentId && versionId ? "loading" : "idle",
    semantic: null,
    markdown: null,
    error: null,
  });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!documentId || !versionId) {
      setState({ status: "idle", semantic: null, markdown: null, error: null });
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    setState((s) => ({ ...s, status: "loading", error: null }));

    Promise.allSettled([
      getSemantic(documentId, versionId, { signal: controller.signal }),
      getMarkdown(documentId, versionId),
    ])
      .then(([sem, md]) => {
        if (cancelled) return;
        // The semantic endpoint returns the canonical state for this
        // hook. The markdown call is a nice-to-have for the preview;
        // a 404 there just shows the JSON in the source tab.
        if (sem.status === "rejected") {
          const reason = sem.reason as unknown;
          if (reason instanceof DOMException && reason.name === "AbortError") {
            return;
          }
          if (reason instanceof ApiError && reason.status === 404) {
            setState({
              status: "absent",
              semantic: null,
              markdown: null,
              error: null,
            });
            return;
          }
          const error =
            reason instanceof ApiError || reason instanceof Error
              ? reason
              : new Error(String(reason));
          setState({
            status: "error",
            semantic: null,
            markdown: null,
            error,
          });
          return;
        }
        const markdown = md.status === "fulfilled" ? md.value : null;
        setState({
          status: "ok",
          semantic: sem.value,
          markdown,
          error: null,
        });
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [documentId, versionId, tick]);

  return { ...state, refetch: () => setTick((n) => n + 1) };
}
