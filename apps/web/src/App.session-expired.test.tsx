/**
 * Session-expired UX (#83 slice 3 / ADR-019 §5) — web reviewer.
 *
 * Asserts the wiring between the app's API client and the shared
 * SessionExpiredBanner / useSessionGuard:
 *
 *   * A 401 response from any endpoint flips the banner on.
 *   * Clicking "Sign in again" reloads the page (dev-mode + bearer-mode
 *     behaviour until a refresh-token flow lands).
 *   * Multiple 401s in a row collapse onto a single banner instance.
 *   * The dev stub at ``#force-session-expired`` flips the banner on
 *     mount so reviewers can see it on a default-mode demo build.
 *
 * The default backend mode is ``KW_AUTH_MODE=dev`` (per #245), which
 * never returns 401, so this entire surface is mock-driven. Once
 * bearer mode is the default, the dev stub goes away.
 */

import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { SessionGuardProvider } from "../../_shared/auth";

import App from "./App";
import type { ApiDocument, ListDocumentsResponse } from "./api/types";

// ─── Fixtures ────────────────────────────────────────────────────────────────

const FIXTURE_VERSION = {
  id: "ver-1",
  document_id: "doc-1",
  version_number: 1,
  filename: "policy.txt",
  content_type: "text/plain",
  file_size: 1,
  sha256: "deadbeef",
  storage_uri: "file://policy",
  status: "VALIDATED" as const,
  duplicate_of_version_id: null,
  failure_reason: null,
  reviewer_note: null,
  reviewed_at: null,
  created_at: "2026-05-04T00:00:00Z",
};

const FIXTURE_DOC: ApiDocument = {
  id: "doc-1",
  original_filename: "policy.txt",
  latest_version_id: "ver-1",
  created_at: "2026-05-04T00:00:00Z",
  archived_at: null,
  versions: [FIXTURE_VERSION],
  scopes: [],
};

const FIXTURE_LIST: ListDocumentsResponse = {
  items: [FIXTURE_DOC],
  next_cursor: null,
};

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

/**
 * jsdom marks ``window.location`` as a non-configurable accessor, so
 * ``Object.defineProperty(window.location, "reload", …)`` throws. The
 * documented workaround is to swap the entire ``location`` object out
 * via ``Object.defineProperty(window, "location", …)`` — that property
 * IS configurable, and a plain object satisfies the runtime read paths
 * the App actually uses (``window.location.reload`` + ``hash``).
 */
function stubReload(): { reload: ReturnType<typeof vi.fn>; restore: () => void } {
  const reload = vi.fn();
  const original = window.location;
  const stub: Location = {
    ...original,
    reload,
    // Keep the assignable hash + assign through to the real object so
    // tests that mutate ``window.location.hash`` mid-test still work.
    href: original.href,
    hash: original.hash,
  } as Location;
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: stub,
  });
  return {
    reload,
    restore: () => {
      Object.defineProperty(window, "location", {
        configurable: true,
        writable: true,
        value: original,
      });
    },
  };
}

function renderApp(): void {
  // The provider + router are wired in main.tsx; tests have to wrap
  // manually because they bypass the bootstrapper. ``MemoryRouter``
  // gives the top-level <Routes> tree (added in D.9) its required
  // routing context — every test stays on the catch-all reviewer
  // workbench at "/" because the legacy assertions target it.
  render(
    <SessionGuardProvider>
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    </SessionGuardProvider>,
  );
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe("App — session-expired banner (#83 slice 3)", () => {
  beforeEach(() => {
    // Ensure the URL hash is cleared between tests so the dev-stub
    // useEffect doesn't fire when we don't expect it to. jsdom's
    // ``window.location.hash`` setter accepts an empty string.
    if (typeof window !== "undefined") window.location.hash = "";
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not render the banner on a normal 200 response", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/documents?")) {
          return Promise.resolve(makeJsonResponse(FIXTURE_LIST));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );
    renderApp();
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /KW Pipeline/i }),
      ).toBeInTheDocument();
    });
    expect(screen.queryByTestId("session-expired-banner")).toBeNull();
  });

  it("flips the banner on when /documents returns 401", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/documents?")) {
          return Promise.resolve(
            makeJsonResponse(
              {
                error: {
                  code: "KW_UNAUTHORIZED",
                  message: "Token expired",
                  retryable: false,
                  remediation: "Sign in again.",
                },
              },
              401,
            ),
          );
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );
    renderApp();
    await waitFor(() => {
      expect(screen.getByTestId("session-expired-banner")).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: /sign in again/i }),
    ).toBeInTheDocument();
  });

  it("clicking 'Sign in again' calls window.location.reload()", async () => {
    const { reload, restore } = stubReload();
    try {
      vi.spyOn(globalThis, "fetch").mockImplementation(
        (input: RequestInfo | URL): Promise<Response> => {
          const url = urlOf(input);
          if (url.includes("/documents?")) {
            return Promise.resolve(makeJsonResponse({}, 401));
          }
          return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
        },
      );
      renderApp();
      const action = await screen.findByTestId("session-expired-banner-action");
      fireEvent.click(action);
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      restore();
    }
  });

  it("multiple 401s collapse onto a single banner instance", async () => {
    let calls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/documents?")) {
          calls += 1;
          return Promise.resolve(makeJsonResponse({}, 401));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );
    renderApp();
    await waitFor(() => {
      expect(screen.getByTestId("session-expired-banner")).toBeInTheDocument();
    });
    // The error path triggers a second list() call via the user's
    // Retry button. Click it and verify the banner stays a single
    // instance after another 401 lands.
    fireEvent.click(screen.getByRole("button", { name: /^Retry$/i }));
    await waitFor(() => {
      expect(calls).toBeGreaterThan(1);
    });
    expect(screen.getAllByTestId("session-expired-banner")).toHaveLength(1);
  });

  it("renders the banner immediately when the URL hash is #force-session-expired", async () => {
    window.location.hash = "#force-session-expired";
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/documents?")) {
          return Promise.resolve(makeJsonResponse(FIXTURE_LIST));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );
    renderApp();
    await waitFor(() => {
      expect(screen.getByTestId("session-expired-banner")).toBeInTheDocument();
    });
  });
});
