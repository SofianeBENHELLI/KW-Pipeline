/**
 * useBatchPipeline tests — drive the per-doc stage transitions and
 * verify the snapshot accumulates done/failed counts correctly.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiDocument } from "../../api/types";
import { useBatchPipeline } from "./useBatchPipeline";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function fixtureDoc(id: string): ApiDocument {
  return {
    id,
    original_filename: `${id}.txt`,
    latest_version_id: `ver-${id}`,
    created_at: "2026-05-12T08:00:00Z",
    archived_at: null,
    scopes: [],
    versions: [
      {
        id: `ver-${id}`,
        document_id: id,
        version_number: 1,
        filename: `${id}.txt`,
        content_type: "text/plain",
        file_size: 100,
        sha256: "h",
        storage_uri: "file://x",
        status: "STORED",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: "2026-05-12T08:00:00Z",
      },
    ],
  };
}

describe("useBatchPipeline", () => {
  afterEach(() => vi.restoreAllMocks());

  beforeEach(() => {
    // Use mockImplementation so each fetch call gets a FRESH Response.
    // Reusing the same Response across calls trips Body-already-read
    // when the api-core retry helper peeks at it.
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse({ ok: true })),
    );
  });

  it("starts as null snapshot", () => {
    const { result } = renderHook(() => useBatchPipeline({ staggerMs: 0 }));
    expect(result.current.snapshot).toBeNull();
  });

  it("runs each doc through queued → extracting → semantic → done", async () => {
    const { result } = renderHook(() => useBatchPipeline({ staggerMs: 0 }));
    await act(async () => {
      await result.current.run([fixtureDoc("a"), fixtureDoc("b")]);
    });
    await waitFor(() => {
      expect(result.current.snapshot?.progress.get("a")).toBe("done");
    });
    expect(result.current.snapshot?.progress.get("b")).toBe("done");
    expect(result.current.snapshot?.failures).toEqual([]);
    expect(result.current.snapshot?.total).toBe(2);
  });

  it("captures failures from the underlying fetch", async () => {
    let calls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      calls += 1;
      // Fail on the second doc's extract call (3rd network call:
      // 1=docA extract, 2=docA semantic, 3=docA validate, 4=docB
      // extract → boom).
      if (calls === 4) return Promise.reject(new Error("network down"));
      return Promise.resolve(makeJsonResponse({ ok: true }));
    });
    const { result } = renderHook(() => useBatchPipeline({ staggerMs: 0 }));
    await act(async () => {
      await result.current.run([fixtureDoc("a"), fixtureDoc("b")]);
    });
    await waitFor(() => {
      expect(result.current.snapshot?.progress.get("b")).toBe("failed");
    });
    expect(result.current.snapshot?.failures).toEqual([
      { docId: "b", reason: "network down" },
    ]);
  });

  it("dismiss clears the snapshot", async () => {
    const { result } = renderHook(() => useBatchPipeline({ staggerMs: 0 }));
    await act(async () => {
      await result.current.run([fixtureDoc("a")]);
    });
    expect(result.current.snapshot).not.toBeNull();
    act(() => result.current.dismiss());
    expect(result.current.snapshot).toBeNull();
  });

  it("ignores docs that have no version", async () => {
    const ghost: ApiDocument = { ...fixtureDoc("g"), versions: [] };
    const { result } = renderHook(() => useBatchPipeline({ staggerMs: 0 }));
    await act(async () => {
      await result.current.run([ghost]);
    });
    expect(result.current.snapshot).toBeNull();
  });
});
