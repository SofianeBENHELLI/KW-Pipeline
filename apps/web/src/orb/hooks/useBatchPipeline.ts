/**
 * useBatchPipeline — drive the rail's "Run pipeline" multi-doc flow.
 *
 * Per design §3.7: when the user has documents selected and hits the
 * batch button, we transition each doc through queued → extracting →
 * semantic → done|failed with ~250ms stagger so progress is visible,
 * tally the final state, and surface a banner under the main grid
 * with `{done} done · {failed} failed · {in-flight} in-flight`.
 *
 * Backend reality: the design's `/documents/batch/transitions` endpoint
 * with WS streaming isn't shipped yet. PR 4 fans out per-doc calls
 * sequentially via the existing per-version endpoints
 * (`extractVersion` → `generateSemantic` → `validateVersion`) with the
 * staggered visual progress retained. When the WS endpoint lands, swap
 * the inner Promise chain for a single subscribe — the consumer
 * surface (state shape + banner) stays identical.
 */

import { useCallback, useState } from "react";

import {
  ApiError,
  extractVersion,
  generateSemantic,
  validateVersion,
} from "../../api/client";
import type { ApiDocument } from "../../api/types";
import { latestVersion } from "../review/format";

export type BatchStage =
  | "queued"
  | "extracting"
  | "semantic"
  | "done"
  | "failed";

export interface BatchFailure {
  docId: string;
  reason: string;
}

export interface BatchSnapshot {
  /** docId → stage. */
  progress: Map<string, BatchStage>;
  failures: BatchFailure[];
  /** Total docs queued in this run. */
  total: number;
}

export interface UseBatchPipelineResult {
  /** Current snapshot, or null when no run is in flight or recently dismissed. */
  snapshot: BatchSnapshot | null;
  /** Kick off a new batch run with the given docs. Resolves when all settle. */
  run: (docs: ApiDocument[]) => Promise<void>;
  /** Clear the snapshot (dismiss the banner). */
  dismiss: () => void;
}

interface RunOptions {
  /** Per-doc stagger in ms — keeps the banner readable. Defaults to 250. */
  staggerMs?: number;
  /** Test seam — lets unit tests skip the timers. */
  now?: () => number;
}

export function useBatchPipeline(
  options: RunOptions = {},
): UseBatchPipelineResult {
  const { staggerMs = 250 } = options;
  const [snapshot, setSnapshot] = useState<BatchSnapshot | null>(null);

  const updateProgress = useCallback(
    (docId: string, stage: BatchStage, failure?: BatchFailure) => {
      setSnapshot((s) => {
        if (!s) return s;
        const progress = new Map(s.progress);
        progress.set(docId, stage);
        const failures = failure ? [...s.failures, failure] : s.failures;
        return { ...s, progress, failures };
      });
    },
    [],
  );

  const run = useCallback(
    async (docs: ApiDocument[]) => {
      const filtered = docs.filter((d) => latestVersion(d));
      if (filtered.length === 0) return;
      const initial = new Map<string, BatchStage>();
      for (const d of filtered) initial.set(d.id, "queued");
      setSnapshot({ progress: initial, failures: [], total: filtered.length });

      const sleep = (ms: number) =>
        new Promise<void>((resolve) => setTimeout(resolve, ms));

      // Run each doc's pipeline sequentially with stagger so the
      // banner shows real progression. Concurrency is intentionally
      // limited — backend doesn't expose a bulk endpoint yet.
      for (let i = 0; i < filtered.length; i++) {
        const doc = filtered[i];
        const ver = latestVersion(doc);
        if (!ver) continue;
        if (i > 0) await sleep(staggerMs);
        try {
          updateProgress(doc.id, "extracting");
          await extractVersion(doc.id, ver.id);
          updateProgress(doc.id, "semantic");
          await generateSemantic(doc.id, ver.id);
          // Auto-validate is intentional only for the batch surface —
          // the design treats batch as the "trust the pipeline"
          // affordance. Single-doc Review tab still requires explicit
          // Validate clicks.
          await validateVersion(doc.id, ver.id);
          updateProgress(doc.id, "done");
        } catch (err) {
          const reason =
            err instanceof ApiError
              ? `${err.status} ${err.message}`
              : err instanceof Error
                ? err.message
                : String(err);
          updateProgress(doc.id, "failed", { docId: doc.id, reason });
        }
      }
    },
    [staggerMs, updateProgress],
  );

  const dismiss = useCallback(() => setSnapshot(null), []);

  return { snapshot, run, dismiss };
}
