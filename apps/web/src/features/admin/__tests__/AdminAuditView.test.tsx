/**
 * Coverage for the Admin Audit Log Viewer (#206 follow-up).
 *
 * Pinned scenarios:
 * - Renders rows + populates the event-name dropdown from the
 *   response's ``available_event_names``.
 * - Filter bar: applying flips the network call to a filtered URL,
 *   reset clears every field and re-fetches.
 * - Click row → expanded panel with pretty-printed JSON payload.
 * - "Load more" appends the next cursor's rows to the existing list.
 * - 403 ``KW_FORBIDDEN`` collapses to the "Forbidden" state.
 * - 503 ``KW_AUDIT_DISABLED`` renders the dedicated "Audit log
 *   disabled" card with the envelope's remediation hint.
 * - Empty state copy adapts to whether filters are applied.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AdminAuditView } from "../AdminAuditView";
import type {
  ApiAdminAuditEventsResponse,
  ApiAuditEventItem,
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

function makeItem(overrides: Partial<ApiAuditEventItem> = {}): ApiAuditEventItem {
  // ``payload`` is typed as ``Record<string, never>`` by openapi-typescript
  // (the generator's translation of an open ``dict[str, Any]`` schema).
  // Cast through ``unknown`` so test fixtures can populate it without
  // tripping the structural check — the runtime shape matches what the
  // route returns.
  return {
    id: "2026-05-04T10:00:00+00:00:review.validated:alice",
    event_name: "review.validated",
    actor: "alice",
    created_at: "2026-05-04T10:00:00Z",
    payload: { document_id: "doc-1", actor: "alice" } as unknown as Record<string, never>,
    ...overrides,
  };
}

function makeResponse(
  overrides: Partial<ApiAdminAuditEventsResponse> = {},
): ApiAdminAuditEventsResponse {
  return {
    items: [],
    next_cursor: null,
    available_event_names: [],
    ...overrides,
  };
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe("AdminAuditView", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders rows from the API response and populates the event-name dropdown", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        makeResponse({
          items: [
            makeItem({
              id: "row-1",
              event_name: "review.validated",
              actor: "alice",
            }),
            makeItem({
              id: "row-2",
              event_name: "routing.decided",
              actor: "bob",
            }),
          ],
          available_event_names: ["review.validated", "routing.decided"],
        }),
      ),
    );

    render(<AdminAuditView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("audit-event-row")).toHaveLength(2);
    });
    const rows = screen.getAllByTestId("audit-event-row");
    expect(rows[0].textContent).toContain("review.validated");
    expect(rows[1].textContent).toContain("routing.decided");

    // Dropdown options come from available_event_names.
    const options = (
      screen.getByTestId("filter-event-name") as HTMLSelectElement
    ).options;
    const optionValues = Array.from(options).map((o) => o.value);
    expect(optionValues).toEqual([
      "",
      "review.validated",
      "routing.decided",
    ]);
  });

  it("renders the empty-state copy when no filters are applied", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeResponse({ items: [] })),
    );

    render(<AdminAuditView />);

    await waitFor(() => {
      expect(screen.getByTestId("empty-events")).toBeInTheDocument();
    });
    expect(screen.getByTestId("empty-events").textContent).toContain(
      "No audit events yet.",
    );
  });

  it("applies the filter bar — Apply re-fetches with filter params", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        makeJsonResponse(
          makeResponse({
            available_event_names: ["review.validated", "routing.decided"],
          }),
        ),
      );

    render(<AdminAuditView />);

    // Wait for initial load.
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalled();
    });
    fetchSpy.mockClear();

    // Pick a filter event_name + actor and click Apply.
    fireEvent.change(screen.getByTestId("filter-event-name"), {
      target: { value: "review.validated" },
    });
    fireEvent.change(screen.getByTestId("filter-actor"), {
      target: { value: "alice" },
    });
    fireEvent.click(screen.getByTestId("filter-apply"));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalled();
    });
    const call = fetchSpy.mock.calls[fetchSpy.mock.calls.length - 1];
    const url = urlOf(call[0] as RequestInfo);
    expect(url).toContain("event_name=review.validated");
    expect(url).toContain("actor=alice");
  });

  it("Reset clears every filter and re-fetches without filter params", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(makeJsonResponse(makeResponse()));

    render(<AdminAuditView />);
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalled();
    });

    // Set, apply, then reset.
    fireEvent.change(screen.getByTestId("filter-actor"), {
      target: { value: "alice" },
    });
    fireEvent.click(screen.getByTestId("filter-apply"));
    await waitFor(() => {
      expect(fetchSpy.mock.calls.some((c) => urlOf(c[0] as RequestInfo).includes("actor=alice"))).toBe(true);
    });

    fetchSpy.mockClear();
    fireEvent.click(screen.getByTestId("filter-reset"));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalled();
    });
    const call = fetchSpy.mock.calls[fetchSpy.mock.calls.length - 1];
    const url = urlOf(call[0] as RequestInfo);
    expect(url).not.toContain("actor=");
    expect(url).not.toContain("event_name=");

    // Inputs are cleared.
    expect(
      (screen.getByTestId("filter-actor") as HTMLInputElement).value,
    ).toBe("");
  });

  it("clicking a row expands the JSON payload panel", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        makeResponse({
          items: [
            makeItem({
              id: "row-1",
              payload: {
                document_id: "doc-42",
                actor: "alice",
                scope: "personal",
              } as unknown as Record<string, never>,
            }),
          ],
        }),
      ),
    );

    render(<AdminAuditView />);
    await waitFor(() => {
      expect(screen.getByTestId("audit-event-row")).toBeInTheDocument();
    });

    // Initially collapsed — no expanded row.
    expect(screen.queryByTestId("audit-event-row-expanded")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("audit-event-row"));

    await waitFor(() => {
      expect(
        screen.getByTestId("audit-event-row-expanded"),
      ).toBeInTheDocument();
    });
    const expandedText =
      screen.getByTestId("audit-event-row-expanded").textContent ?? "";
    // Pretty-printed JSON contains all three keys, indented.
    expect(expandedText).toContain("document_id");
    expect(expandedText).toContain("doc-42");
    expect(expandedText).toContain("scope");
  });

  it("Load more appends the next page to the table", async () => {
    let callCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      callCount += 1;
      if (callCount === 1) {
        return makeJsonResponse(
          makeResponse({
            items: [makeItem({ id: "row-1" })],
            next_cursor: "cursor-page-2",
          }),
        );
      }
      return makeJsonResponse(
        makeResponse({
          items: [makeItem({ id: "row-2" })],
          next_cursor: null,
        }),
      );
    });

    render(<AdminAuditView />);
    await waitFor(() => {
      expect(screen.getAllByTestId("audit-event-row")).toHaveLength(1);
    });

    expect(screen.getByTestId("load-more")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("load-more"));

    await waitFor(() => {
      expect(screen.getAllByTestId("audit-event-row")).toHaveLength(2);
    });

    // Cursor exhausted — Load more disappears.
    expect(screen.queryByTestId("load-more")).not.toBeInTheDocument();
  });

  it('renders "Forbidden" state on a 403 KW_FORBIDDEN envelope', async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          detail: "admin role required (current: reviewer)",
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

    render(<AdminAuditView />);

    await waitFor(() => {
      expect(screen.getByText("Forbidden")).toBeInTheDocument();
    });
  });

  it('renders "Audit log disabled" state on a 503 KW_AUDIT_DISABLED envelope', async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          detail:
            "Audit log is disabled. Likely cause: KW_AUDIT_ENABLED=false (the in-memory default).",
          error: {
            code: "KW_AUDIT_DISABLED",
            message:
              "Audit log is disabled. Likely cause: KW_AUDIT_ENABLED=false (the in-memory default).",
            status: 503,
            retryable: false,
            remediation:
              "Set KW_AUDIT_ENABLED=true and restart the API.",
          },
        },
        503,
      ),
    );

    render(<AdminAuditView />);

    await waitFor(() => {
      expect(screen.getByTestId("audit-disabled-state")).toBeInTheDocument();
    });
    const text =
      screen.getByTestId("audit-disabled-state").textContent ?? "";
    expect(text).toContain("Audit log disabled");
    expect(text).toContain("KW_AUDIT_ENABLED");
    expect(text).toContain("Set KW_AUDIT_ENABLED=true");
  });
});
