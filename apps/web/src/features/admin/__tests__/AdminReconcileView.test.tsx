/**
 * Coverage for the Admin reconcile-queue page (#40, ADR-006 §5).
 *
 * Pinned scenarios:
 * - "Run reconcile" click POSTs ``/admin/reconcile`` and renders the
 *   ``recovered_count`` + ``skipped_inline`` result inline.
 * - The route returning ``recovered_count: 0`` still renders a result
 *   panel (the empty pass is a meaningful operator-facing receipt).
 * - 503 envelope (``KW_HITL_DISABLED``-shape) collapses to a danger
 *   banner carrying the envelope's remediation hint.
 * - 403 envelope collapses the page to the "Forbidden" state.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AdminReconcileView } from "../AdminReconcileView";
import type { ApiReconcileResult } from "../../../api/types";

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

function makeReconcileResult(
  overrides: Partial<ApiReconcileResult> = {},
): ApiReconcileResult {
  return {
    recovered_count: 0,
    skipped_inline: false,
    ...overrides,
  };
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe("AdminReconcileView", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the page heading + run button on mount (no API call yet)", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<AdminReconcileView />);
    expect(screen.getByText("Reconcile extraction queue")).toBeInTheDocument();
    expect(screen.getByTestId("reconcile-run-button")).toBeInTheDocument();
    // The page must not fetch on mount — the operator clicks the
    // button deliberately. Polling / auto-run is explicitly out of
    // scope for this slice.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("clicking Run reconcile POSTs /admin/reconcile and renders the result counts", async () => {
    let capturedUrl = "";
    let capturedMethod = "";
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const req = input as Request;
        capturedUrl = urlOf(input);
        capturedMethod = req.method ?? "";
        return Promise.resolve(
          makeJsonResponse(
            makeReconcileResult({ recovered_count: 4, skipped_inline: false }),
          ),
        );
      },
    );

    render(<AdminReconcileView />);
    fireEvent.click(screen.getByTestId("reconcile-run-button"));

    await waitFor(() => {
      expect(screen.getByTestId("reconcile-result")).toBeInTheDocument();
    });
    expect(capturedUrl).toMatch(/\/admin\/reconcile$/);
    expect(capturedMethod).toBe("POST");
    expect(screen.getByTestId("reconcile-result-counts").textContent).toContain(
      "4",
    );
    expect(
      screen.getByTestId("reconcile-result-counts").textContent,
    ).toContain("Recovered");
  });

  it("renders the skipped-inline hint when the backend short-circuits", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        makeReconcileResult({ recovered_count: 0, skipped_inline: true }),
      ),
    );

    render(<AdminReconcileView />);
    fireEvent.click(screen.getByTestId("reconcile-run-button"));
    await waitFor(() => {
      expect(screen.getByTestId("reconcile-result")).toBeInTheDocument();
    });
    expect(screen.getByTestId("reconcile-result").textContent).toContain(
      "Inline extraction mode is on",
    );
  });

  it("dismissing the result panel clears the inline notice", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        makeReconcileResult({ recovered_count: 1, skipped_inline: false }),
      ),
    );
    render(<AdminReconcileView />);
    fireEvent.click(screen.getByTestId("reconcile-run-button"));
    await waitFor(() => {
      expect(screen.getByTestId("reconcile-result")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText("Dismiss reconcile result"));
    expect(screen.queryByTestId("reconcile-result")).toBeNull();
  });

  it("renders an error banner on a 503 KW_HITL_DISABLED-style envelope", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          error: {
            code: "KW_HITL_DISABLED",
            message: "Admin reconcile surface is disabled.",
            remediation: "Unset KW_HITL_DISABLE_SCORER to re-enable.",
          },
        },
        503,
      ),
    );

    render(<AdminReconcileView />);
    fireEvent.click(screen.getByTestId("reconcile-run-button"));
    await waitFor(() => {
      expect(screen.getByTestId("reconcile-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("reconcile-error").textContent).toContain(
      "Admin reconcile surface is disabled.",
    );
    expect(screen.getByTestId("reconcile-error").textContent).toContain(
      "Unset KW_HITL_DISABLE_SCORER",
    );
  });

  it("403 collapses the page to the Forbidden state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          error: {
            code: "KW_FORBIDDEN",
            message: "admin role required",
          },
        },
        403,
      ),
    );

    render(<AdminReconcileView />);
    fireEvent.click(screen.getByTestId("reconcile-run-button"));
    await waitFor(() => {
      expect(screen.getByText("Forbidden")).toBeInTheDocument();
    });
    expect(screen.getByText("admin role required")).toBeInTheDocument();
  });

  it("the run button reflects in-flight state via aria-busy + label", async () => {
    // Hold the fetch open via a manual resolver so we can observe the
    // intermediate "Running…" + aria-busy state.
    let resolveFetch: (r: Response) => void = () => {};
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    render(<AdminReconcileView />);
    fireEvent.click(screen.getByTestId("reconcile-run-button"));
    const button = screen.getByTestId("reconcile-run-button");
    await waitFor(() => {
      expect(button).toHaveAttribute("aria-busy", "true");
    });
    expect(button).toHaveTextContent(/Running…/);
    // Let the fetch complete so the test doesn't leave a dangling
    // promise (act() warning).
    resolveFetch(
      makeJsonResponse(
        makeReconcileResult({ recovered_count: 0, skipped_inline: false }),
      ),
    );
    await waitFor(() => {
      expect(button).not.toHaveAttribute("aria-busy", "true");
    });
  });
});
