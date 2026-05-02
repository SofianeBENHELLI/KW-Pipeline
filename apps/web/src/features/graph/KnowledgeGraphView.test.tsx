import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import KnowledgeGraphView from "./KnowledgeGraphView";
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
