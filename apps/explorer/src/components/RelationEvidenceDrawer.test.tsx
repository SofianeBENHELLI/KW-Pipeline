/**
 * Component tests for ``RelationEvidenceDrawer``. Covers the four
 * surface states (loading / data / empty / error) plus the close
 * affordances (button + ESC + backdrop). The hook itself is tested
 * separately in ``use-aggregate-relation-evidence.test.ts``.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RelationEvidenceDrawer } from "./RelationEvidenceDrawer";
import type { AggregatedRelationEvidence } from "../api/types";

const POPULATED: AggregatedRelationEvidence = {
  source_document_id: "doc-a",
  target_document_id: "doc-b",
  aggregate_score: 0.74,
  pair_count: 3,
  is_bridge: true,
  is_outlier: false,
  top_contributing_pairs: [
    {
      relation_id: "shared_chunk_pair:c-1->c-2",
      kind: "shared_chunk_pair",
      source_chunk_id: "c-1",
      target_chunk_id: "c-2",
      score: 0.81,
      strength_class: "strong",
      reason: "High keyword overlap.",
      shared_keywords: ["audit", "policy", "compliance"],
    },
    {
      relation_id: "embedding_neighbour:c-3->c-4",
      kind: "embedding_neighbour",
      source_chunk_id: "c-3",
      target_chunk_id: "c-4",
      score: 0.62,
      strength_class: "medium",
      reason: "",
      shared_keywords: [],
    },
  ],
};

function makeResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const baseProps = {
  sourceDocumentId: "doc-a",
  sourceTitle: "Supplier policy",
  targetDocumentId: "doc-b",
  targetTitle: "Audit report",
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RelationEvidenceDrawer", () => {
  it("shows the loading affordance while the fetch is in flight", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise(() => {}), // never resolves
    );
    render(<RelationEvidenceDrawer {...baseProps} onClose={() => {}} />);
    expect(screen.getByTestId("kx-evidence-loading")).toHaveTextContent(/Loading/);
  });

  it("renders the populated metrics + the contributing pairs", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeResponse(POPULATED)),
    );

    render(<RelationEvidenceDrawer {...baseProps} onClose={() => {}} />);

    await waitFor(() => expect(screen.getByTestId("kx-evidence-metrics")).toBeInTheDocument());
    expect(screen.getByText(/74\.0%/)).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument(); // pair_count
    expect(screen.getByTestId("kx-evidence-bridge")).toBeInTheDocument();
    expect(screen.queryByTestId("kx-evidence-outlier")).not.toBeInTheDocument();

    const pairs = screen.getAllByTestId("kx-evidence-pair");
    expect(pairs).toHaveLength(2);
    expect(pairs[0]).toHaveTextContent(/81\.0%/);
    expect(pairs[0]).toHaveTextContent("shared_chunk_pair");
    expect(pairs[0]).toHaveTextContent("strong");
    expect(pairs[0]).toHaveTextContent("c-1");
    expect(pairs[0]).toHaveTextContent("c-2");
    expect(pairs[0]).toHaveTextContent(/High keyword overlap/);
    expect(pairs[0]).toHaveTextContent("audit");
    expect(pairs[0]).toHaveTextContent("policy");
    // Empty reason / no keywords on the second pair must not crash.
    expect(pairs[1]).toHaveTextContent("embedding_neighbour");
    expect(pairs[1]).toHaveTextContent("medium");
  });

  it("renders the empty state when the backend returns 404 (no boundary edge)", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            error: {
              code: "KW_NOT_FOUND",
              message: "No boundary edges.",
              status: 404,
              retryable: false,
            },
          }),
          { status: 404, headers: { "content-type": "application/json" } },
        ),
      ),
    );

    render(<RelationEvidenceDrawer {...baseProps} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("kx-evidence-empty")).toBeInTheDocument());
    expect(screen.getByTestId("kx-evidence-empty")).toHaveTextContent(/not directly linked/);
  });

  it("renders an error banner when the fetch throws", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network is down"));
    render(<RelationEvidenceDrawer {...baseProps} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("kx-evidence-error")).toBeInTheDocument());
    expect(screen.getByTestId("kx-evidence-error")).toHaveTextContent(/network is down/);
  });

  it("renders a fallback when the edge exists but the backend returns no contributing pairs", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        makeResponse({
          ...POPULATED,
          pair_count: 1,
          is_bridge: false,
          is_outlier: true,
          top_contributing_pairs: [],
        }),
      ),
    );

    render(<RelationEvidenceDrawer {...baseProps} onClose={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId("kx-evidence-no-pairs")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("kx-evidence-outlier")).toBeInTheDocument();
  });

  it("close button fires onClose", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise(() => {}),
    );
    const onClose = vi.fn();
    render(<RelationEvidenceDrawer {...baseProps} onClose={onClose} />);
    fireEvent.click(screen.getByTestId("kx-evidence-close"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking the backdrop fires onClose, but clicking the dialog body does not", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise(() => {}),
    );
    const onClose = vi.fn();
    render(<RelationEvidenceDrawer {...baseProps} onClose={onClose} />);
    // Dialog body click — no close.
    fireEvent.click(screen.getByTestId("kx-evidence-modal"));
    expect(onClose).not.toHaveBeenCalled();
    // Backdrop click — close.
    fireEvent.click(screen.getByTestId("kx-evidence-backdrop"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("ESC keypress fires onClose", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise(() => {}),
    );
    const onClose = vi.fn();
    render(<RelationEvidenceDrawer {...baseProps} onClose={onClose} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("renders the source / target titles in the header", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise(() => {}),
    );
    render(<RelationEvidenceDrawer {...baseProps} onClose={() => {}} />);
    expect(screen.getByText("Supplier policy")).toBeInTheDocument();
    expect(screen.getByText("Audit report")).toBeInTheDocument();
  });
});
