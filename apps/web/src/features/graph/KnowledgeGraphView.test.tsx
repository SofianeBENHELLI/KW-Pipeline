import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgeGraphView } from "./KnowledgeGraphView";
import type { ApiKnowledgeGraphProjection } from "../../api/types";

// Replace the real NVL renderer with a stub. jsdom can't render the canvas/SVG
// the real component draws into, so we assert against this marker instead and
// keep the test fast/hermetic.
vi.mock("@neo4j-nvl/react", () => ({
  InteractiveNvlWrapper: (props: { nodes: unknown[]; rels: unknown[] }) => (
    <div
      data-testid="nvl-stub"
      data-node-count={props.nodes.length}
      data-rel-count={props.rels.length}
    />
  ),
}));

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// `openapi-fetch` invokes `fetch` with a Request object (not a URL string).
function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

const FIXTURE_PROJECTION: ApiKnowledgeGraphProjection = {
  document_id: "doc-001",
  version_id: "ver-001",
  schema_version: "v0.1",
  generated_at: "2026-05-01T00:00:00Z",
  nodes: [
    { id: "doc-001", kind: "document", label: "policy.pdf", properties: {} },
    { id: "ver-001", kind: "version", label: "v1", properties: {} },
    { id: "sec-001", kind: "section", label: "Intro", properties: {} },
  ],
  edges: [
    {
      id: "e-1",
      source_id: "ver-001",
      target_id: "doc-001",
      kind: "part_of",
      properties: {},
    },
    {
      id: "e-2",
      source_id: "sec-001",
      target_id: "ver-001",
      kind: "part_of",
      properties: {},
    },
  ],
};

// ─── Tests ───────────────────────────────────────────────────────────────────

describe("KnowledgeGraphView", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the empty-state message when documentId is null", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      throw new Error("fetch should not be called when documentId is null");
    });

    render(<KnowledgeGraphView documentId={null} />);

    expect(
      screen.getByText(/Select a document to view its knowledge graph\./i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("nvl-stub")).not.toBeInTheDocument();
  });

  it("shows a loading state while the projection is being fetched", async () => {
    let resolveFetch: (value: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(() => pending);

    render(<KnowledgeGraphView documentId="doc-001" />);

    expect(await screen.findByText(/Loading graph…/i)).toBeInTheDocument();

    // Let the test finish cleanly so React doesn't complain about an
    // unresolved act(...) update.
    resolveFetch(makeJsonResponse(FIXTURE_PROJECTION));
    await screen.findByTestId("nvl-stub");
  });

  it("renders the NVL container after a successful fetch", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/documents/doc-001/graph")) {
          return Promise.resolve(makeJsonResponse(FIXTURE_PROJECTION));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );

    render(<KnowledgeGraphView documentId="doc-001" />);

    const stub = await screen.findByTestId("nvl-stub");
    expect(stub).toBeInTheDocument();
    expect(stub.getAttribute("data-node-count")).toBe("3");
    expect(stub.getAttribute("data-rel-count")).toBe("2");
    // The fixed-height canvas wrapper is rendered around the stub.
    expect(screen.getByTestId("knowledge-graph-canvas")).toBeInTheDocument();
  });

  it("shows an error banner when the fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse({ detail: "boom" }, 500)),
    );

    render(<KnowledgeGraphView documentId="doc-001" />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    expect(screen.getByText(/Failed to load graph/i)).toBeInTheDocument();
    expect(screen.getByText(/boom/)).toBeInTheDocument();
    expect(screen.queryByTestId("nvl-stub")).not.toBeInTheDocument();
  });
});
