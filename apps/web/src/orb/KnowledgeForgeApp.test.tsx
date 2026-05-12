/**
 * KnowledgeForgeApp — route family + chrome wiring.
 *
 * Pin the brand wordmark, the / → /review redirect, the unknown-route
 * "coming soon" fallback, and the pipelineName crumb override.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { KnowledgeForgeApp } from "./KnowledgeForgeApp";

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

  it("falls back to the 'coming soon' placeholder on unknown sub-paths", () => {
    renderAt("/kf/this/does/not/exist/yet");
    expect(
      screen.getByRole("heading", { name: /coming soon/i }),
    ).toBeInTheDocument();
  });

  it("renders the per-section coming-soon placeholders for PR-stubbed routes", () => {
    // /kf/catalog renders the real CatalogView since PR 5 — assert
    // its title instead of the coming-soon stub.
    {
      const { unmount } = renderAt("/kf/catalog");
      expect(
        screen.getByRole("heading", { name: "Catalog" }),
      ).toBeInTheDocument();
      unmount();
    }
    for (const [path, expectedTitle] of [
      ["/kf/graph", /Graph/],
      ["/kf/search", /Search/],
      ["/kf/chat", /Chat/],
      ["/kf/admin", /Admin/],
      ["/kf/settings", /Settings/],
    ] as const) {
      const { unmount } = renderAt(path);
      expect(
        screen.getByRole("heading", { name: new RegExp(`${expectedTitle.source} — coming soon`, "i") }),
      ).toBeInTheDocument();
      unmount();
    }
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
