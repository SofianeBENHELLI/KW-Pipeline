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

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AdminArchiveView } from "../AdminArchiveView";
import type {
  ApiArchivedDocumentItem,
  ApiArchivedDocumentsResponse,
  ApiPurgeArtifactsResponse,
  ApiPurgeBatchResponse,
  ApiRelinkScopeResponse,
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

function makeRelinkResponse(
  documentId: string,
  scope_kind: "personal" | "swym_community" | "project",
  scope_ref: string,
  overrides: Partial<ApiRelinkScopeResponse> = {},
): ApiRelinkScopeResponse {
  return {
    document_id: documentId,
    scope_kind,
    scope_ref,
    removed_at_before: "2026-05-03T18:00:00Z",
    relinked_at: null,
    dry_run: true,
    ...overrides,
  };
}

function makeBatchResultSuccess(
  documentId: string,
  bytes = 1024,
): ApiPurgeBatchResponse["results"][number] {
  return {
    document_id: documentId,
    success: true,
    error_code: null,
    error_message: null,
    purge_response: {
      document_id: documentId,
      dry_run: true,
      versions_purged: [
        {
          version_id: `${documentId}-v1`,
          status_before: "VALIDATED",
          storage_uri_before: `memory://docs/${documentId}-v1/file.txt`,
          tombstone_uri: `tombstone:purged:${documentId}:${documentId}-v1:2026-05-04T12:00:00+00:00`,
          purged_at: null,
          bytes_estimate: bytes,
        },
      ],
    },
  };
}

function makeBatchResultFailure(
  documentId: string,
): ApiPurgeBatchResponse["results"][number] {
  return {
    document_id: documentId,
    success: false,
    error_code: "document_not_archived",
    error_message: "Document is already active.",
    purge_response: null,
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

  // ─── Relink scope (D.9 follow-up: ADR-027 §1.2 / #269) ────────────────────

  it("Relink scope… opens modal pre-filled from last_active_scope_*", async () => {
    const item = makeItem({
      document_id: "doc-relink-1",
      last_active_scope_kind: "swym_community",
      last_active_scope_ref: "community-42",
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeListResponse([item])),
    );

    render(<AdminArchiveView />);

    await waitFor(() => {
      expect(screen.getByText("Relink scope…")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Relink scope…"));

    await waitFor(() => {
      expect(screen.getByText("Relink scope")).toBeInTheDocument();
    });
    const kind = screen.getByTestId("relink-scope-kind") as HTMLSelectElement;
    const ref = screen.getByTestId("relink-scope-ref") as HTMLInputElement;
    expect(kind.value).toBe("swym_community");
    expect(ref.value).toBe("community-42");
  });

  it("Relink scope preview → confirm POSTs ?confirm=true (no dry_run) and refreshes the list", async () => {
    let listCallCount = 0;
    let dryRunCalled = false;
    let realCalled = false;
    let realUrl = "";
    let realBody: { scope_kind?: string; scope_ref?: string } | null = null;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (url.includes("/admin/archive/archived_documents")) {
          listCallCount += 1;
          if (listCallCount === 1) {
            return makeJsonResponse(
              makeListResponse([
                makeItem({
                  document_id: "doc-relink-1",
                  last_active_scope_kind: "personal",
                  last_active_scope_ref: "alice",
                }),
              ]),
            );
          }
          return makeJsonResponse(makeListResponse([]));
        }
        if (url.includes("/admin/archive/relink_scope") && method === "POST") {
          if (url.includes("dry_run=true")) {
            dryRunCalled = true;
            return makeJsonResponse(
              makeRelinkResponse("doc-relink-1", "personal", "alice", {
                dry_run: true,
              }),
            );
          }
          realCalled = true;
          realUrl = url;
          // Capture the body via the Request object passed by openapi-fetch.
          if (input instanceof Request) {
            realBody = await input.clone().json();
          }
          return makeJsonResponse(
            makeRelinkResponse("doc-relink-1", "personal", "alice", {
              dry_run: false,
              relinked_at: "2026-05-04T12:30:00Z",
            }),
          );
        }
        return makeJsonResponse({ detail: "unexpected" }, 500);
      },
    );

    render(<AdminArchiveView />);

    await waitFor(() => {
      expect(screen.getByText("Relink scope…")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Relink scope…"));

    // Click Preview → dry-run fires.
    await waitFor(() => {
      expect(screen.getByText("Preview")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Preview"));

    await waitFor(() => {
      expect(dryRunCalled).toBe(true);
    });
    // Preview shows the impact (removed_at_before).
    await waitFor(() => {
      expect(screen.getByTestId("relink-removed-at").textContent).toBe(
        "2026-05-03T18:00:00Z",
      );
    });

    // CTA appears once preview resolves.
    await waitFor(() => {
      expect(screen.getByText("Reactivate scope link")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Reactivate scope link"));

    await waitFor(() => {
      expect(realCalled).toBe(true);
    });
    expect(realUrl).toContain("confirm=true");
    expect(realUrl).not.toContain("dry_run=true");
    expect(realBody).toEqual({
      document_id: "doc-relink-1",
      scope_kind: "personal",
      scope_ref: "alice",
    });

    // List refreshed (toast + reload).
    await waitFor(() => {
      expect(screen.getByText("Scope link reactivated.")).toBeInTheDocument();
    });
    expect(listCallCount).toBeGreaterThanOrEqual(2);
  });

  it("Relink scope renders inline error envelope on 404", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (url.includes("/admin/archive/archived_documents")) {
          return makeJsonResponse(
            makeListResponse([
              makeItem({ last_active_scope_kind: "personal", last_active_scope_ref: "alice" }),
            ]),
          );
        }
        if (url.includes("/admin/archive/relink_scope") && method === "POST") {
          return makeJsonResponse(
            {
              detail: "Scope link not found.",
              error: {
                code: "KW_NOT_FOUND",
                message: "Scope link not found.",
                status: 404,
                retryable: false,
              },
            },
            404,
          );
        }
        return makeJsonResponse({ detail: "unexpected" }, 500);
      },
    );

    render(<AdminArchiveView />);
    await waitFor(() => {
      expect(screen.getByText("Relink scope…")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Relink scope…"));
    await waitFor(() => {
      expect(screen.getByText("Preview")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Preview"));

    await waitFor(() => {
      expect(screen.getByText("Preview failed")).toBeInTheDocument();
    });
    expect(screen.getByText(/Scope link not found/)).toBeInTheDocument();
  });

  // ─── Bulk multi-select purge (D.9 follow-up: ADR-027 §4 / #273) ─────────────

  it("selecting 2 rows enables 'Purge selected (N)…' with the live count", async () => {
    const items = [
      makeItem({ document_id: "doc-1", original_filename: "a.txt" }),
      makeItem({ document_id: "doc-2", original_filename: "b.txt" }),
      makeItem({ document_id: "doc-3", original_filename: "c.txt" }),
    ];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeListResponse(items)),
    );

    render(<AdminArchiveView />);
    await waitFor(() => {
      expect(screen.getByText("a.txt")).toBeInTheDocument();
    });

    // No bulk bar yet.
    expect(
      screen.queryByTestId("admin-archive-bulk-bar"),
    ).not.toBeInTheDocument();

    // Select 2 rows.
    const rowSelects = screen.getAllByTestId("admin-archive-row-select");
    expect(rowSelects).toHaveLength(3);
    fireEvent.click(rowSelects[0]);
    fireEvent.click(rowSelects[1]);

    await waitFor(() => {
      expect(
        screen.getByTestId("admin-archive-bulk-bar"),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("Purge selected (2)…")).toBeInTheDocument();
  });

  it("header checkbox selects all visible rows", async () => {
    const items = [
      makeItem({ document_id: "doc-1", original_filename: "a.txt" }),
      makeItem({ document_id: "doc-2", original_filename: "b.txt" }),
    ];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeListResponse(items)),
    );

    render(<AdminArchiveView />);
    await waitFor(() => {
      expect(screen.getByText("a.txt")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-archive-select-all"));
    await waitFor(() => {
      expect(screen.getByText("Purge selected (2)…")).toBeInTheDocument();
    });
    // All row checkboxes are now checked.
    for (const cb of screen.getAllByTestId(
      "admin-archive-row-select",
    ) as HTMLInputElement[]) {
      expect(cb.checked).toBe(true);
    }
  });

  it("bulk preview shows per-doc result, confirm POSTs to purge_batch with ?confirm=true", async () => {
    let listCallCount = 0;
    let dryRunCalled = false;
    let realCalled = false;
    let realUrl = "";
    let realBody: { document_ids?: string[] } | null = null;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (url.includes("/admin/archive/archived_documents")) {
          listCallCount += 1;
          if (listCallCount === 1) {
            return makeJsonResponse(
              makeListResponse([
                makeItem({ document_id: "doc-1", original_filename: "a.txt" }),
                makeItem({ document_id: "doc-2", original_filename: "b.txt" }),
              ]),
            );
          }
          return makeJsonResponse(makeListResponse([]));
        }
        if (url.includes("/admin/archive/purge_batch") && method === "POST") {
          if (url.includes("dry_run=true")) {
            dryRunCalled = true;
            return makeJsonResponse({
              dry_run: true,
              results: [
                makeBatchResultSuccess("doc-1", 2048),
                makeBatchResultSuccess("doc-2", 1024),
              ],
            } satisfies ApiPurgeBatchResponse);
          }
          realCalled = true;
          realUrl = url;
          if (input instanceof Request) {
            realBody = await input.clone().json();
          }
          return makeJsonResponse({
            dry_run: false,
            results: [
              makeBatchResultSuccess("doc-1", 2048),
              makeBatchResultSuccess("doc-2", 1024),
            ],
          } satisfies ApiPurgeBatchResponse);
        }
        return makeJsonResponse({ detail: "unexpected" }, 500);
      },
    );

    render(<AdminArchiveView />);
    await waitFor(() => {
      expect(screen.getByText("a.txt")).toBeInTheDocument();
    });
    // Select all + open bulk modal.
    fireEvent.click(screen.getByTestId("admin-archive-select-all"));
    fireEvent.click(screen.getByText("Purge selected (2)…"));

    // Modal opened, click Preview impact.
    await waitFor(() => {
      expect(screen.getByText("Preview impact")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Preview impact"));

    await waitFor(() => {
      expect(dryRunCalled).toBe(true);
    });
    // Per-doc summary shows 2 purgeable, 0 failed.
    await waitFor(() => {
      expect(screen.getByTestId("bulk-purge-purgeable").textContent).toBe("2");
    });
    expect(screen.getByTestId("bulk-purge-failed").textContent).toBe("0");
    expect(screen.getByTestId("bulk-purge-bytes").textContent).toBe("3072");

    // Confirm.
    fireEvent.click(screen.getByText("Permanently delete 2 documents"));

    await waitFor(() => {
      expect(realCalled).toBe(true);
    });
    expect(realUrl).toContain("confirm=true");
    expect(realUrl).not.toContain("dry_run=true");
    expect(realBody).toEqual({ document_ids: ["doc-1", "doc-2"] });

    // List refreshed + toast.
    await waitFor(() => {
      expect(screen.getByText("Purged 2 docs.")).toBeInTheDocument();
    });
    expect(listCallCount).toBeGreaterThanOrEqual(2);
  });

  it("CTA disables with tooltip when > 100 docs are selected", async () => {
    // Build 101 distinct items so the header select-all crosses the cap.
    const items: ApiArchivedDocumentItem[] = Array.from(
      { length: 101 },
      (_unused, idx) =>
        makeItem({
          document_id: `doc-${idx}`,
          original_filename: `f-${idx}.txt`,
        }),
    );
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeListResponse(items)),
    );

    render(<AdminArchiveView />);
    await waitFor(() => {
      expect(screen.getByText("f-0.txt")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("admin-archive-select-all"));
    fireEvent.click(screen.getByText("Purge selected (101)…"));

    await waitFor(() => {
      expect(
        screen.getByText(/Permanently delete 101 documents/),
      ).toBeInTheDocument();
    });

    const cta = screen.getByText(
      /Permanently delete 101 documents/,
    ) as HTMLButtonElement;
    expect(cta.disabled).toBe(true);
    expect(cta.title).toMatch(/Max 100 per batch/);

    // Preview button is also gated.
    const previewBtn = screen.getByText("Preview impact") as HTMLButtonElement;
    expect(previewBtn.disabled).toBe(true);
  });

  it("one failure in bulk preview does not block the CTA — proceeds with the rest", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (url.includes("/admin/archive/archived_documents")) {
          return makeJsonResponse(
            makeListResponse([
              makeItem({ document_id: "doc-1", original_filename: "a.txt" }),
              makeItem({ document_id: "doc-2", original_filename: "b.txt" }),
            ]),
          );
        }
        if (
          url.includes("/admin/archive/purge_batch") &&
          method === "POST" &&
          url.includes("dry_run=true")
        ) {
          return makeJsonResponse({
            dry_run: true,
            results: [
              makeBatchResultSuccess("doc-1", 2048),
              makeBatchResultFailure("doc-2"),
            ],
          } satisfies ApiPurgeBatchResponse);
        }
        return makeJsonResponse({ detail: "unexpected" }, 500);
      },
    );

    render(<AdminArchiveView />);
    await waitFor(() => {
      expect(screen.getByText("a.txt")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("admin-archive-select-all"));
    fireEvent.click(screen.getByText("Purge selected (2)…"));
    await waitFor(() => {
      expect(screen.getByText("Preview impact")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Preview impact"));

    await waitFor(() => {
      expect(screen.getByTestId("bulk-purge-purgeable").textContent).toBe("1");
    });
    expect(screen.getByTestId("bulk-purge-failed").textContent).toBe("1");
    // CTA stays enabled — the one purgeable doc still proceeds.
    const cta = screen.getByText(
      /Permanently delete 2 documents/,
    ) as HTMLButtonElement;
    expect(cta.disabled).toBe(false);

    // Per-doc list shows the failure inline.
    const tombstoneSection = screen
      .getByText(/Per-document outcome/)
      .closest("details");
    expect(tombstoneSection).not.toBeNull();
    expect(
      within(tombstoneSection as HTMLElement).getByText(
        /document_not_archived/,
      ),
    ).toBeInTheDocument();
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
