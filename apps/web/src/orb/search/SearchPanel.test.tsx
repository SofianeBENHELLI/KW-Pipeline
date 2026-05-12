/**
 * SearchPanel tests — pin debounced fetch, results rendering,
 * Phase-3 disabled banner, error fallback, empty state, click-through.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { SearchPanel } from "./SearchPanel";

function makeJsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function renderPanel() {
  return render(
    <MemoryRouter initialEntries={["/kf/search"]}>
      <Routes>
        <Route path="/kf/search" element={<SearchPanel />} />
        <Route path="/kf/review/:docId" element={<div data-testid="review-page" />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("<SearchPanel />", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders idle copy with no fetch firing", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderPanel();
    expect(screen.getByText(/Type a query above/i)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("debounces typing and fires one request per burst", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        makeJsonResponse({
          schema_version: "v0.1",
          embedding_model: "voyage-3",
          query: "x",
          query_embedding_dim: 1024,
          results: [
            {
              chunk_id: "c1",
              document_id: "doc-1",
              version_id: "v1",
              section_id: "s1",
              snippet: "hello world",
              score: 0.91,
            },
          ],
        }),
      ),
    );
    renderPanel();
    fireEvent.change(screen.getByLabelText("Search query"), {
      target: { value: "h" },
    });
    fireEvent.change(screen.getByLabelText("Search query"), {
      target: { value: "he" },
    });
    fireEvent.change(screen.getByLabelText("Search query"), {
      target: { value: "hello" },
    });
    expect(fetchSpy).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(310);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("renders results + click-through to /kf/review/:docId", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        makeJsonResponse({
          schema_version: "v0.1",
          embedding_model: "voyage-3",
          query: "x",
          query_embedding_dim: 1024,
          results: [
            {
              chunk_id: "c1",
              document_id: "doc-1",
              version_id: "v1",
              section_id: "s1",
              snippet: "Net new ARR closed at $8.4M",
              score: 0.94,
            },
          ],
        }),
      ),
    );
    renderPanel();
    fireEvent.change(screen.getByLabelText("Search query"), {
      target: { value: "ARR" },
    });
    const list = await screen.findByTestId("kf-search-results", undefined, { timeout: 1000 });
    expect(list).toHaveTextContent(/Net new ARR/);
    expect(list).toHaveTextContent(/0\.940/);
    fireEvent.click(screen.getByRole("button", { name: /Open document doc-1/ }));
    expect(screen.getByTestId("review-page")).toBeInTheDocument();
  });

  it("renders the Phase-3 disabled banner with remediation copy", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          detail: "Vector search disabled.",
          error: {
            code: "KW_VECTOR_SEARCH_DISABLED",
            message: "Vector search disabled.",
            status: 503,
            retryable: false,
            remediation:
              "Set KW_KNOWLEDGE_LAYER_ENABLED=true and configure VOYAGE_API_KEY.",
          },
        },
        503,
      ),
    );
    renderPanel();
    fireEvent.change(screen.getByLabelText("Search query"), {
      target: { value: "test" },
    });
    const banner = await screen.findByTestId(
      "kf-search-disabled",
      undefined,
      { timeout: 1000 },
    );
    expect(banner).toHaveTextContent(/Vector search disabled/i);
    expect(banner).toHaveTextContent(/VOYAGE_API_KEY/);
  });

  it("renders the empty state when no results", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        schema_version: "v0.1",
        embedding_model: "voyage-3",
        query: "x",
        query_embedding_dim: 1024,
        results: [],
      }),
    );
    renderPanel();
    fireEvent.change(screen.getByLabelText("Search query"), {
      target: { value: "nope" },
    });
    await waitFor(() =>
      expect(screen.getByText(/No matches/i)).toBeInTheDocument(),
    );
  });

  it("renders the generic error banner on non-503 failures", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    renderPanel();
    fireEvent.change(screen.getByLabelText("Search query"), {
      target: { value: "x" },
    });
    const err = await screen.findByTestId(
      "kf-search-error",
      undefined,
      { timeout: 1000 },
    );
    expect(err).toHaveTextContent(/network down/);
  });
});
