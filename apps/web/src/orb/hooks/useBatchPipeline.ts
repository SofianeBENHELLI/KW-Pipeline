/**
 * useBatchPipeline — drive the rail's "Run pipeline" multi-doc flow.
 *
 * Per design §3.7: when the user has documents selected and hits the
 * batch button, we transition each doc through queued → extracting →
 * semantic → done|failed with ~250ms stagger so progress is visible,
 * tally the final state, and surface a banner under the main grid
 * with `{done} done · {failed} failed · {in-flight} in-flight`.
 *
 * Backend reality
 * ---------------
 * The design's `/documents/batch/transitions` endpoint with WS
 * streaming isn't shipped yet. We fan out per-doc calls sequentially
 * via the existing per-version endpoints
 * (`extractVersion` → `generateSemantic` → `validateVersion`).
 *
 * Async-extraction caveat: with `KW_EXTRACTION_INLINE=false` (the
 * production default for large parsers), `extractVersion` returns
 * 202 immediately while the parser runs in a background queue.
 * Calling `generateSemantic` straight away then 404s on
 * "Raw extraction not found.". We poll `getDocument()` between steps
 * until the version's status reaches the next gate (EXTRACTED for
 * semantic, SEMANTIC_READY/NEEDS_REVIEW for validate). FAILED short-
 * circuits the chain with the backend's `failure_reason` surfaced in
 * the banner.
 *
 * When the bulk-transitions endpoint lands, swap the inner Promise
 * chain for a single subscribe — the consumer surface (state shape +
 * banner) stays identical.
 */

import { useCallback, useState } from "react";

import {
  ApiError,
  extractVersion,
  generateSemantic,
  getDocument,
  validateVersion,
} from "../../api/client";
import type { ApiDocument, ApiDocumentVersion } from "../../api/types";
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
  /** How long to poll between status checks. Defaults to 1500 ms. */
  pollIntervalMs?: number;
  /** Total time to wait for a state transition before giving up. */
  pollTimeoutMs?: number;
}

const DEFAULT_POLL_INTERVAL_MS = 1500;
const DEFAULT_POLL_TIMEOUT_MS = 60_000; // 60s — plenty for normal parsers

/**
 * Statuses that mean "extraction is done, you can call semantic now."
 * EXTRACTED is the canonical post-extract state; the later semantic
 * states are also acceptable because the backend is happy to re-run
 * a semantic pass on top of an existing one.
 */
const EXTRACTION_READY: ReadonlySet<string> = new Set([
  "EXTRACTED",
  "SEMANTIC_READY",
  "NEEDS_REVIEW",
  "VALIDATED",
]);

/**
 * Statuses that mean "semantic output is ready, you can validate now."
 */
const SEMANTIC_READY: ReadonlySet<string> = new Set([
  "SEMANTIC_READY",
  "NEEDS_REVIEW",
  "VALIDATED",
]);

/** Terminal failure state — short-circuit with `failure_reason`. */
function isTerminalFailure(version: ApiDocumentVersion | null): boolean {
  if (!version) return false;
  return version.status === "FAILED" || version.status === "REJECTED";
}

interface PollOptions {
  documentId: string;
  versionId: string;
  /** Names statuses we're waiting for. Polling resolves on first match. */
  isReady: (status: string) => boolean;
  pollIntervalMs: number;
  pollTimeoutMs: number;
}

/**
 * Poll `GET /documents/{id}` until the matching version's status
 * passes `isReady` or hits a terminal-failure state. Throws on
 * timeout or terminal failure (with the backend-supplied
 * `failure_reason` when present).
 */
async function pollUntilReady(opts: PollOptions): Promise<void> {
  const start = Date.now();
  for (;;) {
    const doc = await getDocument(opts.documentId);
    const version = doc.versions.find((v) => v.id === opts.versionId) ?? null;
    if (version && opts.isReady(version.status)) return;
    if (isTerminalFailure(version)) {
      const reason =
        version?.failure_reason ??
        `pipeline ended with status ${version?.status ?? "unknown"}`;
      throw new Error(reason);
    }
    if (Date.now() - start > opts.pollTimeoutMs) {
      throw new Error(
        `pipeline did not reach the next state within ${Math.round(
          opts.pollTimeoutMs / 1000,
        )}s — current status: ${version?.status ?? "unknown"}`,
      );
    }
    await new Promise<void>((resolve) =>
      setTimeout(resolve, opts.pollIntervalMs),
    );
  }
}

export function useBatchPipeline(
  options: RunOptions = {},
): UseBatchPipelineResult {
  const {
    staggerMs = 250,
    pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
    pollTimeoutMs = DEFAULT_POLL_TIMEOUT_MS,
  } = options;
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
          // 1. Trigger extraction. Returns RawExtraction synchronously
          // (KW_EXTRACTION_INLINE=true) or ExtractionJobSnapshot 202
          // (async). Either way, we then poll until the version's
          // status indicates extraction is done — that's the only
          // contract that lets us safely call semantic next.
          //
          // Skip the call if the doc is already past EXTRACTED — the
          // user might run the batch on a partially-progressed set.
          if (!EXTRACTION_READY.has(ver.status)) {
            await extractVersion(doc.id, ver.id);
            await pollUntilReady({
              documentId: doc.id,
              versionId: ver.id,
              isReady: (s) => EXTRACTION_READY.has(s),
              pollIntervalMs,
              pollTimeoutMs,
            });
          }

          updateProgress(doc.id, "semantic");
          // 2. Generate the semantic document.
          if (!SEMANTIC_READY.has(ver.status)) {
            await generateSemantic(doc.id, ver.id);
            await pollUntilReady({
              documentId: doc.id,
              versionId: ver.id,
              isReady: (s) => SEMANTIC_READY.has(s),
              pollIntervalMs,
              pollTimeoutMs,
            });
          }

          // 3. Auto-validate. Intentional only for the batch surface —
          // the design treats batch as the "trust the pipeline"
          // affordance. Single-doc Review tab still requires explicit
          // Validate clicks.
          if (ver.status !== "VALIDATED") {
            await validateVersion(doc.id, ver.id);
          }
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
    [staggerMs, pollIntervalMs, pollTimeoutMs, updateProgress],
  );

  const dismiss = useCallback(() => setSnapshot(null), []);

  return { snapshot, run, dismiss };
}
