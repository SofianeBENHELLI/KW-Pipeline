/**
 * Tests for the transitional ``DemoToggle`` shared component.
 *
 * The brief asks for MSW + React Testing Library; the rest of the repo
 * (apps/explorer, apps/web) does not actually depend on MSW and uses a
 * ``vi.spyOn(globalThis, "fetch")`` mock instead. We follow the
 * established convention so this suite drops cleanly into both apps'
 * vitest runs without adding a new dev dependency (the brief's
 * "do NOT install new npm packages" constraint).
 *
 * Coverage:
 *
 *   1. Initial render shows OFF when status returns ``loaded=false,
 *      in_progress=false``.
 *   2. Toggling ON calls ``POST /admin/demo/load`` and switches to
 *      polling; ``onCorpusRefreshNeeded`` fires once the
 *      ``in_progress`` flag flips back to false.
 *   3. 409 surfaces the conflict panel with ``non_demo_doc_count``;
 *      clicking Force re-issues the call with ``force=true``.
 *   4. Reset confirms via ``window.confirm`` then issues
 *      ``POST /admin/demo/reset`` and re-fires
 *      ``onCorpusRefreshNeeded``.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";

import { DemoToggle } from "./DemoToggle";
import type { DemoStatusResponse } from "./api";

const BASE_URL = "http://localhost:8000";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface FetchCall {
  url: string;
  method: string;
  body: unknown;
}

/**
 * Build a fetch mock keyed by ``"<METHOD> <pathname>"`` so each test
 * declares its responses with minimal ceremony. Returns the spy plus
 * a ``calls`` array the test can read to assert request bodies.
 */
function installFetchMock(
  routes: Record<string, () => Response | Promise<Response>>,
): { spy: MockInstance; calls: FetchCall[] } {
  const calls: FetchCall[] = [];
  const spy = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : (input as Request).url;
      const method = (init?.method ?? "GET").toUpperCase();
      const path = new URL(url).pathname;
      let body: unknown = null;
      if (init?.body && typeof init.body === "string") {
        try {
          body = JSON.parse(init.body);
        } catch {
          body = init.body;
        }
      }
      calls.push({ url, method, body });
      const handler = routes[`${method} ${path}`];
      if (!handler) {
        return jsonResponse({ detail: `unhandled ${method} ${path}` }, 500);
      }
      return handler();
    });
  return { spy, calls };
}

const STATUS_OFF: DemoStatusResponse = {
  loaded: false,
  in_progress: false,
  demo_doc_count: 0,
  non_demo_doc_count: 0,
  last_loaded_at: null,
  last_error: null,
};

const STATUS_LOADING: DemoStatusResponse = {
  loaded: false,
  in_progress: true,
  demo_doc_count: 12,
  non_demo_doc_count: 0,
  last_loaded_at: null,
  last_error: null,
};

const STATUS_LOADED: DemoStatusResponse = {
  loaded: true,
  in_progress: false,
  demo_doc_count: 47,
  non_demo_doc_count: 0,
  last_loaded_at: "2026-05-06T10:00:00Z",
  last_error: null,
};

describe("DemoToggle", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders OFF when the initial status reports loaded=false / in_progress=false", async () => {
    installFetchMock({
      "GET /admin/demo/status": () => jsonResponse(STATUS_OFF),
    });
    const onRefresh = vi.fn();

    render(<DemoToggle apiBaseUrl={BASE_URL} onCorpusRefreshNeeded={onRefresh} />);

    const checkbox = await screen.findByTestId("demo-toggle-checkbox");
    await waitFor(() =>
      expect(screen.getByTestId("demo-toggle-status")).toHaveTextContent("Off"),
    );
    expect(checkbox).not.toBeChecked();
    expect(checkbox).not.toBeDisabled();
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it("toggling ON issues POST /admin/demo/load, polls, and fires onCorpusRefreshNeeded when the load completes", async () => {
    // Status flips through OFF (mount) → LOADING (post-load + first
    // poll tick) → LOADED (second poll tick fires the refresh).
    let getCalls = 0;
    const { calls } = installFetchMock({
      "GET /admin/demo/status": () => {
        getCalls += 1;
        if (getCalls === 1) return jsonResponse(STATUS_OFF);
        if (getCalls === 2) return jsonResponse(STATUS_LOADING);
        return jsonResponse(STATUS_LOADED);
      },
      "POST /admin/demo/load": () => jsonResponse(STATUS_LOADING, 202),
    });
    const onRefresh = vi.fn();

    render(<DemoToggle apiBaseUrl={BASE_URL} onCorpusRefreshNeeded={onRefresh} />);
    const checkbox = await screen.findByTestId("demo-toggle-checkbox");
    await waitFor(() => expect(checkbox).not.toBeDisabled());

    fireEvent.click(checkbox);

    // Wait for the POST + polling to spin up and the badge to flip
    // into the "Loading…" state.
    await waitFor(() =>
      expect(screen.getByTestId("demo-toggle-status")).toHaveTextContent(/Loading/),
    );

    const loadCall = calls.find((c) => c.method === "POST" && c.url.endsWith("/admin/demo/load"));
    expect(loadCall?.body).toEqual({ force: false });

    // First poll tick (still in_progress) — corpus refresh should NOT
    // fire yet.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(onRefresh).not.toHaveBeenCalled();

    // Second poll tick — backend reports done, refresh fires once.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByTestId("demo-toggle-status")).toHaveTextContent(/Loaded/),
    );
  });

  it("surfaces the conflict panel on 409 and re-issues with force=true on the Force button", async () => {
    let loadCallCount = 0;
    const conflictBody = {
      error: {
        code: "DEMO_CONFLICT",
        message: "Catalog already contains 5 non-demo document(s).",
        status: 409,
        retryable: false,
        remediation: "Re-issue with force=true to ignore the guard.",
      },
      detail: {
        code: "DEMO_CONFLICT",
        detail: "Catalog already contains 5 non-demo document(s).",
        non_demo_doc_count: 5,
      },
    };
    const { calls } = installFetchMock({
      "GET /admin/demo/status": () => jsonResponse(STATUS_OFF),
      "POST /admin/demo/load": () => {
        loadCallCount += 1;
        if (loadCallCount === 1) return jsonResponse(conflictBody, 409);
        return jsonResponse(STATUS_LOADING, 202);
      },
    });
    const onRefresh = vi.fn();

    render(<DemoToggle apiBaseUrl={BASE_URL} onCorpusRefreshNeeded={onRefresh} />);
    const checkbox = await screen.findByTestId("demo-toggle-checkbox");
    await waitFor(() => expect(checkbox).not.toBeDisabled());

    fireEvent.click(checkbox);

    const panel = await screen.findByTestId("demo-toggle-conflict-panel");
    expect(panel).toHaveTextContent("5");
    expect(panel).toHaveTextContent(/non-demo/);

    const forceBtn = screen.getByTestId("demo-toggle-force");
    fireEvent.click(forceBtn);

    await waitFor(() => {
      const second = calls.filter(
        (c) => c.method === "POST" && c.url.endsWith("/admin/demo/load"),
      );
      expect(second).toHaveLength(2);
      expect(second[1].body).toEqual({ force: true });
    });

    // The conflict panel should disappear once the force-issue
    // succeeds; toggle stays ON and polling kicks off.
    await waitFor(() =>
      expect(screen.queryByTestId("demo-toggle-conflict-panel")).toBeNull(),
    );
  });

  it("Reset confirms, calls POST /admin/demo/reset, then fires onCorpusRefreshNeeded", async () => {
    let getCalls = 0;
    installFetchMock({
      "GET /admin/demo/status": () => {
        getCalls += 1;
        // Mount returns LOADED so the Reset button is rendered.
        return jsonResponse(getCalls === 1 ? STATUS_LOADED : STATUS_OFF);
      },
      "POST /admin/demo/reset": () => jsonResponse(STATUS_OFF),
    });
    const onRefresh = vi.fn();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<DemoToggle apiBaseUrl={BASE_URL} onCorpusRefreshNeeded={onRefresh} />);

    const resetBtn = await screen.findByTestId("demo-toggle-reset");
    expect(resetBtn).not.toBeDisabled();

    fireEvent.click(resetBtn);

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByTestId("demo-toggle-status")).toHaveTextContent(/Off/),
    );

    confirmSpy.mockRestore();
  });
});
