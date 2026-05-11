import { ApiError, extractVersion, generateSemantic } from "../api/client";
import type { components } from "../api/generated/schema";
import { latestVersion } from "../domain/document";

type ApiDocument = components["schemas"]["Document"];

export type BatchStage = "queued" | "extracting" | "semantic" | "done" | "failed";

export interface BatchEntry {
  stage: BatchStage;
  reason?: string;
}

export interface BatchFailure {
  document_id: string;
  filename: string;
  reason: string;
}

export type BatchProgress = Record<string, BatchEntry>;

export type BatchUpdate = (next: BatchProgress | ((prev: BatchProgress) => BatchProgress)) => void;

/**
 * Sequential extract → semantic across the selected docs. Same shape as
 * the mockup's mock `runBatch` but uses the real `api/client.ts`
 * primitives. Sequential because the backend default is one extraction
 * worker — parallel client-side just queues server-side.
 */
export async function runBatch(
  targets: ApiDocument[],
  setProgress: BatchUpdate,
): Promise<{ progress: BatchProgress; failures: BatchFailure[] }> {
  const initial: BatchProgress = {};
  for (const doc of targets) initial[doc.id] = { stage: "queued" };
  setProgress(() => ({ ...initial }));

  let progress: BatchProgress = { ...initial };
  const failures: BatchFailure[] = [];

  for (const doc of targets) {
    const version = latestVersion(doc);
    if (!version) {
      progress = { ...progress, [doc.id]: { stage: "failed", reason: "no version" } };
      setProgress(progress);
      failures.push({ document_id: doc.id, filename: doc.original_filename, reason: "no version" });
      continue;
    }
    try {
      progress = { ...progress, [doc.id]: { stage: "extracting" } };
      setProgress(progress);
      await extractVersion(doc.id, version.id);

      progress = { ...progress, [doc.id]: { stage: "semantic" } };
      setProgress(progress);
      await generateSemantic(doc.id, version.id);

      progress = { ...progress, [doc.id]: { stage: "done" } };
      setProgress(progress);
    } catch (err) {
      const reason =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      progress = { ...progress, [doc.id]: { stage: "failed", reason } };
      setProgress(progress);
      failures.push({ document_id: doc.id, filename: doc.original_filename, reason });
    }
  }

  return { progress, failures };
}
