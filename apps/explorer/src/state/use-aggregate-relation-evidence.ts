/**
 * On-demand fetch hook for aggregated doc→doc relation evidence
 * (#318 partial — drawer surface from the DetailPanel "Related
 * Documents" list).
 *
 * Differs from ``useExploreSearch`` in two ways: the request is
 * keyed by an ordered (source_doc, target_doc) tuple rather than a
 * free-text query, and there is no debounce — the call fires once
 * per pair when the drawer opens. The state machine still mirrors
 * the search hook so consumers can switch on the same
 * ``idle/loading/data/empty/error`` shape.
 *
 * Empty (``state === "empty"``) maps to the backend's 404
 * ``KW_NOT_FOUND`` response, which means the projection didn't
 * materialise a cross-document edge. Surface that as a friendly
 * "no shared evidence" rather than an error — it's expected when
 * the user opens the drawer for two unrelated docs.
 */

import { useEffect, useRef, useState } from "react";

import { ApiError, getAggregateRelationEvidence } from "../api/client";
import type { AggregatedRelationEvidence } from "../api/types";

const DEFAULT_TOP_N = 5;

export type AggregateRelationEvidenceState =
  | "idle"
  | "loading"
  | "data"
  | "empty"
  | "error";

export interface AggregateRelationEvidenceSnapshot {
  state: AggregateRelationEvidenceState;
  /** Populated only when ``state === "data"``. */
  evidence: AggregatedRelationEvidence | null;
  /** Populated only when ``state === "error"``. */
  error: ApiError | string | null;
}

export interface UseAggregateRelationEvidenceOptions {
  apiBaseUrl?: string;
  topN?: number;
}

const IDLE_SNAPSHOT: AggregateRelationEvidenceSnapshot = {
  state: "idle",
  evidence: null,
  error: null,
};

export function useAggregateRelationEvidence(
  pair: { sourceDocumentId: string; targetDocumentId: string } | null,
  options: UseAggregateRelationEvidenceOptions = {},
): AggregateRelationEvidenceSnapshot {
  const { apiBaseUrl, topN = DEFAULT_TOP_N } = options;
  const [snapshot, setSnapshot] =
    useState<AggregateRelationEvidenceSnapshot>(IDLE_SNAPSHOT);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (pair === null) {
      abortRef.current?.abort();
      abortRef.current = null;
      setSnapshot(IDLE_SNAPSHOT);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setSnapshot({ state: "loading", evidence: null, error: null });

    getAggregateRelationEvidence(pair.sourceDocumentId, pair.targetDocumentId, {
      topN,
      baseUrl: apiBaseUrl,
      signal: controller.signal,
    })
      .then((evidence) => {
        if (controller.signal.aborted) return;
        if (evidence === null) {
          setSnapshot({ state: "empty", evidence: null, error: null });
          return;
        }
        setSnapshot({ state: "data", evidence, error: null });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if ((err as { name?: string })?.name === "AbortError") return;
        setSnapshot({
          state: "error",
          evidence: null,
          error:
            err instanceof ApiError
              ? err
              : err instanceof Error
                ? err.message
                : "Failed to load evidence.",
        });
      });

    return () => {
      controller.abort();
    };
  }, [pair?.sourceDocumentId, pair?.targetDocumentId, apiBaseUrl, topN]);

  return snapshot;
}
