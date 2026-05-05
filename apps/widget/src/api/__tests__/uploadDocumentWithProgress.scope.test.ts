/**
 * Wire-layer tests for ``uploadDocumentWithProgress`` scope params
 * (EPIC-D #218 / #250).
 *
 * Pinned in a separate file from the UploadQueue tests so this suite
 * can keep the real ``api/client`` module loaded — the queue tests
 * stub it for assertion purposes, and a single ``vi.unmock`` would
 * leak across tests in the same file.
 *
 * Behaviour pinned:
 *   - ``scope_kind`` and ``scope_ref`` are appended as query params
 *     (matching the route shape from #250) when both are provided.
 *   - Neither is appended when both are omitted, so the backend's
 *     ``get_current_user`` default (``personal:<user_id>``) kicks in.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { uploadDocumentWithProgress } from "../client";

interface CapturedRequest {
  url?: string;
}

class FakeXhr {
  public upload = { addEventListener: vi.fn() };
  public status = 200;
  public statusText = "OK";
  public responseText = JSON.stringify({
    id: "ver-1",
    document_id: "doc-1",
    version_number: 1,
    filename: "x.txt",
    content_type: "text/plain",
    file_size: 4,
    sha256: "x".repeat(64),
    storage_uri: "file://x",
    status: "STORED",
    duplicate_of_version_id: null,
    failure_reason: null,
    reviewer_note: null,
    reviewed_at: null,
    created_at: "2026-05-01T00:00:00Z",
  });
  private listeners: Record<string, Array<() => void>> = {};
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  open(_method: string, url: string) {
    captured.url = url;
  }
  addEventListener(name: string, fn: () => void) {
    (this.listeners[name] ??= []).push(fn);
  }
  send() {
    // Fire ``load`` synchronously so the awaited promise resolves
    // before the test asserts on the captured URL.
    for (const fn of this.listeners["load"] ?? []) fn();
  }
}

let captured: CapturedRequest = {};
let realXhr: typeof XMLHttpRequest | undefined;

beforeEach(() => {
  captured = {};
  realXhr = globalThis.XMLHttpRequest;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).XMLHttpRequest = FakeXhr;
});

afterEach(() => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  if (realXhr) (globalThis as any).XMLHttpRequest = realXhr;
});

const fakeFile = () =>
  new File(["body"], "doc.txt", { type: "text/plain" });

describe("uploadDocumentWithProgress — scope params (#250)", () => {
  it("appends scope_kind + scope_ref when both are provided", async () => {
    await uploadDocumentWithProgress(fakeFile(), {
      baseUrl: "http://test.local",
      scope_kind: "swym_community",
      scope_ref: "comm-42",
    });

    expect(captured.url).toBeDefined();
    const parsed = new URL(captured.url!);
    expect(parsed.pathname).toBe("/documents/upload");
    expect(parsed.searchParams.get("scope_kind")).toBe("swym_community");
    expect(parsed.searchParams.get("scope_ref")).toBe("comm-42");
  });

  it("omits scope_kind / scope_ref when neither is provided", async () => {
    await uploadDocumentWithProgress(fakeFile(), {
      baseUrl: "http://test.local",
    });

    expect(captured.url).toBeDefined();
    const parsed = new URL(captured.url!);
    expect(parsed.pathname).toBe("/documents/upload");
    expect(parsed.searchParams.get("scope_kind")).toBeNull();
    expect(parsed.searchParams.get("scope_ref")).toBeNull();
    // The query string is empty → backend's ``get_current_user``
    // default (``personal:<current_user.id>``) takes over.
    expect(parsed.search).toBe("");
  });
});
