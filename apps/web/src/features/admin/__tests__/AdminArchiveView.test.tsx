/**
 * Coverage for the Admin UI Archive view (D.9).
 *
 * Pins the user-facing surface around the new
 * ``GET /admin/archive/archived_documents`` listing + the existing
 * unarchive / purge_artifacts admin actions.
 *
 * Pinned scenarios:
 * - Empty state copy when the API returns no items.
 * - Row rendering for a populated list (filename, scope-removed,
 *   versions split).
 * - Click "Unarchive" → confirm modal → confirm → POST fires with
 *   ``?confirm=true`` and the list refreshes.
 * - Click "Purge…" → dry-run preview modal renders the impact,
 *   then real purge POSTs with ``?confirm=true`` on confirm.
 * - 403 ``KW_FORBIDDEN`` from the listing renders the "Forbidden"
 *   state — we never derive admin role client-side.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AdminArchiveView } from "../AdminArchiveView";
import type {
  ApiArchivedDocumentItem,
  ApiArchivedDocumentsResponse,
  ApiPurgeArtifactsResponse,
  ApiUnarchiveResponse,
} from "../../../api/types";

// ─── Helpers ─────────────────────────────────────────────────────────────────

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

function makeItem(
  overrides: Partial<ApiArchivedDocumentItem> = {},
): ApiArchivedDocumentItem {
  return {
    document_id: "doc-1",
    original_filename: "supplier-quality-policy.txt",
    archived_at: "2026-05-04T12:00:00Z",
    last_active_scope_kind: "personal",
    last_active_scope_ref: "alice",
    versions_purged: 0,
    versions_remaining: 2,
    ...overrides,
  };
}

function makeListResponse(
  items: ApiArchivedDocumentItem[] = [],
): ApiArchivedDocumentsResponse {
  return { items, next_cursor: null };
}

function makePurgeDryRun(documentId: string): ApiPurgeArtifactsResponse {
  return {
    document_id: documentId,
    dry_run: true,
    versions_purged: [
      {
        version_id: `${documentId}-v1`,
        status_before: "VALIDATED",
        storage_uri_before: `memory://docs/${documentId}-v1/file.txt`,
        tombstone_uri: `tombstone:purged:${documentId}:${documentId}-v1:2026-05-04T12:00:00+00:00`,
        purged_at: null,
        bytes_estimate: 1024,
      },
    ],
  };
}

function makePurgeReal(documentId: string): ApiPurgeArtifactsResponse {
  return {
    document_id: documentId,
    dry_run: false,
    versions_purged: [
      {
        version_id: `${documentId}-v1`,
        status_before: "VALIDATED",
        storage_uri_before: `memory://docs/${documentId}-v1/file.txt`,
        tombstone_uri: `tombstone:purged:${documentId}:${documentId}-v1:2026-05-04T12:00:00+00:00`,
        purged_at: "2026-05-04T12:30:00Z",
        bytes_estimate: 1024,
      },
    ],
  };
}

function makeUnarchive(documentId: string): ApiUnarchiveResponse {
  return {
    document_id: documentId,
    archived_at_before: "2026-05-04T12:00:00Z",
    unarchived_at: "2026-05-04T12:30:00Z",
    dry_run: false,
  };
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe("AdminArchiveView", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders empty state copy when the API returns no items", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeListResponse([])),
    );

    render(<AdminArchiveView />);

    await waitFor(() => {
      expect(screen.getByText("No archived documents.")).toBeInTheDocument();
    });
  });

  it("renders rows from the API response with filename + scope-removed + versions split", async () => {
    const item = makeItem({
      versions_purged: 1,
      versions_remaining: 2,
      original_filename: "Policy v3.pdf",
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeListResponse([item])),
    );

    render(<AdminArchiveView />);

    await waitFor(() => {
      expect(screen.getByText("Policy v3.pdf")).toBeInTheDocument();
    });
    // Scope-removed cell shows "kind:ref".
    expect(screen.getByTestId("row-scope-removed").textContent).toBe(
      "personal:alice",
    );
    // Versions cell shows "remaining / total" where total = remaining + purged.
    expect(screen.getByTestId("row-version-counts").textContent).toBe("2 / 3");
  });

  it("renders '—' placeholder when no scope history is recoverable", async () => {
    const item = makeItem({
      last_active_scope_kind: null,
      last_active_scope_ref: null,
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeListResponse([item])),
    );

    render(<AdminArchiveView />);

    await waitFor(() => {
      expect(screen.getByTestId("row-scope-removed").textContent).toBe("—");
    });
  });

  it("Unarchive button opens confirm modal, confirm POSTs ?confirm=true and refreshes the list", async () => {
    let listCallCount = 0;
    let unarchiveCalled = false;
    let unarchiveUrl = "";
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        // openapi-fetch passes a Request object as ``input``; native
        // fetch wrappers (the upload paths) pass a string URL with
        // ``init.method``. Read the method from whichever is set.
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (url.includes("/admin/archive/archived_documents")) {
          listCallCount += 1;
          // First call returns the row; after the unarchive succeeds
          // the list reloads to confirm refresh.
          if (listCallCount === 1) {
            return makeJsonResponse(makeListResponse([makeItem()]));
          }
          return makeJsonResponse(makeListResponse([]));
        }
        if (url.includes("/admin/archive/unarchive") && method === "POST") {
          unarchiveCalled = true;
          unarchiveUrl = url;
          return makeJsonResponse(makeUnarchive("doc-1"));
        }
        return makeJsonResponse({ detail: "unexpected" }, 500);
      },
    );

    render(<AdminArchiveView />);

    // Wait for the row to render then click Unarchive.
    await waitFor(() => {
      expect(screen.getByText("Unarchive")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Unarchive"));

    // Confirm modal opens.
    await waitFor(() => {
      expect(screen.getByText("Restore document?")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Restore"));

    // POST fires with ?confirm=true.
    await waitFor(() => {
      expect(unarchiveCalled).toBe(true);
    });
    expect(unarchiveUrl).toContain("confirm=true");

    // List re-fetched + empty state shown after refresh.
    await waitFor(() => {
      expect(screen.getByText("No archived documents.")).toBeInTheDocument();
    });
    expect(listCallCount).toBeGreaterThanOrEqual(2);
  });

  it("Purge… opens dry-run preview, confirm POSTs ?confirm=true and refreshes the list", async () => {
    let listCallCount = 0;
    let dryRunCalled = false;
    let realPurgeCalled = false;
    let realPurgeUrl = "";
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        // openapi-fetch passes a Request object as ``input``; native
        // fetch wrappers (the upload paths) pass a string URL with
        // ``init.method``. Read the method from whichever is set.
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (url.includes("/admin/archive/archived_documents")) {
          listCallCount += 1;
          if (listCallCount === 1) {
            return makeJsonResponse(makeListResponse([makeItem()]));
          }
          return makeJsonResponse(makeListResponse([]));
        }
        if (url.includes("/admin/archive/purge_artifacts") && method === "POST") {
          if (url.includes("dry_run=true")) {
            dryRunCalled = true;
            return makeJsonResponse(makePurgeDryRun("doc-1"));
          }
          realPurgeCalled = true;
          realPurgeUrl = url;
          return makeJsonResponse(makePurgeReal("doc-1"));
        }
        return makeJsonResponse({ detail: "unexpected" }, 500);
      },
    );

    render(<AdminArchiveView />);

    await waitFor(() => {
      expect(screen.getByText("Purge…")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Purge…"));

    // Modal opens, dry-run runs.
    await waitFor(() => {
      expect(dryRunCalled).toBe(true);
    });
    // Preview shows version count + bytes.
    await waitFor(() => {
      expect(screen.getByTestId("purge-versions-count").textContent).toBe("1");
    });
    expect(screen.getByTestId("purge-bytes-total").textContent).toBe("1024");

    // Real purge button is destructive.
    fireEvent.click(screen.getByText("Permanently delete"));

    await waitFor(() => {
      expect(realPurgeCalled).toBe(true);
    });
    expect(realPurgeUrl).toContain("confirm=true");
    // Real purge call should NOT carry dry_run=true.
    expect(realPurgeUrl).not.toContain("dry_run=true");

    // List refreshed.
    await waitFor(() => {
      expect(screen.getByText("No archived documents.")).toBeInTheDocument();
    });
  });

  it("renders Forbidden state when the listing returns 403 KW_FORBIDDEN", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          detail: "admin role required",
          error: {
            code: "KW_FORBIDDEN",
            message: "admin role required (current: reviewer)",
            status: 403,
            retryable: false,
          },
        },
        403,
      ),
    );

    render(<AdminArchiveView />);

    await waitFor(() => {
      expect(screen.getByText("Forbidden")).toBeInTheDocument();
    });
    expect(
      screen.getByText(/admin role required/),
    ).toBeInTheDocument();
  });
});
