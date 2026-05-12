/**
 * useFsmTransition + computeGates tests.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { computeGates, useFsmTransition } from "./useFsmTransition";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("computeGates", () => {
  it("STORED → only Extract enabled", () => {
    expect(computeGates("STORED")).toEqual({
      extract: true, semantic: false, validate: false, reject: false,
    });
  });
  it("FAILED → only Extract enabled (allows retry)", () => {
    expect(computeGates("FAILED")).toEqual({
      extract: true, semantic: false, validate: false, reject: false,
    });
  });
  it("EXTRACTED → only Semantic enabled", () => {
    expect(computeGates("EXTRACTED")).toEqual({
      extract: false, semantic: true, validate: false, reject: false,
    });
  });
  it("NEEDS_REVIEW → Validate + Reject enabled", () => {
    expect(computeGates("NEEDS_REVIEW")).toEqual({
      extract: false, semantic: false, validate: true, reject: true,
    });
  });
  it("SEMANTIC_READY → Validate + Reject enabled", () => {
    expect(computeGates("SEMANTIC_READY")).toEqual({
      extract: false, semantic: false, validate: true, reject: true,
    });
  });
  it("VALIDATED → nothing enabled (terminal)", () => {
    expect(computeGates("VALIDATED")).toEqual({
      extract: false, semantic: false, validate: false, reject: false,
    });
  });
  it("null status → nothing enabled", () => {
    expect(computeGates(null)).toEqual({
      extract: false, semantic: false, validate: false, reject: false,
    });
  });
});

describe("useFsmTransition", () => {
  afterEach(() => vi.restoreAllMocks());

  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ ok: true }),
    );
  });

  it("computes gates from currentStatus", () => {
    const { result } = renderHook(() =>
      useFsmTransition({
        documentId: "doc-1",
        versionId: "ver-1",
        currentStatus: "EXTRACTED",
      }),
    );
    expect(result.current.gates).toEqual({
      extract: false, semantic: true, validate: false, reject: false,
    });
  });

  it("dispatches `validate` and calls onAfter", async () => {
    const onAfter = vi.fn();
    const { result } = renderHook(() =>
      useFsmTransition({
        documentId: "doc-1",
        versionId: "ver-1",
        currentStatus: "NEEDS_REVIEW",
        onAfter,
      }),
    );
    await act(async () => {
      await result.current.run("validate", "looks good");
    });
    expect(result.current.status).toBe("ok");
    expect(onAfter).toHaveBeenCalledWith("validate");
  });

  it("no-ops when the gate forbids the action", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy.mockClear();
    const { result } = renderHook(() =>
      useFsmTransition({
        documentId: "doc-1",
        versionId: "ver-1",
        currentStatus: "VALIDATED",
      }),
    );
    await act(async () => {
      await result.current.run("validate");
    });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
  });

  it("flips to 'error' on a fetch failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() =>
      useFsmTransition({
        documentId: "doc-1",
        versionId: "ver-1",
        currentStatus: "NEEDS_REVIEW",
      }),
    );
    await act(async () => {
      await result.current.run("validate");
    });
    expect(result.current.status).toBe("error");
    expect(result.current.error?.message).toBe("boom");
  });

  it("no-ops when documentId is missing", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy.mockClear();
    const { result } = renderHook(() =>
      useFsmTransition({
        documentId: null,
        versionId: "ver-1",
        currentStatus: "NEEDS_REVIEW",
      }),
    );
    await act(async () => {
      await result.current.run("validate");
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
