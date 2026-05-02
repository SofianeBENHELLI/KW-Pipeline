import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import KnowledgeGraphView from "./KnowledgeGraphView";
import type { ApiKnowledgeGraphProjection } from "../../api/types";
import { MOCK_V0_2_PROJECTION } from "./__mocks__/v0_2_payload";

// Replace the real NVL renderer with a stub. jsdom can't render the canvas/SVG
// the real component draws into, so we assert against this marker instead and
// keep the test fast/hermetic.
//
// The stub also renders the node captions / edge captions in inert
// ``data-*`` attributes so v0.2 tests can assert that specific node
// labels (``Chunk 1``, ``Eligibility & income``, …) and edge captions
// (``has chunk``, ``belongs to``, ``shares keyword``) made it through
// the adapter. Real NVL rendering still happens in the browser.
interface NvlStubNode {
  id: string;
  captions: { value: string }[];
  color: string;
}
interface NvlStubRel {
  id: string;
  captions: { value: string }[];
  color: string;
}

vi.mock("@neo4j-nvl/react", () => ({
  InteractiveNvlWrapper: (props: {
    nodes: NvlStubNode[];
    rels: NvlStubRel[];
  }) => (
    <div
      data-testid="nvl-stub"
      data-node-count={props.nodes.length}
      data-rel-count={props.rels.length}
      data-node-captions={props.nodes
        .map((n) => n.captions.map((c) => c.value).join("|"))
        .join(",")}
      data-rel-captions={props.rels
        .map((r) => r.captions.map((c) => c.value).join("|"))
        .join(",")}
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

// ─── v0.2: chunk / topic rendering via the mock data path ───────────────────

describe("KnowledgeGraphView — v0.2 chunk/topic rendering", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders chunks and topics from the mock projection with the right captions", () => {
    // Mock data path skips fetch entirely — guard against an
    // accidental network call regressing into the live path.
    vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      throw new Error("fetch should not be called when mockData is provided");
    });

    render(
      <KnowledgeGraphView documentId="doc-001" mockData={MOCK_V0_2_PROJECTION} />,
    );

    const stub = screen.getByTestId("nvl-stub");

    // 1 doc + 1 ver + 1 sec + 4 chunks + 2 topics = 9 nodes.
    // 6 structural + 4 belongs_to + 3 semantic = 13 edges.
    expect(stub.getAttribute("data-node-count")).toBe("9");
    expect(stub.getAttribute("data-rel-count")).toBe("13");

    const captions = stub.getAttribute("data-node-captions") ?? "";
    // Chunk and topic captions made it through the adapter — these are
    // the user-visible node labels in the canvas.
    expect(captions).toContain("Chunk 1");
    expect(captions).toContain("Chunk 4");
    expect(captions).toContain("Eligibility & income");
    expect(captions).toContain("Application process");
  });

  it("renders belongs_to and shares_keyword edges with their captions", () => {
    render(
      <KnowledgeGraphView documentId="doc-001" mockData={MOCK_V0_2_PROJECTION} />,
    );

    const stub = screen.getByTestId("nvl-stub");
    const relCaptions = stub.getAttribute("data-rel-captions") ?? "";

    // belongs_to (chunk → topic) is rendered.
    expect(relCaptions).toContain("belongs to");
    // shares_keyword (chunk ↔ chunk via shared keywords) is rendered.
    expect(relCaptions).toContain("shares keyword");
    // has_chunk structural edges are rendered.
    expect(relCaptions).toContain("has chunk");
    // same_topic_as semantic edge is rendered.
    expect(relCaptions).toContain("same topic");
  });

  it("legend reflects all six v0.2 node kinds", () => {
    render(
      <KnowledgeGraphView documentId="doc-001" mockData={MOCK_V0_2_PROJECTION} />,
    );

    const legend = screen.getByLabelText(/Node kind legend/i);
    // Six kinds — every entry must be present so the legend stays
    // exhaustive against ``GraphNodeKindV02``.
    expect(legend).toHaveTextContent(/Document/);
    expect(legend).toHaveTextContent(/Version/);
    expect(legend).toHaveTextContent(/Section/);
    expect(legend).toHaveTextContent(/Chunk/);
    expect(legend).toHaveTextContent(/Topic/);
    expect(legend).toHaveTextContent(/Entity/);
  });

  it("does not call fetch when mockData is provided", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      return Promise.reject(new Error("should not be called"));
    });

    render(
      <KnowledgeGraphView documentId="doc-001" mockData={MOCK_V0_2_PROJECTION} />,
    );

    expect(fetchSpy).not.toHaveBeenCalled();
    // And the canvas (not the empty-state) is what's on screen.
    expect(screen.getByTestId("knowledge-graph-canvas")).toBeInTheDocument();
  });

  it("clears the panel when documentId becomes null even with mockData set", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      throw new Error("fetch should not be called");
    });

    render(
      <KnowledgeGraphView documentId={null} mockData={MOCK_V0_2_PROJECTION} />,
    );

    expect(
      screen.getByText(/Select a document to view its knowledge graph\./i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("nvl-stub")).not.toBeInTheDocument();
  });
});

// ─── v0.2: typed property helpers ───────────────────────────────────────────

describe("graph types helpers", () => {
  it("asChunkNodeProperties returns typed view for chunk nodes only", async () => {
    const mod = await import("./types");
    const chunk = MOCK_V0_2_PROJECTION.nodes.find((n) => n.kind === "chunk");
    const document = MOCK_V0_2_PROJECTION.nodes.find(
      (n) => n.kind === "document",
    );
    expect(chunk).toBeDefined();
    expect(document).toBeDefined();

    // Cast through generated wire types — helpers accept the union
    // ``AnyGraphNode`` and discriminate by ``kind`` at runtime.
    const chunkProps = mod.asChunkNodeProperties(chunk!);
    expect(chunkProps).toBeDefined();
    expect(chunkProps?.index).toBe(0);
    expect(chunkProps?.token_count).toBe(142);

    expect(mod.asChunkNodeProperties(document!)).toBeUndefined();
  });

  it("asTopicNodeProperties returns typed view for topic nodes only", async () => {
    const mod = await import("./types");
    const topic = MOCK_V0_2_PROJECTION.nodes.find((n) => n.kind === "topic");
    const chunk = MOCK_V0_2_PROJECTION.nodes.find((n) => n.kind === "chunk");
    expect(topic).toBeDefined();

    const props = mod.asTopicNodeProperties(topic!);
    expect(props).toBeDefined();
    expect(props?.size).toBe(2);
    expect(props?.keywords).toContain("eligibility");

    expect(mod.asTopicNodeProperties(chunk!)).toBeUndefined();
  });

  it("asChunkRelationEdgeProperties accepts the three semantic edge kinds", async () => {
    const mod = await import("./types");
    const sharesKeyword = MOCK_V0_2_PROJECTION.edges.find(
      (e) => e.kind === "shares_keyword",
    );
    const belongsTo = MOCK_V0_2_PROJECTION.edges.find(
      (e) => e.kind === "belongs_to",
    );
    expect(sharesKeyword).toBeDefined();

    const props = mod.asChunkRelationEdgeProperties(sharesKeyword!);
    expect(props).toBeDefined();
    expect(props?.weight).toBe(0.5);
    expect(props?.shared_keywords).toContain("eligibility");

    // belongs_to is NOT a chunk-relation edge — it's a topic-membership edge.
    expect(mod.asChunkRelationEdgeProperties(belongsTo!)).toBeUndefined();
    expect(mod.asTopicMembershipEdgeProperties(belongsTo!)).toBeDefined();
  });

  it("asStructuralEdgeProperties accepts part_of / has_version / has_chunk only", async () => {
    const mod = await import("./types");
    const hasChunk = MOCK_V0_2_PROJECTION.edges.find(
      (e) => e.kind === "has_chunk",
    );
    const sharesKeyword = MOCK_V0_2_PROJECTION.edges.find(
      (e) => e.kind === "shares_keyword",
    );
    expect(hasChunk).toBeDefined();
    expect(sharesKeyword).toBeDefined();

    expect(mod.asStructuralEdgeProperties(hasChunk!)).toBeDefined();
    expect(mod.asStructuralEdgeProperties(sharesKeyword!)).toBeUndefined();
  });
});
