import { ApiError, extractVersion, generateSemantic } from "../api/client";
import type { ApiDocument } from "../api/types";
import { latestVersion } from "../domain/document";

export type BatchStage = "queued" | "extracting" | "semantic" | "done" | "failed";

export interface BatchProgressEntry {
  stage: BatchStage;
  reason?: string;
}

export interface BatchFailure {
  document_id: string;
  filename: string;
  reason: string;
}

export interface BatchSnapshot {
  progress: Record<string, BatchProgressEntry>;
  failures: BatchFailure[];
}

export type BatchUpdater = (
  next: BatchSnapshot | ((prev: BatchSnapshot) => BatchSnapshot),
) => void;

/**
 * Phase-3 batch runner — sequentially extracts + runs semantic generation
 * on every target document. Idempotent failure handling: any doc that
 * blows up is added to `failures` with its filename + error message,
 * and its progress entry stays as `failed` so the catalog row reflects
 * the state visually. Caller is responsible for refreshing the catalog
 * after we return.
 *
 * Why sequential instead of parallel: the backend is single-worker by
 * default (`KW_EXTRACTION_WORKERS=1`) so parallelizing client-side just
 * causes queue contention without changing wall-clock time.
 */
export async function runBatchPipeline(
  targets: ApiDocument[],
  setSnapshot: BatchUpdater,
): Promise<BatchSnapshot> {
  // Initialize all targets at "queued" so the UI renders something
  // immediately when N is large.
  setSnapshot(() => {
    const progress: Record<string, BatchProgressEntry> = {};
    for (const doc of targets) progress[doc.id] = { stage: "queued" };
    return { progress, failures: [] };
  });

  let result: BatchSnapshot = {
    progress: Object.fromEntries(targets.map((doc) => [doc.id, { stage: "queued" as BatchStage }])),
    failures: [],
  };

  for (const doc of targets) {
    const version = latestVersion(doc);
    if (!version) {
      result = {
        ...result,
        progress: { ...result.progress, [doc.id]: { stage: "failed", reason: "no version" } },
        failures: [...result.failures, { document_id: doc.id, filename: doc.original_filename, reason: "no version" }],
      };
      setSnapshot(result);
      continue;
    }

    try {
      result = { ...result, progress: { ...result.progress, [doc.id]: { stage: "extracting" } } };
      setSnapshot(result);
      await extractVersion(doc.id, version.id);

      result = { ...result, progress: { ...result.progress, [doc.id]: { stage: "semantic" } } };
      setSnapshot(result);
      await generateSemantic(doc.id, version.id);

      result = { ...result, progress: { ...result.progress, [doc.id]: { stage: "done" } } };
      setSnapshot(result);
    } catch (err) {
      const reason =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      result = {
        ...result,
        progress: { ...result.progress, [doc.id]: { stage: "failed", reason } },
        failures: [...result.failures, { document_id: doc.id, filename: doc.original_filename, reason }],
      };
      setSnapshot(result);
    }
  }

  return result;
}

/**
 * After a batch completes, drop succeeded ids from the selection but
 * keep failed ones so the operator can retry without re-selecting.
 * Pure function so it can be tested in isolation.
 */
export function pruneSelectionAfterBatch(
  selection: ReadonlySet<string>,
  snapshot: BatchSnapshot,
): Set<string> {
  const next = new Set<string>();
  for (const id of selection) {
    const entry = snapshot.progress[id];
    if (!entry || entry.stage === "failed") next.add(id);
  }
  return next;
}
