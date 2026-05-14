/**
 * KnowledgeForgeApp — route family + chrome wiring.
 *
 * Pin the brand wordmark, the / → /review redirect, the unknown-route
 * "coming soon" fallback, and the pipelineName crumb override.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { KnowledgeForgeApp } from "./KnowledgeForgeApp";

/** Disambiguate icon-rail tiles vs top-bar tabs (both use button + same labels). */
function railTile(name: string): HTMLElement {
  const rail = screen.getByRole("navigation", { name: /Primary navigation/i });
  return within(rail).getByRole("button", { name });
}

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/kf/*" element={<KnowledgeForgeApp />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("<KnowledgeForgeApp />", () => {
  beforeEach(() => {
    // The rail at /kf/review fetches /documents on mount; stub with an
    // empty page so tests don't hit the network.
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ items: [], next_cursor: null }),
    );
  });

  afterEach(() => vi.restoreAllMocks());

  it("renders the Knowledge Forge brand wordmark in the top bar", () => {
    renderAt("/kf/review");
    expect(screen.getByText("Knowledge Forge")).toBeInTheDocument();
  });

  it("/kf redirects to /kf/review", async () => {
    renderAt("/kf");
    // The Review Workspace's empty-state header is the post-redirect
    // signal — we don't poke at the URL because MemoryRouter doesn't
    // expose it without a Location capture helper.
    await waitFor(() =>
      expect(screen.getByText(/Pick a document from the rail/i)).toBeInTheDocument(),
    );
  });

  it("renders /kf/review with the document picker rail", async () => {
    renderAt("/kf/review");
    await waitFor(() =>
      expect(screen.getByPlaceholderText("Filter filename…")).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("tab", { name: /Recent/ }),
    ).toBeInTheDocument();
  });

  it("redirects unknown sub-paths back to the Review Workspace", async () => {
    renderAt("/kf/this/does/not/exist/yet");
    await waitFor(() =>
      expect(
        screen.getByText(/Pick a document from the rail/i),
      ).toBeInTheDocument(),
    );
  });

  it("renders the real Knowledge Forge surfaces for every top-tab route", async () => {
    {
      const { unmount } = renderAt("/kf/catalog");
      expect(screen.getByRole("heading", { name: "Catalog" })).toBeInTheDocument();
      unmount();
    }
    {
      const { unmount } = renderAt("/kf/search");
      expect(screen.getByRole("heading", { name: "Search" })).toBeInTheDocument();
      unmount();
    }
    {
      const { unmount } = renderAt("/kf/chat");
      expect(screen.getByRole("heading", { name: "Chat" })).toBeInTheDocument();
      unmount();
    }
    {
      const { unmount } = renderAt("/kf/admin");
      expect(screen.getByRole("heading", { name: "Admin" })).toBeInTheDocument();
      unmount();
    }
  });

  it("never exposes a corpus-level Graph nav surface", async () => {
    // Knowledge Forge has no corpus-wide graph view — graph is a
    // per-document tab inside the Review Workspace. Corpus exploration
    // is the scope of the Knowledge Explorer app (`apps/explorer`).
    renderAt("/kf/review");
    await waitFor(() =>
      expect(screen.getByPlaceholderText("Filter filename…")).toBeInTheDocument(),
    );
    const nav = screen.getByRole("navigation", { name: /Workspace sections/i });
    expect(within(nav).queryByRole("button", { name: /^Graph$/ })).toBeNull();
    const rail = screen.getByRole("navigation", { name: /Primary navigation/i });
    expect(within(rail).queryByRole("button", { name: "Graph" })).toBeNull();
  });

  it("/kf/graph deep-links redirect to the Review Workspace", async () => {
    renderAt("/kf/graph");
    await waitFor(() =>
      expect(screen.getByText(/Pick a document from the rail/i)).toBeInTheDocument(),
    );
  });

  it("clicking the icon-rail Upload tile navigates to /kf/catalog", async () => {
    renderAt("/kf/review");
    await waitFor(() =>
      expect(screen.getByPlaceholderText("Filter filename…")).toBeInTheDocument(),
    );
    fireEvent.click(railTile("Upload"));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Catalog" })).toBeInTheDocument(),
    );
  });

  it("clicking the icon-rail Activity tile navigates to /kf/admin", async () => {
    renderAt("/kf/review");
    await waitFor(() =>
      expect(screen.getByPlaceholderText("Filter filename…")).toBeInTheDocument(),
    );
    fireEvent.click(railTile("Activity"));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Admin" })).toBeInTheDocument(),
    );
  });

  it("clicking the icon-rail Settings tile opens the settings modal (no nav)", async () => {
    renderAt("/kf/review");
    await waitFor(() =>
      expect(screen.getByPlaceholderText("Filter filename…")).toBeInTheDocument(),
    );
    fireEvent.click(railTile("Settings"));
    await waitFor(() =>
      expect(screen.getByTestId("kf-settings-modal")).toBeInTheDocument(),
    );
    // Workspace is still mounted underneath — settings is an overlay.
    expect(screen.getByPlaceholderText("Filter filename…")).toBeInTheDocument();
  });

  it("highlights the matching rail tile based on the current route", async () => {
    renderAt("/kf/catalog");
    const uploadTile = railTile("Upload");
    expect(uploadTile).toHaveAttribute("aria-current", "page");
    expect(uploadTile).toHaveClass("is-active");
  });

  it("includes the pipelineName override in the brand crumb when given", () => {
    render(
      <MemoryRouter initialEntries={["/kf/review"]}>
        <Routes>
          <Route
            path="/kf/*"
            element={<KnowledgeForgeApp pipelineName="kw-pipeline" />}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("kw-pipeline · alpha")).toBeInTheDocument();
  });
});
