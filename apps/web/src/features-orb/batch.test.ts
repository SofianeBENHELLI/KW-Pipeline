/**
 * Phase-3 batch helpers — pure-function pin tests. The orchestration in
 * runBatchPipeline is exercised by the integration test against the
 * mocked api/client; here we just guarantee the post-run selection
 * prune keeps failures sticky.
 */

import { describe, expect, it } from "vitest";

import { pruneSelectionAfterBatch, type BatchSnapshot } from "./batch";

describe("pruneSelectionAfterBatch", () => {
  it("drops succeeded ids and keeps failures selected", () => {
    const snapshot: BatchSnapshot = {
      progress: {
        a: { stage: "done" },
        b: { stage: "failed", reason: "boom" },
        c: { stage: "done" },
      },
      failures: [{ document_id: "b", filename: "b.pdf", reason: "boom" }],
    };
    const next = pruneSelectionAfterBatch(new Set(["a", "b", "c"]), snapshot);
    expect([...next]).toEqual(["b"]);
  });

  it("keeps ids without a progress entry (defensive — never observed in practice)", () => {
    const snapshot: BatchSnapshot = { progress: {}, failures: [] };
    const next = pruneSelectionAfterBatch(new Set(["x"]), snapshot);
    expect([...next]).toEqual(["x"]);
  });

  it("returns an empty set when everything succeeded", () => {
    const snapshot: BatchSnapshot = {
      progress: { a: { stage: "done" }, b: { stage: "done" } },
      failures: [],
    };
    const next = pruneSelectionAfterBatch(new Set(["a", "b"]), snapshot);
    expect(next.size).toBe(0);
  });
});
