/**
 * AdminReconcileView — pin the happy path (recovered_count + skipped
 * inline copy), the 403 collapse, and the error banner.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdminReconcileView } from "../AdminReconcileView";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("<AdminReconcileView />", () => {
  afterEach(() => vi.restoreAllMocks());

  it("Run pass POSTs to /admin/reconcile and renders the recovered count", async () => {
    let postCalled = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (method === "POST") {
          postCalled = true;
          return makeJsonResponse({
            recovered_count: 3,
            skipped_inline: false,
          });
        }
        return makeJsonResponse({}, 404);
      },
    );
    render(<AdminReconcileView />);
    fireEvent.click(screen.getByTestId("admin-reconcile-run"));
    await waitFor(() => expect(postCalled).toBe(true));
    const result = await screen.findByTestId("admin-reconcile-result");
    expect(result).toHaveTextContent("Recovered 3 versions");
  });

  it("Renders the inline no-op envelope when skipped_inline is true", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ recovered_count: 0, skipped_inline: true }),
    );
    render(<AdminReconcileView />);
    fireEvent.click(screen.getByTestId("admin-reconcile-run"));
    const result = await screen.findByTestId("admin-reconcile-result");
    expect(result).toHaveTextContent(/No-op/);
    expect(result).toHaveTextContent(/Inline mode/);
  });

  it("403 envelope collapses the page to Forbidden", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          error: {
            code: "KW_FORBIDDEN",
            message: "Admin role required.",
            status: 403,
            retryable: false,
          },
          detail: "Admin role required.",
        },
        403,
      ),
    );
    render(<AdminReconcileView />);
    fireEvent.click(screen.getByTestId("admin-reconcile-run"));
    expect(await screen.findByText("Forbidden")).toBeInTheDocument();
  });
});
