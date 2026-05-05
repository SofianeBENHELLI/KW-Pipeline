import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KnowledgeGraphPage } from "../api/types";

import { KnowledgeSummary } from "./KnowledgeSummary";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makePage(
  nodes: { id: string; kind: string; label: string }[],
  next: string | null = null,
): KnowledgeGraphPage {
  return {
    schema_version: "v0.1",
    nodes: nodes.map((n) => ({
      id: n.id,
      kind: n.kind,
      label: n.label,
      properties: {},
    })),
    edges: [],
    next_cursor: next,
  };
}

const BASE_PROPS = {
  apiBaseUrl: "http://test",
  refreshTick: 0,
};

describe("KnowledgeSummary (widget)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders Loading… while the first page is in flight", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));
    render(<KnowledgeSummary {...BASE_PROPS} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders nodes / edges totals and the top-N kind tiles after a single page", async () => {
    // Counts deliberately distinct so getByText doesn't match multiple
    // elements (kw-kg-hero__num vs kw-kg-tile__num share the rendering
    // path).
    const nodes = [
      { id: "n1", kind: "Person", label: "Alice" },
      { id: "n2", kind: "Person", label: "Bob" },
      { id: "n3", kind: "Person", label: "Carol" },
      { id: "n4", kind: "Person", label: "Dan" },
      { id: "n5", kind: "Person", label: "Eve" },
      { id: "n6", kind: "Org", label: "Acme" },
      { id: "n7", kind: "Org", label: "Globex" },
    ];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        ...makePage(nodes),
        edges: [
          { id: "e1", source_id: "n1", target_id: "n6", predicate: "works_at" },
          { id: "e2", source_id: "n2", target_id: "n7", predicate: "works_at" },
          { id: "e3", source_id: "n3", target_id: "n6", predicate: "works_at" },
        ],
      }),
    );

    render(<KnowledgeSummary {...BASE_PROPS} />);

    expect(await screen.findByText("Nodes")).toBeInTheDocument();
    expect(screen.getByText("Edges")).toBeInTheDocument();
    // 7 nodes, 3 edges → distinct hero numbers.
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    // Top-N kind tiles: Person (5) and Org (2).
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("Person")).toBeInTheDocument();
    expect(screen.getByText("Org")).toBeInTheDocument();
  });

  it("walks the cursor across multiple pages and aggregates counts", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy.mockResolvedValueOnce(
      makeJsonResponse(
        makePage(
          [
            { id: "a1", kind: "Person", label: "A" },
            { id: "a2", kind: "Person", label: "B" },
          ],
          "cursor-2",
        ),
      ),
    );
    fetchSpy.mockResolvedValueOnce(
      makeJsonResponse(
        makePage([{ id: "b1", kind: "Org", label: "C" }], null),
      ),
    );

    render(<KnowledgeSummary {...BASE_PROPS} />);

    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    // Page 2 request must carry ?cursor=cursor-2
    const urls = fetchSpy.mock.calls.map((call) => {
      const [arg] = call as [RequestInfo | URL, ...unknown[]];
      return typeof arg === "string"
        ? arg
        : arg instanceof URL
          ? arg.toString()
          : (arg as Request).url;
    });
    expect(urls.some((u) => u.includes("cursor=cursor-2"))).toBe(true);
  });

  it("renders the error message on ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          error: {
            code: "KW_KNOWLEDGE_DISABLED",
            message: "off",
            status: 503,
            retryable: false,
          },
          detail: "Knowledge layer is disabled.",
        },
        503,
      ),
    );

    render(<KnowledgeSummary {...BASE_PROPS} />);

    // asApiError prefers ``error.message`` over the outer ``detail``
    // when both are set, so we assert against the message.
    expect(
      await screen.findByText(/KW_KNOWLEDGE_DISABLED.*off/),
    ).toBeInTheDocument();
  });
});
