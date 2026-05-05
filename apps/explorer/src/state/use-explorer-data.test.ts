/**
 * Tests for the live-data orchestrator hook.
 *
 * Strategy: mock ``fetch`` and route requests by URL to canned bodies.
 * Each test asserts on the final ``ExplorerDataState`` reached by the
 * hook (sample fallback when the backend is empty / offline; live mode
 * when documents come back).
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useExplorerData } from "./use-explorer-data";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Route {
  match: RegExp;
  body: unknown;
  status?: number;
}

function mockRoutedFetch(routes: Route[]) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation((input: RequestInfo | URL): Promise<Response> => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      for (const r of routes) {
        if (r.match.test(url)) {
          return Promise.resolve(makeJsonResponse(r.body, r.status));
        }
      }
      return Promise.reject(new Error(`Unrouted fetch: ${url}`));
    });
}

function makeApiDoc(id: string, filename: string) {
  const versionId = `${id}-v1`;
  const created = new Date().toISOString();
  return {
    id,
    original_filename: filename,
    latest_version_id: versionId,
    created_at: created,
    versions: [
      {
        id: versionId,
        document_id: id,
        version_number: 1,
        filename,
        content_type: "application/pdf",
        file_size: 1024,
        sha256: `sha-${id}`,
        storage_uri: `file://${id}`,
        status: "VALIDATED",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: created,
      },
    ],
  };
}

describe("useExplorerData", () => {
  afterEach(() => vi.restoreAllMocks());

  it("falls back to the sample corpus when the backend has no documents", async () => {
    mockRoutedFetch([
      { match: /\/documents/, body: { items: [], next_cursor: null } },
    ]);

    const { result } = renderHook(() => useExplorerData("http://test", 0));

    await waitFor(() => expect(result.current.refreshing).toBe(false));
    expect(result.current.mode).toBe("sample-fallback");
    expect(result.current.snapshot.corpusLabel).toMatch(/empty/i);
    // Sample snapshot still ships with documents so the UI never renders blank.
    expect(result.current.snapshot.documents.length).toBeGreaterThan(0);
  });

  it("falls back to the sample corpus when the catalog request fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("DNS"));

    const { result } = renderHook(() => useExplorerData("http://test", 0));

    await waitFor(() => expect(result.current.refreshing).toBe(false));
    expect(result.current.mode).toBe("sample-fallback");
    expect(result.current.snapshot.corpusLabel).toMatch(/offline/i);
    expect(result.current.error).toMatch(/DNS/);
  });

  it("returns live mode when at least one document is in the catalog (knowledge layer 503 + missing semantic/extraction tolerated)", async () => {
    mockRoutedFetch([
      {
        match: /\/documents\?/,
        body: { items: [makeApiDoc("doc-1", "spec.pdf")], next_cursor: null },
      },
      // Knowledge layer disabled — hook tolerates 503 and renders live.
      {
        match: /\/knowledge\/graph/,
        status: 503,
        body: {
          error: { code: "KW_KNOWLEDGE_DISABLED", message: "off", retryable: false },
          detail: "Knowledge layer is disabled.",
        },
      },
      // Per-doc extraction / semantic missing — 404 is tolerated.
      {
        match: /\/extraction/,
        status: 404,
        body: { detail: "not found" },
      },
      {
        match: /\/semantic/,
        status: 404,
        body: { detail: "not found" },
      },
    ]);

    const { result } = renderHook(() => useExplorerData("http://test", 0));

    await waitFor(() => expect(result.current.mode).toBe("live"));
    expect(result.current.snapshot.documents).toHaveLength(1);
    expect(result.current.snapshot.documents[0].id).toBe("doc-1");
    expect(result.current.snapshot.corpusLabel).toMatch(/1 documents/);
    expect(result.current.error).toBeNull();
  });

  it("re-runs the orchestrator when refreshTick changes", async () => {
    const fetchSpy = mockRoutedFetch([
      { match: /\/documents/, body: { items: [], next_cursor: null } },
    ]);

    const { result, rerender } = renderHook(
      ({ tick }: { tick: number }) => useExplorerData("http://test", tick),
      { initialProps: { tick: 0 } },
    );

    await waitFor(() => expect(result.current.refreshing).toBe(false));
    const firstCount = fetchSpy.mock.calls.length;

    rerender({ tick: 1 });

    await waitFor(() => {
      expect(fetchSpy.mock.calls.length).toBeGreaterThan(firstCount);
    });
  });
});
