/**
 * Hook contract tests for ``useExploreSearch``. The component-side
 * tests in ``SearchResults.test.tsx`` exercise the rendered output
 * for every state; this module pins the underlying state-machine
 * behaviour (debounce, abort, disabled detection, empty detection).
 *
 * Uses real timers with a tiny ``debounceMs`` so the assertions
 * land deterministically without fighting React's microtask flush
 * under ``vi.useFakeTimers``.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useExploreSearch } from "./use-explore-search";
import type { ExploreSearchResponse } from "../api/types";

const POPULATED_RESPONSE: ExploreSearchResponse = {
  schema_version: "v0.1",
  query: "policy",
  embedding_model: "voyage-3",
  chunks: [
    {
      chunk_id: "c-1",
      document_id: "d-1",
      version_id: "v-1",
      section_id: "s-1",
      snippet: "Reviewer must validate every claim.",
      score: 0.91,
      validation_status: null,
      is_source_backed: false,
    },
  ],
  documents: [
    {
      document_id: "d-1",
      title: "Supplier policy",
      score: 0.94,
      validation_status: "VALIDATED",
      is_source_backed: false,
      contributing_chunks: [],
    },
  ],
  topics: [
    { topic_id: "t-1", label: "Compliance", keywords: ["audit"], score: 0.81, evidence_chunks: [] },
  ],
  entities: [],
  relations: [],
};

const EMPTY_RESPONSE: ExploreSearchResponse = {
  schema_version: "v0.1",
  query: "noresults",
  embedding_model: "voyage-3",
  chunks: [],
  documents: [],
  topics: [],
  entities: [],
  relations: [],
};

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const FAST_DEBOUNCE = 10; // ms — fast enough that waitFor lands within its default 1s budget.

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useExploreSearch", () => {
  it("starts idle and short-circuits empty / whitespace queries", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useExploreSearch("   ", { debounceMs: FAST_DEBOUNCE }));
    expect(result.current.state).toBe("idle");
    await new Promise((r) => setTimeout(r, FAST_DEBOUNCE * 4));
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(result.current.state).toBe("idle");
  });

  it("debounces typing — one fetch per quiet window, not per keystroke", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(makeJsonResponse(POPULATED_RESPONSE));

    const { rerender, result } = renderHook(
      ({ q }) => useExploreSearch(q, { debounceMs: FAST_DEBOUNCE }),
      { initialProps: { q: "p" } },
    );
    rerender({ q: "po" });
    rerender({ q: "pol" });
    rerender({ q: "policy" });

    await waitFor(() => expect(result.current.state).toBe("data"));
    // Exactly one fetch — keystrokes earlier in the window were
    // collapsed by the trailing-edge debounce.
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const lastCall = fetchSpy.mock.calls[0];
    expect(String(lastCall[0])).toContain("q=policy");
    expect(result.current.response?.documents[0].document_id).toBe("d-1");
  });

  it("flags 'empty' state when the response has zero hits across every group", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeJsonResponse(EMPTY_RESPONSE));
    const { result } = renderHook(() =>
      useExploreSearch("noresults", { debounceMs: FAST_DEBOUNCE }),
    );
    await waitFor(() => expect(result.current.state).toBe("empty"));
    expect(result.current.response).not.toBeNull();
  });

  it("flags 'disabled' state on KW_VECTOR_SEARCH_DISABLED 503 envelope", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          error: {
            code: "KW_VECTOR_SEARCH_DISABLED",
            message: "Vector search is disabled.",
            status: 503,
            retryable: false,
            remediation: "Set VOYAGE_API_KEY.",
          },
        },
        503,
      ),
    );

    const { result } = renderHook(() =>
      useExploreSearch("anything", { debounceMs: FAST_DEBOUNCE }),
    );
    await waitFor(() => expect(result.current.state).toBe("disabled"));
    expect(result.current.error).toBeNull();
    expect(result.current.response).toBeNull();
  });

  it("flags 'error' state on non-503 failures with the message preserved", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network error"));
    const { result } = renderHook(() =>
      useExploreSearch("anything", { debounceMs: FAST_DEBOUNCE }),
    );
    await waitFor(() => expect(result.current.state).toBe("error"));
    expect(result.current.error).toBe("Network error");
  });

  it("clearing the query resets to idle", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeJsonResponse(POPULATED_RESPONSE));
    const { rerender, result } = renderHook(
      ({ q }) => useExploreSearch(q, { debounceMs: FAST_DEBOUNCE }),
      { initialProps: { q: "policy" } },
    );
    await waitFor(() => expect(result.current.state).toBe("data"));

    rerender({ q: "" });
    await waitFor(() => expect(result.current.state).toBe("idle"));
  });
});
