/**
 * useDocuments + viewToStatuses tests.
 *
 * The hook wraps the existing fetch-based listDocuments client. Tests
 * stub `globalThis.fetch` in the same shape as the rest of the suite.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ListDocumentsResponse } from "../../api/types";
import { useDocuments, viewToStatuses } from "./useDocuments";

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const EMPTY_PAGE: ListDocumentsResponse = { items: [], next_cursor: null };

const ONE_DOC: ListDocumentsResponse = {
  items: [
    {
      origin: "operator",
      id: "doc-1",
      original_filename: "x.txt",
      latest_version_id: "ver-1",
      created_at: "2026-05-11T14:22:08Z",
      archived_at: null,
      versions: [
        {
          id: "ver-1",
          document_id: "doc-1",
          version_number: 1,
          filename: "x.txt",
          content_type: "text/plain",
          file_size: 100,
          sha256: "h",
          storage_uri: "file://x",
          status: "STORED",
          duplicate_of_version_id: null,
          failure_reason: null,
          reviewer_note: null,
          reviewed_at: null,
          created_at: "2026-05-11T14:22:08Z",
        },
      ],
      scopes: [],
    },
  ],
  next_cursor: null,
};

describe("viewToStatuses", () => {
  it("maps each saved view to the right status array", () => {
    expect(viewToStatuses("recent")).toEqual([]);
    expect(viewToStatuses("review")).toEqual(["NEEDS_REVIEW"]);
    expect(viewToStatuses("validated")).toEqual(["VALIDATED"]);
    expect(viewToStatuses("failed")).toEqual(["FAILED"]);
  });
});

describe("useDocuments", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/documents")) {
          return Promise.resolve(makeJsonResponse(ONE_DOC));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the page on mount and resolves to status 'ok'", async () => {
    const { result } = renderHook(() => useDocuments({ view: "recent" }));
    expect(result.current.status).toBe("loading");
    await waitFor(() => expect(result.current.status).toBe("ok"));
    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0].id).toBe("doc-1");
  });

  it("passes the view's status filter through to the URL", async () => {
    let capturedUrl = "";
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        capturedUrl = urlOf(input);
        return Promise.resolve(makeJsonResponse(ONE_DOC));
      },
    );
    const { result } = renderHook(() => useDocuments({ view: "review" }));
    await waitFor(() => expect(result.current.status).toBe("ok"));
    expect(capturedUrl).toMatch(/status=NEEDS_REVIEW/);
  });

  it("propagates fetch failures as status='error'", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network error"));
    const { result } = renderHook(() => useDocuments({ view: "recent" }));
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error?.message).toBe("Network error");
  });

  it("handles the empty list gracefully", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(EMPTY_PAGE),
    );
    const { result } = renderHook(() => useDocuments({ view: "recent" }));
    await waitFor(() => expect(result.current.status).toBe("ok"));
    expect(result.current.items).toEqual([]);
  });
});
