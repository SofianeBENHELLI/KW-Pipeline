import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SearchPanel } from "./SearchPanel";
import type { ApiChunkSearchResponse } from "../../api/types";

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

const FIXTURE_RESPONSE: ApiChunkSearchResponse = {
  schema_version: "v0.1",
  query: "ISO",
  embedding_model: "fake-embedding",
  query_embedding_dim: 16,
  results: [
    {
      chunk_id: "chunk-1",
      document_id: "doc-A",
      version_id: "ver-A",
      section_id: "sec-1",
      snippet: "ISO 9001 compliance management system",
      score: 0.92,
    },
    {
      chunk_id: "chunk-2",
      document_id: "doc-B",
      version_id: "ver-B",
      section_id: "sec-2",
      snippet: "Quality management standard",
      score: 0.71,
    },
  ],
};

describe("SearchPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders an empty input by default and does not query", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<SearchPanel />);
    expect(screen.getByTestId("search-panel")).toBeInTheDocument();
    expect(screen.getByTestId("search-panel-input")).toHaveValue("");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("queries the API after the user types and renders results", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeJsonResponse(FIXTURE_RESPONSE));

    render(<SearchPanel />);
    fireEvent.change(screen.getByTestId("search-panel-input"), {
      target: { value: "ISO" },
    });

    await waitFor(() => {
      expect(screen.getByTestId("search-panel-results")).toBeInTheDocument();
    });
    const items = screen.getAllByTestId("search-panel-result");
    expect(items).toHaveLength(2);
    expect(screen.getByText(/ISO 9001 compliance/)).toBeInTheDocument();
    // Score formatted as percent.
    expect(screen.getByText("92.0%")).toBeInTheDocument();
  });

  it("debounces the request — one fetch per typing burst", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(makeJsonResponse(FIXTURE_RESPONSE));

    render(<SearchPanel />);
    const input = screen.getByTestId("search-panel-input");
    fireEvent.change(input, { target: { value: "I" } });
    fireEvent.change(input, { target: { value: "IS" } });
    fireEvent.change(input, { target: { value: "ISO" } });

    await waitFor(() => {
      expect(screen.getByTestId("search-panel-results")).toBeInTheDocument();
    });
    // Only one fetch should have fired despite 3 keystrokes.
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [input1] = fetchSpy.mock.calls[0] as [RequestInfo | URL, ...unknown[]];
    expect(urlOf(input1)).toContain("q=ISO");
  });

  it("renders the disabled banner with remediation when API returns 503", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          error: {
            code: "KW_VECTOR_SEARCH_DISABLED",
            message: "Vector search is disabled.",
            status: 503,
            retryable: false,
            remediation: "Set both KW_KNOWLEDGE_LAYER_ENABLED=true and VOYAGE_API_KEY.",
          },
          detail: "Vector search is disabled.",
        },
        503,
      ),
    );

    render(<SearchPanel />);
    fireEvent.change(screen.getByTestId("search-panel-input"), {
      target: { value: "anything" },
    });

    const banner = await screen.findByTestId("search-panel-disabled");
    expect(banner).toHaveTextContent("Vector search is disabled");
    expect(banner).toHaveTextContent(/KW_KNOWLEDGE_LAYER_ENABLED/);
    expect(banner).toHaveTextContent(/VOYAGE_API_KEY/);
    // No result list, no generic error banner.
    expect(screen.queryByTestId("search-panel-results")).toBeNull();
    expect(screen.queryByTestId("search-panel-error")).toBeNull();
  });

  it("renders a generic error banner on non-503 failures", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ detail: "Boom" }, 500),
    );

    render(<SearchPanel />);
    fireEvent.change(screen.getByTestId("search-panel-input"), {
      target: { value: "x" },
    });

    const banner = await screen.findByTestId("search-panel-error");
    expect(banner).toBeInTheDocument();
    expect(screen.queryByTestId("search-panel-disabled")).toBeNull();
  });

  it("renders an empty-state message when the API returns no results", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        ...FIXTURE_RESPONSE,
        query: "nothingmatches",
        results: [],
      }),
    );

    render(<SearchPanel />);
    fireEvent.change(screen.getByTestId("search-panel-input"), {
      target: { value: "nothingmatches" },
    });

    const empty = await screen.findByTestId("search-panel-empty");
    expect(empty).toHaveTextContent(/No matches/);
  });

  it("invokes onSelectResult when a result is clicked", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeJsonResponse(FIXTURE_RESPONSE));
    const onSelectResult = vi.fn();

    render(<SearchPanel onSelectResult={onSelectResult} />);
    fireEvent.change(screen.getByTestId("search-panel-input"), {
      target: { value: "ISO" },
    });

    await waitFor(() => {
      expect(screen.getByTestId("search-panel-results")).toBeInTheDocument();
    });
    const buttons = screen.getAllByRole("button");
    fireEvent.click(buttons[0]);

    expect(onSelectResult).toHaveBeenCalledTimes(1);
    expect(onSelectResult).toHaveBeenCalledWith(
      expect.objectContaining({ chunk_id: "chunk-1", document_id: "doc-A" }),
    );
  });

  it("clearing the input clears results without firing another request", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(makeJsonResponse(FIXTURE_RESPONSE));

    render(<SearchPanel />);
    const input = screen.getByTestId("search-panel-input");
    fireEvent.change(input, { target: { value: "ISO" } });
    await waitFor(() => {
      expect(screen.getByTestId("search-panel-results")).toBeInTheDocument();
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    fireEvent.change(input, { target: { value: "" } });
    await waitFor(() => {
      expect(screen.queryByTestId("search-panel-results")).toBeNull();
    });
    // Empty query short-circuits — no extra fetch.
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});
