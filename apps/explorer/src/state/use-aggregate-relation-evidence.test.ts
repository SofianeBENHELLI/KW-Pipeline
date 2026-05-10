/**
 * Hook contract tests for ``useAggregateRelationEvidence``. The
 * component-side tests in ``RelationEvidenceDrawer.test.tsx``
 * exercise the rendered output for every state; this module pins
 * the underlying state-machine behaviour.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAggregateRelationEvidence } from "./use-aggregate-relation-evidence";
import type { AggregatedRelationEvidence } from "../api/types";

const POPULATED: AggregatedRelationEvidence = {
  source_document_id: "doc-a",
  target_document_id: "doc-b",
  aggregate_score: 0.74,
  pair_count: 3,
  is_bridge: false,
  is_outlier: false,
  top_contributing_pairs: [
    {
      relation_id: "shared_chunk_pair:c-1->c-2",
      kind: "shared_chunk_pair",
      source_chunk_id: "c-1",
      target_chunk_id: "c-2",
      score: 0.81,
      strength_class: "strong",
      reason: "Shared keywords overlap.",
      shared_keywords: ["audit", "policy"],
    },
  ],
};

function makeResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useAggregateRelationEvidence", () => {
  it("starts idle when pair is null and never fetches", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useAggregateRelationEvidence(null));
    expect(result.current.state).toBe("idle");
    await new Promise((r) => setTimeout(r, 10));
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("transitions loading → data on a populated response", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => Promise.resolve(makeResponse(POPULATED)));

    const { result } = renderHook(() =>
      useAggregateRelationEvidence({
        sourceDocumentId: "doc-a",
        targetDocumentId: "doc-b",
      }),
    );

    await waitFor(() => expect(result.current.state).toBe("data"));
    expect(result.current.evidence?.aggregate_score).toBe(0.74);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const url = String(fetchSpy.mock.calls[0][0]);
    expect(url).toContain("source_document_id=doc-a");
    expect(url).toContain("target_document_id=doc-b");
    expect(url).toContain("top_n=5");
  });

  it("propagates a custom topN as the top_n query param", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => Promise.resolve(makeResponse(POPULATED)));

    renderHook(() =>
      useAggregateRelationEvidence(
        { sourceDocumentId: "doc-a", targetDocumentId: "doc-b" },
        { topN: 12 },
      ),
    );

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    expect(String(fetchSpy.mock.calls[0][0])).toContain("top_n=12");
  });

  it("maps backend 404 (no boundary edge) to the empty state", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            error: {
              code: "KW_NOT_FOUND",
              message: "No boundary edges found.",
              status: 404,
              retryable: false,
            },
          }),
          { status: 404, headers: { "content-type": "application/json" } },
        ),
      ),
    );

    const { result } = renderHook(() =>
      useAggregateRelationEvidence({
        sourceDocumentId: "doc-a",
        targetDocumentId: "doc-z",
      }),
    );

    await waitFor(() => expect(result.current.state).toBe("empty"));
    expect(result.current.evidence).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("maps non-404 failures to the error state with the message preserved", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() =>
      useAggregateRelationEvidence({
        sourceDocumentId: "doc-a",
        targetDocumentId: "doc-b",
      }),
    );
    await waitFor(() => expect(result.current.state).toBe("error"));
    expect(result.current.error).toBe("boom");
  });

  it("re-fetches when the pair changes", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => Promise.resolve(makeResponse(POPULATED)));

    const { rerender, result } = renderHook(
      ({ pair }) => useAggregateRelationEvidence(pair),
      {
        initialProps: {
          pair: { sourceDocumentId: "doc-a", targetDocumentId: "doc-b" },
        },
      },
    );
    await waitFor(() => expect(result.current.state).toBe("data"));
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    rerender({
      pair: { sourceDocumentId: "doc-a", targetDocumentId: "doc-c" },
    });

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
    expect(String(fetchSpy.mock.calls[1][0])).toContain("target_document_id=doc-c");
  });

  it("clearing the pair returns to idle and aborts in-flight calls", async () => {
    const resolvers: Array<(response: Response) => void> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (_url, init) =>
        new Promise<Response>((resolve, reject) => {
          resolvers.push(resolve);
          // The hook aborts the controller on cleanup; surface that as
          // the standard AbortError so the catch branch matches the
          // production fetch contract.
          const signal = (init as RequestInit | undefined)?.signal;
          signal?.addEventListener("abort", () => {
            reject(
              Object.assign(new Error("aborted"), { name: "AbortError" }),
            );
          });
        }),
    );

    const { rerender, result } = renderHook(
      ({ pair }: { pair: { sourceDocumentId: string; targetDocumentId: string } | null }) =>
        useAggregateRelationEvidence(pair),
      {
        initialProps: {
          pair: { sourceDocumentId: "doc-a", targetDocumentId: "doc-b" } as
            | { sourceDocumentId: string; targetDocumentId: string }
            | null,
        },
      },
    );
    await waitFor(() => expect(result.current.state).toBe("loading"));

    rerender({ pair: null });

    await waitFor(() => expect(result.current.state).toBe("idle"));
    // Resolving the previous (now aborted) call should NOT flip state
    // back to "data" — the hook ignores aborted resolutions.
    resolvers[0]?.(makeResponse(POPULATED));
    await new Promise((r) => setTimeout(r, 10));
    expect(result.current.state).toBe("idle");
  });
});
