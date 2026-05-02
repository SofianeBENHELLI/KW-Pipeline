import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import KnowledgeGraphView, { filterProjection } from "./KnowledgeGraphView";
import type { ApiKnowledgeGraphProjection } from "../../api/types";
import { v0_2_enrichedProjection as ENRICHED_PROJECTION } from "./__mocks__/v0_2_payload";

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

const EMPTY_PROJECTION: ApiKnowledgeGraphProjection = {
  document_id: "doc-001",
  version_id: "ver-001",
  schema_version: "v0.1",
  generated_at: "2026-05-01T00:00:00Z",
  nodes: [],
  edges: [],
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

  it("shows an error banner with a Retry button when the fetch fails", async () => {
    let attempts = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      attempts += 1;
      if (attempts === 1) {
        return Promise.resolve(makeJsonResponse({ detail: "boom" }, 503));
      }
      return Promise.resolve(makeJsonResponse(FIXTURE_PROJECTION));
    });

    render(<KnowledgeGraphView documentId="doc-001" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(screen.getByText(/Couldn't load the knowledge graph/i)).toBeInTheDocument();
    expect(screen.getByText(/boom/)).toBeInTheDocument();
    expect(screen.queryByTestId("nvl-stub")).not.toBeInTheDocument();

    // Retry must be a real button so it's keyboard-reachable. Click it and
    // verify the panel recovers with a fresh payload.
    const retry = screen.getByRole("button", { name: /retry/i });
    fireEvent.click(retry);

    const stub = await screen.findByTestId("nvl-stub");
    expect(stub).toBeInTheDocument();
    expect(attempts).toBe(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders the pre-validation copy when status !== VALIDATED and payload is empty", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse(EMPTY_PROJECTION)),
    );

    render(
      <KnowledgeGraphView documentId="doc-001" documentStatus="NEEDS_REVIEW" />,
    );

    expect(
      await screen.findByText(/after a reviewer validates this document/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("nvl-stub")).not.toBeInTheDocument();
    // Make sure the layer-disabled copy is NOT shown — they must be distinct.
    expect(
      screen.queryByText(/optional add-on/i),
    ).not.toBeInTheDocument();
  });

  it("renders the disabled-layer copy when status === VALIDATED and payload is empty", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse(EMPTY_PROJECTION)),
    );

    render(
      <KnowledgeGraphView documentId="doc-001" documentStatus="VALIDATED" />,
    );

    expect(
      await screen.findByText(/optional add-on/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/KW_KNOWLEDGE_LAYER_ENABLED=true/),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("nvl-stub")).not.toBeInTheDocument();
    // The pre-validation copy must NOT show here.
    expect(
      screen.queryByText(/after a reviewer validates this document/i),
    ).not.toBeInTheDocument();
  });

  it("re-fetches and shows the latest payload when refreshKey changes", async () => {
    // Track the inflight resolvers so the test can control ordering.
    const resolvers: Array<(value: Response) => void> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      return new Promise<Response>((resolve) => {
        resolvers.push(resolve);
      });
    });

    const { rerender } = render(
      <KnowledgeGraphView documentId="doc-001" refreshKey={0} />,
    );

    // Wait for the first fetch to be issued.
    await waitFor(() => expect(resolvers).toHaveLength(1));

    // Bump refreshKey while the first request is still inflight; this
    // should issue a second request and the cancel flag should drop the
    // first response when it eventually arrives.
    rerender(<KnowledgeGraphView documentId="doc-001" refreshKey={1} />);
    await waitFor(() => expect(resolvers).toHaveLength(2));

    // First (now-stale) request resolves with an old/empty payload.
    await act(async () => {
      resolvers[0](makeJsonResponse(EMPTY_PROJECTION));
    });
    // Second (latest) request resolves with the real projection.
    await act(async () => {
      resolvers[1](makeJsonResponse(FIXTURE_PROJECTION));
    });

    const stub = await screen.findByTestId("nvl-stub");
    expect(stub.getAttribute("data-node-count")).toBe("3");
    expect(stub.getAttribute("data-rel-count")).toBe("2");
  });
});

// ─── Demo KG (Lane D) — v0.2 fixtures ────────────────────────────────────────
//
// The enriched projection moved to `./__mocks__/v0_2_payload.ts` (#164)
// so other consumers can share one source of truth. Imported above as
// `ENRICHED_PROJECTION` to keep these test bodies untouched.

describe("filterProjection", () => {
  it("returns the input unchanged for the All filter", () => {
    const result = filterProjection(ENRICHED_PROJECTION, "all");
    expect(result.nodes).toEqual(ENRICHED_PROJECTION.nodes);
    expect(result.edges).toEqual(ENRICHED_PROJECTION.edges);
  });

  it("Chunks filter keeps only chunk and topic nodes", () => {
    const result = filterProjection(ENRICHED_PROJECTION, "chunks");
    const kinds = new Set(result.nodes.map((n) => n.kind));
    expect(kinds).toEqual(new Set(["chunk", "topic"]));
  });

  it("Topics filter shows topic nodes plus their member chunks and belongs_to edges", () => {
    const result = filterProjection(ENRICHED_PROJECTION, "topics");
    expect(new Set(result.nodes.map((n) => n.id))).toEqual(
      new Set(["topic-aaaa1111", "alpha", "beta"]),
    );
    expect(result.edges.every((e) => e.kind === "belongs_to")).toBe(true);
    expect(result.edges).toHaveLength(2);
  });

  it("Relations filter shows chunks and only deterministic semantic edges", () => {
    const result = filterProjection(ENRICHED_PROJECTION, "relations");
    expect(new Set(result.nodes.map((n) => n.kind))).toEqual(new Set(["chunk"]));
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0].kind).toBe("same_topic_as");
  });

  it("Entities filter shows only entity nodes and has_entity edges", () => {
    const result = filterProjection(ENRICHED_PROJECTION, "entities");
    expect(result.nodes.map((n) => n.kind)).toEqual(["entity"]);
    // The has_entity edge has source=alpha (chunk), so it's filtered out
    // because alpha is not in the entities-only kept set. This is the
    // correct semantic — entity-only view should not show edges that
    // reference filtered-out nodes.
    expect(result.edges).toHaveLength(0);
  });

  it("Source-backed filter keeps nodes/edges with a source-reference imprint", () => {
    const result = filterProjection(ENRICHED_PROJECTION, "source-backed");
    // alpha and beta both have source_reference_count > 0; chunks survive.
    const ids = new Set(result.nodes.map((n) => n.id));
    expect(ids.has("alpha")).toBe(true);
    expect(ids.has("beta")).toBe(true);
    expect(ids.has("doc-001")).toBe(false);
    expect(ids.has("topic-aaaa1111")).toBe(false);
  });

  it("does not crash on an empty projection", () => {
    const empty = { nodes: [], edges: [] };
    expect(filterProjection(empty, "all")).toEqual(empty);
    expect(filterProjection(empty, "chunks")).toEqual(empty);
    expect(filterProjection(empty, "topics")).toEqual(empty);
    expect(filterProjection(empty, "relations")).toEqual(empty);
    expect(filterProjection(empty, "entities")).toEqual(empty);
    expect(filterProjection(empty, "source-backed")).toEqual(empty);
  });
});

describe("KnowledgeGraphView (Demo KG / Lane D)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function mockEnrichedFetch() {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse(ENRICHED_PROJECTION)),
    );
  }

  it("renders chunk and topic nodes in the canvas (#149)", async () => {
    mockEnrichedFetch();

    render(<KnowledgeGraphView documentId="doc-001" />);

    const stub = await screen.findByTestId("nvl-stub");
    // 2 document/version + 2 chunks + 1 topic + 1 entity + 1 future = 7
    expect(stub.getAttribute("data-node-count")).toBe(
      String(ENRICHED_PROJECTION.nodes.length),
    );
    expect(stub.getAttribute("data-rel-count")).toBe(
      String(ENRICHED_PROJECTION.edges.length),
    );
  });

  it("filters down to chunks-only when the Chunks toggle is pressed (#150)", async () => {
    mockEnrichedFetch();

    render(<KnowledgeGraphView documentId="doc-001" />);
    await screen.findByTestId("nvl-stub");

    const chunksButton = screen.getByRole("button", { name: /^chunks$/i });
    fireEvent.click(chunksButton);

    const stub = await screen.findByTestId("nvl-stub");
    // chunks + topic = 3 nodes; the only edge with both endpoints in
    // that set is the chunk→chunk same_topic_as.
    expect(stub.getAttribute("data-node-count")).toBe("3");
    expect(chunksButton.getAttribute("aria-pressed")).toBe("true");
  });

  it("filters to relations only when the Relations toggle is pressed (#150)", async () => {
    mockEnrichedFetch();

    render(<KnowledgeGraphView documentId="doc-001" />);
    await screen.findByTestId("nvl-stub");

    fireEvent.click(screen.getByRole("button", { name: /^relations$/i }));

    const stub = await screen.findByTestId("nvl-stub");
    expect(stub.getAttribute("data-node-count")).toBe("2");
    expect(stub.getAttribute("data-rel-count")).toBe("1");
  });

  it("shows chunk details when a chunk node is clicked in the inspector (#151)", async () => {
    mockEnrichedFetch();

    render(<KnowledgeGraphView documentId="doc-001" />);
    await screen.findByTestId("nvl-stub");

    const nodeList = screen.getByTestId("graph-inspector-nodes");
    const chunkButton = within(nodeList).getByRole("button", { name: /audit plan/i });
    fireEvent.click(chunkButton);

    const detail = await screen.findByTestId("graph-detail-node");
    // "Audit plan" appears in both the Label and Heading rows of the
    // detail dl, so assert via getAllByText rather than getByText.
    expect(within(detail).getAllByText("Audit plan").length).toBeGreaterThan(0);
    expect(within(detail).getByText(/topic-aaaa1111/)).toBeInTheDocument();
    expect(within(detail).getByText(/audit, supplier, quality/i)).toBeInTheDocument();
  });

  it("shows relation details when a same_topic_as edge is clicked (#151)", async () => {
    mockEnrichedFetch();

    render(<KnowledgeGraphView documentId="doc-001" />);
    await screen.findByTestId("nvl-stub");

    const edgeList = screen.getByTestId("graph-inspector-edges");
    const relationButton = within(edgeList).getByRole("button", {
      name: /alpha → beta/,
    });
    fireEvent.click(relationButton);

    const detail = await screen.findByTestId("graph-detail-edge");
    expect(within(detail).getByText(/same topic/i)).toBeInTheDocument();
    expect(within(detail).getByText(/0\.420/)).toBeInTheDocument();
    expect(within(detail).getByText(/Share 3 topic keywords/)).toBeInTheDocument();
    expect(within(detail).getByText(/audit, quality, supplier/i)).toBeInTheDocument();
  });

  it("clears the selection when the selected node is filtered out (#150)", async () => {
    mockEnrichedFetch();

    render(<KnowledgeGraphView documentId="doc-001" />);
    await screen.findByTestId("nvl-stub");

    const nodeList = screen.getByTestId("graph-inspector-nodes");
    fireEvent.click(within(nodeList).getByRole("button", { name: /audit plan/i }));
    expect(screen.getByTestId("graph-detail-node")).toBeInTheDocument();

    // Switch to Entities — alpha is filtered out, so the detail should
    // fall back to the empty-state hint.
    fireEvent.click(screen.getByRole("button", { name: /^entities$/i }));

    await waitFor(() =>
      expect(screen.queryByTestId("graph-detail-node")).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/click a node or edge above/i)).toBeInTheDocument();
  });
});
