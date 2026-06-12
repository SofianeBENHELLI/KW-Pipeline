/**
 * HighValueChunksPanel — pin the render branches against the
 * ``GET /documents/{id}/high-value-chunks`` envelope.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HighValueChunksPanel } from "./HighValueChunksPanel";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function readyResponse(itemCount: number): unknown {
  return {
    schema_version: "v0.1",
    document_id: "doc-1",
    version_id: "ver-1",
    version_number: 2,
    total_chunks: 12,
    weights: {
      claims: 0.3,
      process_steps: 0.2,
      graph_degree: 0.25,
      entity_density: 0.25,
    },
    items: Array.from({ length: itemCount }, (_, i) => ({
      chunk_id: `chunk-${i + 1}`,
      section_id: `section-${i + 1}`,
      heading: `Section ${i + 1}`,
      snippet: `Sample text for section ${i + 1}.`,
      char_count: 100,
      score: Math.max(0, 0.95 - i * 0.1),
      signals: {
        claims: 1.0 - i * 0.1,
        process_steps: 0.5,
        graph_degree: 0.5,
        entity_density: 0.4,
      },
      claim_count: 5 - i,
      process_step_count: 3,
      graph_degree: 2,
      entity_mention_count: 4,
    })),
  };
}

describe("<HighValueChunksPanel />", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the idle state when no document is picked", () => {
    render(<HighValueChunksPanel documentId={null} />);
    expect(
      screen.getByText(/pick a document from the rail/i),
    ).toBeInTheDocument();
  });

  it("renders the ranked list with score + signal chips", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(readyResponse(3)),
    );
    render(<HighValueChunksPanel documentId="doc-1" />);
    await screen.findByTestId("kf-hv-list");
    expect(screen.getByTestId("kf-hv-row-chunk-1")).toBeInTheDocument();
    expect(screen.getByTestId("kf-hv-score-chunk-1")).toHaveTextContent(
      "95%",
    );
    // Each row carries the four signal chips.
    const firstRow = screen.getByTestId("kf-hv-row-chunk-1");
    expect(
      firstRow.querySelector('[data-testid="kf-hv-signal-claims"]'),
    ).toBeInTheDocument();
    expect(
      firstRow.querySelector('[data-testid="kf-hv-signal-steps"]'),
    ).toBeInTheDocument();
    expect(
      firstRow.querySelector('[data-testid="kf-hv-signal-degree"]'),
    ).toBeInTheDocument();
    expect(
      firstRow.querySelector('[data-testid="kf-hv-signal-entities"]'),
    ).toBeInTheDocument();
  });

  it("renders the cold-start empty state when items is empty", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        ...(readyResponse(0) as Record<string, unknown>),
        total_chunks: 0,
      }),
    );
    render(<HighValueChunksPanel documentId="doc-1" />);
    await screen.findByTestId("kf-hv-empty");
    expect(
      screen.getByText(/extraction has not produced any chunks/i),
    ).toBeInTheDocument();
  });

  it("surfaces an inline error banner when the fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "boom",
            message: "boom",
            detail: "Boom downstream",
            status: 500,
            retryable: false,
          },
        }),
        {
          status: 500,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    render(<HighValueChunksPanel documentId="doc-1" />);
    await waitFor(() =>
      expect(screen.getByTestId("kf-hv-error")).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/failed to load chunks/i),
    ).toBeInTheDocument();
  });

  it("renders the count summary in the card header", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(readyResponse(2)),
    );
    render(<HighValueChunksPanel documentId="doc-1" />);
    await screen.findByTestId("kf-hv-list");
    expect(screen.getByText(/v2 · 2\/12 chunks/i)).toBeInTheDocument();
  });
});
