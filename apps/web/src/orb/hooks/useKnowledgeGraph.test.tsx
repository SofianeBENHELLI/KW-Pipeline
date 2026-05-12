/**
 * useKnowledgeGraph + applyGraphFilter + neighborsOf tests.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiGraphEdge, ApiGraphNode } from "../../api/types";
import {
  applyGraphFilter,
  neighborsOf,
  useKnowledgeGraph,
} from "./useKnowledgeGraph";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const NODES: ApiGraphNode[] = [
  { id: "t1", kind: "topic",  label: "arr-walk",   properties: {} },
  { id: "t2", kind: "topic",  label: "renewals",   properties: {} },
  { id: "e1", kind: "entity", label: "$8.4M",      properties: {} },
  { id: "c1", kind: "chunk",  label: "chunk-1",    properties: {} },
  { id: "c2", kind: "chunk",  label: "chunk-2",    properties: {} },
];
const EDGES: ApiGraphEdge[] = [
  { id: "1", kind: "belongs_to", source_id: "c1", target_id: "t1", properties: {} },
  { id: "2", kind: "belongs_to", source_id: "c2", target_id: "t1", properties: {} },
  { id: "3", kind: "has_entity", source_id: "c1", target_id: "e1", properties: {} },
];

describe("applyGraphFilter", () => {
  it("'all' returns the input unchanged", () => {
    const out = applyGraphFilter("all", NODES, EDGES);
    expect(out.nodes).toBe(NODES);
    expect(out.edges).toBe(EDGES);
  });

  it("'topics' keeps only topic nodes and edges between them", () => {
    const out = applyGraphFilter("topics", NODES, EDGES);
    expect(out.nodes.map((n) => n.id).sort()).toEqual(["t1", "t2"]);
    expect(out.edges).toEqual([]); // no topic-topic edges in fixture
  });

  it("'chunks' keeps only chunk nodes", () => {
    const out = applyGraphFilter("chunks", NODES, EDGES);
    expect(out.nodes.map((n) => n.id).sort()).toEqual(["c1", "c2"]);
  });

  it("'relations' keeps only nodes that participate in edges", () => {
    const out = applyGraphFilter("relations", NODES, EDGES);
    expect(out.nodes.map((n) => n.id).sort()).toEqual(["c1", "c2", "e1", "t1"]);
    // t2 has no edges → excluded
  });

  it("'sourcebacked' applies the same v0 approximation as relations", () => {
    const out = applyGraphFilter("sourcebacked", NODES, EDGES);
    expect(out.nodes.map((n) => n.id).sort()).toEqual(["c1", "c2", "e1", "t1"]);
  });
});

describe("neighborsOf", () => {
  it("splits edges into incoming + outgoing", () => {
    const out = neighborsOf("t1", EDGES);
    expect(out.outgoing).toEqual([]);
    expect(out.incoming.map((e) => e.id).sort()).toEqual(["1", "2"]);
  });
});

describe("useKnowledgeGraph", () => {
  afterEach(() => vi.restoreAllMocks());

  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        makeJsonResponse({ nodes: NODES, edges: EDGES, next_cursor: null }),
      ),
    );
  });

  it("loads + resolves to 'ok'", async () => {
    const { result } = renderHook(() => useKnowledgeGraph());
    await waitFor(() => expect(result.current.status).toBe("ok"));
    expect(result.current.nodes).toHaveLength(5);
    expect(result.current.edges).toHaveLength(3);
  });

  it("resolves to 'empty' when no nodes", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        makeJsonResponse({ nodes: [], edges: [], next_cursor: null }),
      ),
    );
    const { result } = renderHook(() => useKnowledgeGraph());
    await waitFor(() => expect(result.current.status).toBe("empty"));
  });

  it("propagates fetch errors as 'error'", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => useKnowledgeGraph());
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error?.message).toBe("network down");
  });
});
