/**
 * useBatchPipeline tests — drive the per-doc stage transitions and
 * verify the snapshot accumulates done/failed counts correctly.
 *
 * The hook polls `getDocument()` between extract → semantic and
 * semantic → validate to honour async-extraction backends. Tests
 * mock both the POST endpoints (always return 200 ok) AND the GET
 * endpoint, returning a doc whose version status flips on each
 * call so polling resolves on the first read.
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

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

type DocStatus = ApiDocument["versions"][number]["status"];

function fixtureDoc(id: string, status: DocStatus = "STORED"): ApiDocument {
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
        status,
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: "2026-05-12T08:00:00Z",
      },
    ],
  };
}

/**
 * Build a fetch mock that:
 *   - returns `getDocResponse(docId)` for `GET /documents/{docId}`
 *   - returns 200 ok for every POST (extract / semantic / validate)
 *
 * `getDocResponse` is called fresh on each request so the test can
 * advance the doc's status across polls (first call: EXTRACTED;
 * second call: SEMANTIC_READY; etc).
 */
function installFetchMock(
  getDocResponse: (docId: string) => ApiDocument,
): void {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    (input: RequestInfo | URL): Promise<Response> => {
      const url = urlOf(input);
      const docMatch = url.match(/\/documents\/([^/?]+)$/);
      if (docMatch) {
        return Promise.resolve(makeJsonResponse(getDocResponse(docMatch[1])));
      }
      return Promise.resolve(makeJsonResponse({ ok: true }));
    },
  );
}

describe("useBatchPipeline", () => {
  afterEach(() => vi.restoreAllMocks());

  beforeEach(() => {
    // Default: every POST + every GET returns a doc that's already
    // VALIDATED so polling completes immediately and the chain runs
    // through extract → semantic → validate without waiting.
    installFetchMock((id) => fixtureDoc(id, "VALIDATED"));
  });

  it("starts as null snapshot", () => {
    const { result } = renderHook(() =>
      useBatchPipeline({ staggerMs: 0, pollIntervalMs: 0 }),
    );
    expect(result.current.snapshot).toBeNull();
  });

  it("runs each doc through queued → extracting → semantic → done", async () => {
    const { result } = renderHook(() =>
      useBatchPipeline({ staggerMs: 0, pollIntervalMs: 0 }),
    );
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

  it("polls between extract and semantic until status reaches EXTRACTED", async () => {
    // Polling sequence: 1st GET = EXTRACTING (still in flight), 2nd = EXTRACTED.
    // Then 3rd = SEMANTIC_READY (semantic poll resolves), 4th = VALIDATED.
    const statuses: DocStatus[] = [
      "EXTRACTING",
      "EXTRACTED",
      "SEMANTIC_READY",
      "VALIDATED",
    ];
    let cursor = 0;
    installFetchMock((id) => {
      const status = statuses[Math.min(cursor, statuses.length - 1)];
      cursor += 1;
      return fixtureDoc(id, status);
    });
    const { result } = renderHook(() =>
      useBatchPipeline({ staggerMs: 0, pollIntervalMs: 0, pollTimeoutMs: 5_000 }),
    );
    await act(async () => {
      await result.current.run([fixtureDoc("a")]);
    });
    expect(result.current.snapshot?.progress.get("a")).toBe("done");
  });

  it("surfaces FAILED with the backend's failure_reason verbatim", async () => {
    installFetchMock((id) => ({
      ...fixtureDoc(id, "FAILED"),
      versions: [
        {
          ...fixtureDoc(id, "FAILED").versions[0],
          status: "FAILED",
          failure_reason: "tesseract: page-7 OCR confidence below threshold",
        },
      ],
    }));
    const { result } = renderHook(() =>
      useBatchPipeline({ staggerMs: 0, pollIntervalMs: 0, pollTimeoutMs: 5_000 }),
    );
    await act(async () => {
      await result.current.run([fixtureDoc("a")]);
    });
    expect(result.current.snapshot?.progress.get("a")).toBe("failed");
    expect(result.current.snapshot?.failures).toEqual([
      {
        docId: "a",
        reason: "tesseract: page-7 OCR confidence below threshold",
      },
    ]);
  });

  it("captures failures from the underlying POST", async () => {
    let calls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        calls += 1;
        const url = urlOf(input);
        const docMatch = url.match(/\/documents\/([^/?]+)$/);
        if (docMatch) {
          return Promise.resolve(
            makeJsonResponse(fixtureDoc(docMatch[1], "VALIDATED")),
          );
        }
        // First doc's extract POST is fine; second doc's extract POST
        // throws. (The exact call number depends on poll cycles —
        // failing on URL match for "ver-b" is more deterministic.)
        if (url.includes("/ver-b/")) {
          return Promise.reject(new Error("network down"));
        }
        return Promise.resolve(makeJsonResponse({ ok: true }));
      },
    );
    const { result } = renderHook(() =>
      useBatchPipeline({ staggerMs: 0, pollIntervalMs: 0 }),
    );
    await act(async () => {
      await result.current.run([fixtureDoc("a"), fixtureDoc("b")]);
    });
    expect(result.current.snapshot?.progress.get("a")).toBe("done");
    expect(result.current.snapshot?.progress.get("b")).toBe("failed");
    expect(result.current.snapshot?.failures).toEqual([
      { docId: "b", reason: "network down" },
    ]);
    // Sanity: the polling adds calls — just confirm the per-doc
    // chain ran (we don't pin exact counts because retry/poll cycles
    // depend on internal sleep timers).
    expect(calls).toBeGreaterThan(2);
  });

  it("dismiss clears the snapshot", async () => {
    const { result } = renderHook(() =>
      useBatchPipeline({ staggerMs: 0, pollIntervalMs: 0 }),
    );
    await act(async () => {
      await result.current.run([fixtureDoc("a")]);
    });
    expect(result.current.snapshot).not.toBeNull();
    act(() => result.current.dismiss());
    expect(result.current.snapshot).toBeNull();
  });

  it("ignores docs that have no version", async () => {
    const ghost: ApiDocument = { ...fixtureDoc("g"), versions: [] };
    const { result } = renderHook(() =>
      useBatchPipeline({ staggerMs: 0, pollIntervalMs: 0 }),
    );
    await act(async () => {
      await result.current.run([ghost]);
    });
    expect(result.current.snapshot).toBeNull();
  });

  it("skips already-VALIDATED docs without re-running the chain", async () => {
    let postCalls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        const docMatch = url.match(/\/documents\/([^/?]+)$/);
        if (docMatch) {
          return Promise.resolve(
            makeJsonResponse(fixtureDoc(docMatch[1], "VALIDATED")),
          );
        }
        postCalls += 1;
        return Promise.resolve(makeJsonResponse({ ok: true }));
      },
    );
    const { result } = renderHook(() =>
      useBatchPipeline({ staggerMs: 0, pollIntervalMs: 0 }),
    );
    await act(async () => {
      await result.current.run([fixtureDoc("a", "VALIDATED")]);
    });
    expect(result.current.snapshot?.progress.get("a")).toBe("done");
    // No POSTs fired because the doc's initial status is already
    // VALIDATED — extract/semantic/validate are all skipped.
    expect(postCalls).toBe(0);
  });
});
