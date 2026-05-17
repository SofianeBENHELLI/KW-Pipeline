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
      extract: true, "retry-extraction": false, semantic: false, "semantic-rerun": false, validate: false, reject: false, demote: false,
    });
  });
  it("FAILED → Extract + Retry-extraction enabled (recovery paths)", () => {
    expect(computeGates("FAILED")).toEqual({
      extract: true, "retry-extraction": true, semantic: false, "semantic-rerun": false, validate: false, reject: false, demote: false,
    });
  });
  it("EXTRACTED → only Semantic enabled", () => {
    expect(computeGates("EXTRACTED")).toEqual({
      extract: false, "retry-extraction": false, semantic: true, "semantic-rerun": false, validate: false, reject: false, demote: false,
    });
  });
  it("NEEDS_REVIEW → Validate + Reject + Re-run enabled", () => {
    expect(computeGates("NEEDS_REVIEW")).toEqual({
      extract: false, "retry-extraction": false, semantic: false, "semantic-rerun": true, validate: true, reject: true, demote: false,
    });
  });
  it("SEMANTIC_READY → Validate + Reject + Re-run enabled", () => {
    expect(computeGates("SEMANTIC_READY")).toEqual({
      extract: false, "retry-extraction": false, semantic: false, "semantic-rerun": true, validate: true, reject: true, demote: false,
    });
  });
  it("VALIDATED → Demote + Re-run enabled (re-open + method-switch paths)", () => {
    expect(computeGates("VALIDATED")).toEqual({
      extract: false, "retry-extraction": false, semantic: false, "semantic-rerun": true, validate: false, reject: false, demote: true,
    });
  });
  it("REJECTED → Demote + Re-run enabled (re-open + method-switch paths)", () => {
    expect(computeGates("REJECTED")).toEqual({
      extract: false, "retry-extraction": false, semantic: false, "semantic-rerun": true, validate: false, reject: false, demote: true,
    });
  });
  it("null status → nothing enabled", () => {
    expect(computeGates(null)).toEqual({
      extract: false, "retry-extraction": false, semantic: false, "semantic-rerun": false, validate: false, reject: false, demote: false,
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
      extract: false, "retry-extraction": false, semantic: true, "semantic-rerun": false, validate: false, reject: false, demote: false,
    });
  });

  it("`semantic-rerun` hits the same /semantic endpoint with the chosen method", async () => {
    let capturedUrl = "";
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        capturedUrl =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.toString()
              : input.url;
        return Promise.resolve(makeJsonResponse({ ok: true }));
      },
    );
    const { result } = renderHook(() =>
      useFsmTransition({
        documentId: "doc-1",
        versionId: "ver-1",
        currentStatus: "NEEDS_REVIEW",
        semanticMethod: "knowledge_graph",
      }),
    );
    await act(async () => {
      await result.current.run("semantic-rerun");
    });
    expect(result.current.status).toBe("ok");
    expect(capturedUrl).toMatch(/\/semantic\?method=knowledge_graph$/);
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

  it("appends `?method=semantic_intelligence` to the semantic POST when semanticMethod='semantic_intelligence'", async () => {
    let capturedUrl = "";
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        capturedUrl =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.toString()
              : input.url;
        return Promise.resolve(makeJsonResponse({ ok: true }));
      },
    );
    const { result } = renderHook(() =>
      useFsmTransition({
        documentId: "doc-1",
        versionId: "ver-1",
        currentStatus: "EXTRACTED",
        semanticMethod: "semantic_intelligence",
      }),
    );
    await act(async () => {
      await result.current.run("semantic");
    });
    expect(capturedUrl).toMatch(/\/semantic\?method=semantic_intelligence$/);
  });

  it("does NOT append a method param when semanticMethod is omitted", async () => {
    let capturedUrl = "";
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        capturedUrl =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.toString()
              : input.url;
        return Promise.resolve(makeJsonResponse({ ok: true }));
      },
    );
    const { result } = renderHook(() =>
      useFsmTransition({
        documentId: "doc-1",
        versionId: "ver-1",
        currentStatus: "EXTRACTED",
      }),
    );
    await act(async () => {
      await result.current.run("semantic");
    });
    expect(capturedUrl).toMatch(/\/semantic$/);
  });

  it("dispatches `demote` against /reset_to_review when status is VALIDATED", async () => {
    let capturedUrl = "";
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        capturedUrl =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.toString()
              : input.url;
        return Promise.resolve(makeJsonResponse({ ok: true }));
      },
    );
    const onAfter = vi.fn();
    const { result } = renderHook(() =>
      useFsmTransition({
        documentId: "doc-1",
        versionId: "ver-1",
        currentStatus: "VALIDATED",
        onAfter,
      }),
    );
    await act(async () => {
      await result.current.run("demote", "re-opening");
    });
    expect(result.current.status).toBe("ok");
    expect(capturedUrl).toMatch(/\/reset_to_review$/);
    expect(onAfter).toHaveBeenCalledWith("demote");
  });

  it("`demote` no-ops when status is in-flight (NEEDS_REVIEW)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy.mockClear();
    const { result } = renderHook(() =>
      useFsmTransition({
        documentId: "doc-1",
        versionId: "ver-1",
        currentStatus: "NEEDS_REVIEW",
      }),
    );
    await act(async () => {
      await result.current.run("demote");
    });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
  });
});
