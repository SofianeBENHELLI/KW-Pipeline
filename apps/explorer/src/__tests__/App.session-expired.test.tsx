/**
 * Session-expired UX (#83 slice 3 / ADR-019 §5) — Knowledge Explorer.
 *
 * Same shape as the widget + web tests: a 401 from the backend
 * flips the shared ``SessionExpiredBanner`` via the
 * ``setSessionTrigger`` registered at the explorer's root.
 *
 * Limitation: ``KW_AUTH_MODE=dev`` (default per #245) never returns
 * 401, so this is the only verification surface until bearer mode
 * lands. The dev-stub URL hash (``#force-session-expired``) gives
 * reviewers a way to see the banner on a default-mode demo build.
 */

import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionGuardProvider } from "../../../_shared/auth";

import App from "../App";

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

function renderExplorer(): void {
  render(
    <SessionGuardProvider>
      <App />
    </SessionGuardProvider>,
  );
}

describe("Knowledge Explorer App — session-expired banner (#83 slice 3)", () => {
  beforeEach(() => {
    if (typeof window !== "undefined") window.location.hash = "";
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not render the banner when the catalog walk succeeds (sample fallback)", async () => {
    // Empty document list → ``useExplorerData`` falls back to the
    // sample corpus and never throws. No 401, so no banner.
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse({ items: [], next_cursor: null })),
    );
    renderExplorer();
    await waitFor(() => {
      // Sample fallback renders the corpus header — wait for it as
      // proof the App finished its initial mount.
      expect(
        document.querySelector(".kx-cluster-list"),
      ).not.toBeNull();
    });
    expect(screen.queryByTestId("session-expired-banner")).toBeNull();
  });

  it("flips the banner on when /documents returns 401", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
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
        return Promise.resolve(makeJsonResponse({ items: [], next_cursor: null }));
      },
    );
    renderExplorer();
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
          if (url.includes("/documents")) {
            return Promise.resolve(makeJsonResponse({}, 401));
          }
          return Promise.resolve(makeJsonResponse({ items: [], next_cursor: null }));
        },
      );
      renderExplorer();
      const action = await screen.findByTestId("session-expired-banner-action");
      fireEvent.click(action);
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      restore();
    }
  });

  it("renders the banner immediately when the URL hash is #force-session-expired", async () => {
    window.location.hash = "#force-session-expired";
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse({ items: [], next_cursor: null })),
    );
    renderExplorer();
    await waitFor(() => {
      expect(screen.getByTestId("session-expired-banner")).toBeInTheDocument();
    });
  });
});
