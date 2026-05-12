/**
 * GraphView + GraphInspector tests.
 *
 * Cover the toolbar, filter switching, node selection → inspector
 * open, and the inspector's Open-in-Review action.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { GraphInspector } from "./GraphInspector";
import { GraphView } from "./GraphView";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const FIXTURE = {
  nodes: [
    { id: "t1", kind: "topic",  label: "arr-walk",   properties: {} },
    { id: "e1", kind: "entity", label: "$8.4M",      properties: { document_id: "doc-1" } },
    { id: "c1", kind: "chunk",  label: "chunk-1",    properties: {} },
  ],
  edges: [
    { id: "edge1", kind: "belongs_to", source_id: "c1", target_id: "t1", properties: {} },
    { id: "edge2", kind: "has_entity", source_id: "c1", target_id: "e1", properties: {} },
  ],
  next_cursor: null,
};

function renderGraph() {
  return render(
    <MemoryRouter initialEntries={["/kf/graph"]}>
      <Routes>
        <Route path="/kf/graph" element={<GraphView />} />
        <Route path="/kf/review/:docId" element={<div data-testid="review-page" />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("<GraphView />", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse(FIXTURE)),
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders the filter toolbar with 6 options + the canvas", async () => {
    renderGraph();
    for (const label of ["All", "Topics", "Entities", "Chunks", "Relations", "Source-backed"]) {
      expect(await screen.findByRole("tab", { name: label })).toBeInTheDocument();
    }
    // Canvas SVG has the aria-label.
    expect(
      await screen.findByRole("img", { name: /Knowledge graph/i }),
    ).toBeInTheDocument();
  });

  it("clicking a filter chip narrows the rendered nodes", async () => {
    renderGraph();
    await screen.findByRole("img", { name: /Knowledge graph/i });
    fireEvent.click(screen.getByRole("tab", { name: "Topics" }));
    // After filter, only t1 is rendered.
    expect(screen.getByTestId("kf-gv-node-t1")).toBeInTheDocument();
    expect(screen.queryByTestId("kf-gv-node-c1")).toBeNull();
  });

  it("clicking a node opens the inspector", async () => {
    renderGraph();
    await screen.findByRole("img", { name: /Knowledge graph/i });
    fireEvent.click(screen.getByTestId("kf-gv-node-t1"));
    const inspector = screen.getByTestId("kf-gv-inspector");
    expect(within(inspector).getByText("arr-walk")).toBeInTheDocument();
    expect(within(inspector).getByText("TOPIC")).toBeInTheDocument();
  });

  it("clicking the close button collapses the inspector", async () => {
    renderGraph();
    await screen.findByRole("img", { name: /Knowledge graph/i });
    fireEvent.click(screen.getByTestId("kf-gv-node-t1"));
    fireEvent.click(screen.getByLabelText("Close inspector"));
    expect(screen.queryByTestId("kf-gv-inspector")).toBeNull();
  });

  it("renders the loading state then the canvas", async () => {
    renderGraph();
    expect(screen.getByText(/Loading knowledge graph/i)).toBeInTheDocument();
    await screen.findByRole("img", { name: /Knowledge graph/i });
  });

  it("renders the empty state when nodes is empty", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse({ nodes: [], edges: [], next_cursor: null })),
    );
    renderGraph();
    expect(
      await screen.findByText(/No graph projected yet/i),
    ).toBeInTheDocument();
  });
});

describe("<GraphInspector />", () => {
  it("returns null with no node", () => {
    const { container } = render(
      <GraphInspector
        node={null}
        incoming={[]}
        outgoing={[]}
        onClose={() => {}}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders the Open-in-Review button when node has document_id", () => {
    const onOpen = vi.fn();
    render(
      <GraphInspector
        node={{
          id: "n1",
          kind: "entity",
          label: "x",
          properties: { document_id: "doc-1" },
        }}
        incoming={[]}
        outgoing={[]}
        onClose={() => {}}
        onOpenInReview={onOpen}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Open in Review/ }));
    expect(onOpen).toHaveBeenCalledWith("doc-1");
  });
});
