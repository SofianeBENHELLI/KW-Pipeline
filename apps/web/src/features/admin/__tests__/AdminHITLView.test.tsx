/**
 * Coverage for the Admin HITL dashboard (#215, EPIC-A close-out).
 *
 * Pinned scenarios:
 * - Renders the four config metric cards from the API response.
 * - Renders bucket table rows in the order returned by the API
 *   (the route already sorts by drift_ratio DESC).
 * - Click "Run pass" → POSTs ``/admin/hitl/run_auto_promote_pass``
 *   and renders the structured result envelope inline.
 * - 403 ``KW_FORBIDDEN`` collapses the page to the "Forbidden" state.
 * - 503 ``KW_HITL_DISABLED`` renders the "HITL disabled" card with
 *   the envelope's remediation hint.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AdminHITLView } from "../AdminHITLView";
import type {
  ApiAdminHITLStateResponse,
  ApiAutoPromoteResult,
  ApiBucketState,
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

function makeBucket(overrides: Partial<ApiBucketState> = {}): ApiBucketState {
  return {
    content_type: "text/plain",
    topic_cluster: "compliance",
    samples_taken: 14,
    samples_auto: 10,
    samples_human: 4,
    samples_human_after_auto: 2,
    drift_ratio: 0.2,
    effective_sample_rate: 0.5,
    last_decision_at: "2026-05-04T12:00:00Z",
    ...overrides,
  };
}

function makeStateResponse(
  overrides: Partial<ApiAdminHITLStateResponse> = {},
): ApiAdminHITLStateResponse {
  return {
    enabled: true,
    force_auto_corpus: false,
    threshold: 0.85,
    baseline_sample_rate: 0.05,
    drift_threshold: 0.1,
    drift_ramp_factor: 10,
    pending_auto_promotions: 3,
    buckets: [],
    ...overrides,
  };
}

function makeAutoPromoteResult(
  overrides: Partial<ApiAutoPromoteResult> = {},
): ApiAutoPromoteResult {
  return {
    scanned: 2,
    promoted: [],
    skipped: [],
    failed: [],
    ...overrides,
  };
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe("AdminHITLView", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the four config metric cards from the API response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        makeStateResponse({
          enabled: true,
          force_auto_corpus: false,
          threshold: 0.9,
          pending_auto_promotions: 7,
        }),
      ),
    );

    render(<AdminHITLView />);

    await waitFor(() => {
      expect(screen.getByTestId("card-status").textContent).toContain(
        "Enabled",
      );
    });
    expect(screen.getByTestId("card-force-auto").textContent).toContain("OFF");
    expect(screen.getByTestId("card-threshold").textContent).toContain("0.90");
    expect(screen.getByTestId("card-pending").textContent).toContain("7");
  });

  it("renders the bucket table in the API-provided order (drift desc)", async () => {
    // The route sorts server-side; the UI just renders. Pin that
    // contract so a future client-side re-sort doesn't slip in.
    const buckets: ApiBucketState[] = [
      makeBucket({
        topic_cluster: "highest-drift",
        drift_ratio: 0.5,
        effective_sample_rate: 0.5,
      }),
      makeBucket({
        topic_cluster: "mid-drift",
        drift_ratio: 0.3,
        effective_sample_rate: 0.5,
      }),
      makeBucket({
        topic_cluster: "no-drift",
        drift_ratio: 0.0,
        effective_sample_rate: 0.05,
      }),
    ];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeStateResponse({ buckets })),
    );

    render(<AdminHITLView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("hitl-bucket-row")).toHaveLength(3);
    });
    const rows = screen.getAllByTestId("hitl-bucket-row");
    expect(rows[0].textContent).toContain("highest-drift");
    expect(rows[1].textContent).toContain("mid-drift");
    expect(rows[2].textContent).toContain("no-drift");
  });

  it("renders the empty-buckets copy when the API returns no rows", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeStateResponse({ buckets: [] })),
    );

    render(<AdminHITLView />);

    await waitFor(() => {
      expect(screen.getByTestId("empty-buckets")).toBeInTheDocument();
    });
  });

  it('clicking "Run pass" POSTs and renders the structured result inline', async () => {
    let runPassCalled = false;
    let runPassMethod = "";
    let listCallCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (url.includes("/admin/hitl/state")) {
          listCallCount += 1;
          return makeJsonResponse(
            makeStateResponse({ pending_auto_promotions: 2 }),
          );
        }
        if (
          url.includes("/admin/hitl/run_auto_promote_pass") &&
          method === "POST"
        ) {
          runPassCalled = true;
          runPassMethod = method;
          return makeJsonResponse(
            makeAutoPromoteResult({
              scanned: 2,
              promoted: [
                {
                  document_id: "doc-1",
                  version_id: "ver-1",
                  score_overall: 0.95,
                },
              ],
            }),
          );
        }
        return makeJsonResponse({ detail: "unexpected" }, 500);
      },
    );

    render(<AdminHITLView />);

    await waitFor(() => {
      expect(screen.getByTestId("run-pass-button")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("run-pass-button"));

    await waitFor(() => {
      expect(runPassCalled).toBe(true);
    });
    expect(runPassMethod).toBe("POST");

    // Result panel renders the scanned / promoted / skipped / failed counts.
    await waitFor(() => {
      expect(screen.getByTestId("run-pass-result")).toBeInTheDocument();
    });
    const resultText = screen.getByTestId("run-pass-result").textContent ?? "";
    expect(resultText).toContain("Scanned 2");
    expect(resultText).toContain("promoted 1");

    // State refreshed after the pass — list called twice (initial + post-pass).
    expect(listCallCount).toBeGreaterThanOrEqual(2);
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

    render(<AdminHITLView />);

    await waitFor(() => {
      expect(screen.getByText("Forbidden")).toBeInTheDocument();
    });
  });

  it('renders "HITL disabled" state with remediation on a 503 KW_HITL_DISABLED envelope', async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          detail: "HITL routing is not wired. Likely cause: KW_HITL_DISABLE_SCORER=true.",
          error: {
            code: "KW_HITL_DISABLED",
            message:
              "HITL routing is not wired. Likely cause: KW_HITL_DISABLE_SCORER=true.",
            status: 503,
            retryable: false,
            remediation: "Unset KW_HITL_DISABLE_SCORER and restart the API.",
          },
        },
        503,
      ),
    );

    render(<AdminHITLView />);

    await waitFor(() => {
      expect(screen.getByTestId("hitl-disabled-state")).toBeInTheDocument();
    });
    const text = screen.getByTestId("hitl-disabled-state").textContent ?? "";
    expect(text).toContain("HITL disabled");
    expect(text).toContain("KW_HITL_DISABLE_SCORER");
    expect(text).toContain("Unset KW_HITL_DISABLE_SCORER");
  });

  it("force_auto_corpus=ON renders the warning badge", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeStateResponse({ force_auto_corpus: true })),
    );

    render(<AdminHITLView />);

    await waitFor(() => {
      expect(screen.getByTestId("card-force-auto").textContent).toContain("ON");
    });
  });
});
