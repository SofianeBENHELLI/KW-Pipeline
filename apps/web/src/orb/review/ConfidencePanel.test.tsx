/**
 * ConfidencePanel — pin the four render branches against
 * ``GET /documents/{id}/confidence`` envelope shapes.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfidencePanel } from "./ConfidencePanel";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function readyResponse(overrides: Record<string, unknown> = {}): unknown {
  return {
    schema_version: "v0.1",
    document_id: "doc-1",
    version_id: "ver-1",
    version_number: 2,
    has_score: true,
    confidence_score: {
      overall: 0.92,
      signals: {
        ocr_override_active: 0.0,
        orphan_ratio: 0.95,
        section_length_z: 0.88,
        topic_incoherence: 0.91,
        citation_coverage: 0.97,
      },
      weights: {
        ocr_override_active: 0.1,
        orphan_ratio: 0.25,
        section_length_z: 0.2,
        topic_incoherence: 0.2,
        citation_coverage: 0.25,
      },
      ocr_override_active: false,
      computed_at: "2026-05-15T00:00:00Z",
      computed_by_version: "v0.1",
    },
    routing_decision: "auto",
    validation_method: "auto",
    validation_actor: "hitl_auto_promoter@v0.1",
    auto_validate_threshold: 0.85,
    ...overrides,
  };
}

describe("<ConfidencePanel />", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the empty state when no document is picked", () => {
    render(<ConfidencePanel documentId={null} />);
    expect(
      screen.getByText(/pick a document from the rail/i),
    ).toBeInTheDocument();
  });

  it("renders the overall score, threshold, routing chips, and signal bars", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(readyResponse()),
    );
    render(<ConfidencePanel documentId="doc-1" />);
    await screen.findByTestId("kf-confidence");
    expect(screen.getByTestId("kf-confidence-overall")).toHaveTextContent("92%");
    expect(
      screen.getByText(/threshold 85% · above auto-validate cut-off/i),
    ).toBeInTheDocument();
    expect(screen.getByTestId("kf-confidence-routing-chip")).toHaveTextContent(
      /routed · auto/,
    );
    expect(screen.getByTestId("kf-confidence-method-chip")).toHaveTextContent(
      /validated · auto/,
    );
    expect(screen.getByTestId("kf-confidence-signals")).toBeInTheDocument();
    // Humanised signal names (snake_case → Title Case).
    expect(screen.getByText("Citation Coverage")).toBeInTheDocument();
  });

  it("renders the has_score=false empty state when the scorer is disabled", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        readyResponse({
          has_score: false,
          confidence_score: null,
          routing_decision: "human",
          validation_method: "human",
        }),
      ),
    );
    render(<ConfidencePanel documentId="doc-1" />);
    await screen.findByTestId("kf-confidence-empty");
    expect(
      screen.getByText(/predates the scorer or the scorer is disabled/i),
    ).toBeInTheDocument();
    // Routing chips still surface even without a score.
    expect(screen.getByTestId("kf-confidence-routing-chip")).toHaveTextContent(
      /routed · human/,
    );
  });

  it("surfaces the routing-pending hint when no metadata row exists", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        readyResponse({
          has_score: false,
          confidence_score: null,
          routing_decision: null,
          validation_method: null,
          validation_actor: null,
        }),
      ),
    );
    render(<ConfidencePanel documentId="doc-1" />);
    await screen.findByTestId("kf-confidence-routing");
    expect(
      screen.getByText(/no hitl routing decision recorded/i),
    ).toBeInTheDocument();
  });

  it("surfaces the OCR-override flag when active", async () => {
    const base = readyResponse() as { confidence_score: Record<string, unknown> };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        readyResponse({
          confidence_score: {
            ...base.confidence_score,
            ocr_override_active: true,
          },
        }),
      ),
    );
    render(<ConfidencePanel documentId="doc-1" />);
    await screen.findByTestId("kf-confidence-ocr-flag");
    expect(screen.getByTestId("kf-confidence-ocr-flag")).toBeInTheDocument();
  });

  it("renders the error banner when the fetch fails with an ApiError envelope", async () => {
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
    render(<ConfidencePanel documentId="doc-1" />);
    await waitFor(() =>
      expect(screen.getByTestId("kf-confidence-error")).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/failed to load confidence/i),
    ).toBeInTheDocument();
  });
});
