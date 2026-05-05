/**
 * Session-expired UX (#83 slice 3 / ADR-019 §5) — 3DDashboard widget.
 *
 * Same shape as ``apps/web/src/App.session-expired.test.tsx``: assert
 * that a 401 from the backend flips the shared SessionExpiredBanner
 * via the module-level ``setSessionTrigger`` registered at the
 * widget's root.
 *
 * Limitation: ``KW_AUTH_MODE=dev`` (default per #245) never returns
 * 401, so the production demo build can't reach this state without
 * the URL-hash dev stub. This test is the only verification surface
 * until bearer mode is wired.
 */

import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionGuardProvider } from "../../_shared/auth";

import App from "./App";

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
 * jsdom's ``window.location`` is a non-configurable accessor, so
 * ``Object.defineProperty(window.location, "reload", …)`` throws. Swap
 * the whole ``location`` via ``window.location = …`` instead — that
 * property IS configurable in jsdom 25.
 */
function stubReload(): { reload: ReturnType<typeof vi.fn>; restore: () => void } {
  const reload = vi.fn();
  const original = window.location;
  const stub: Location = {
    ...original,
    reload,
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

function renderWidget(): void {
  render(
    <SessionGuardProvider>
      <App />
    </SessionGuardProvider>,
  );
}

const HEALTH_BODY = { status: "ok", version: "1.0.0" };
const EMPTY_LIST = { items: [], next_cursor: null };

describe("widget App — session-expired banner (#83 slice 3)", () => {
  beforeEach(() => {
    if (typeof window !== "undefined") window.location.hash = "";
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not render the banner on healthy 200 responses", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/health")) {
          return Promise.resolve(makeJsonResponse(HEALTH_BODY));
        }
        if (url.includes("/documents")) {
          return Promise.resolve(makeJsonResponse(EMPTY_LIST));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );
    renderWidget();
    await waitFor(() => {
      // Wait for the docs list to settle (default mode is ``docs``).
      expect(
        screen.queryByTestId("session-expired-banner"),
      ).toBeNull();
    });
  });

  it("flips the banner on when /documents returns 401", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/health")) {
          return Promise.resolve(makeJsonResponse(HEALTH_BODY));
        }
        if (url.includes("/documents")) {
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
    renderWidget();
    await waitFor(() => {
      expect(screen.getByTestId("session-expired-banner")).toBeInTheDocument();
    });
  });

  it("clicking 'Sign in again' calls window.location.reload()", async () => {
    const { reload, restore } = stubReload();
    try {
      vi.spyOn(globalThis, "fetch").mockImplementation(
        (input: RequestInfo | URL): Promise<Response> => {
          const url = urlOf(input);
          if (url.includes("/health")) {
            return Promise.resolve(makeJsonResponse(HEALTH_BODY));
          }
          if (url.includes("/documents")) {
            return Promise.resolve(makeJsonResponse({}, 401));
          }
          return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
        },
      );
      renderWidget();
      const action = await screen.findByTestId("session-expired-banner-action");
      fireEvent.click(action);
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      restore();
    }
  });

  it("renders the banner immediately when the URL hash is #force-session-expired", async () => {
    window.location.hash = "#force-session-expired";
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/health")) {
          return Promise.resolve(makeJsonResponse(HEALTH_BODY));
        }
        if (url.includes("/documents")) {
          return Promise.resolve(makeJsonResponse(EMPTY_LIST));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );
    renderWidget();
    await waitFor(() => {
      expect(screen.getByTestId("session-expired-banner")).toBeInTheDocument();
    });
  });
});
