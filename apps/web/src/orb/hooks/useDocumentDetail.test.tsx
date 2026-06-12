/**
 * useDocumentDetail tests — happy path, 404 → "not-found", network
 * error → "error", null id stays "idle".
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiDocument } from "../../api/types";
import { useDocumentDetail } from "./useDocumentDetail";

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

const FIXTURE_DOC: ApiDocument = {
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
      status: "VALIDATED",
      duplicate_of_version_id: null,
      failure_reason: null,
      reviewer_note: null,
      reviewed_at: null,
      created_at: "2026-05-11T14:22:08Z",
    },
  ],
  scopes: [],
};

describe("useDocumentDetail", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns 'idle' for a null id and never fetches", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useDocumentDetail(null));
    expect(result.current.status).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  describe("with a real id", () => {
    beforeEach(() => {
      vi.spyOn(globalThis, "fetch").mockImplementation(
        (input: RequestInfo | URL): Promise<Response> => {
          const url = urlOf(input);
          if (url.match(/\/documents\/doc-1$/)) {
            return Promise.resolve(makeJsonResponse(FIXTURE_DOC));
          }
          if (url.match(/\/documents\/missing$/)) {
            return Promise.resolve(
              makeJsonResponse({ detail: "Not found" }, 404),
            );
          }
          return Promise.resolve(
            makeJsonResponse({ detail: "Not found" }, 404),
          );
        },
      );
    });

    it("fetches the document and resolves to 'ok'", async () => {
      const { result } = renderHook(() => useDocumentDetail("doc-1"));
      expect(result.current.status).toBe("loading");
      await waitFor(() => expect(result.current.status).toBe("ok"));
      expect(result.current.document?.id).toBe("doc-1");
    });

    it("returns 'not-found' for a 404", async () => {
      const { result } = renderHook(() => useDocumentDetail("missing"));
      await waitFor(() => expect(result.current.status).toBe("not-found"));
      expect(result.current.document).toBeNull();
    });
  });

  it("returns 'error' for a network failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network down"));
    const { result } = renderHook(() => useDocumentDetail("doc-1"));
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error?.message).toBe("Network down");
  });
});
