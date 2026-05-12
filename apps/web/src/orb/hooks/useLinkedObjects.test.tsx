/**
 * useLinkedObjects + projectGraph tests.
 *
 * The hook is mostly a thin wrapper around getDocumentGraph + the
 * projectGraph reducer. The reducer is where all the cross-highlight
 * book-keeping lives, so it gets the lion's share of the cases.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiKnowledgeGraphProjection } from "../../api/types";
import { projectGraph, useLinkedObjects } from "./useLinkedObjects";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const FIXTURE: ApiKnowledgeGraphProjection = {
  document_id: "doc-1",
  version_id: "ver-1",
  generated_at: "2026-05-12T09:00:00Z",
  schema_version: "v0.2",
  nodes: [
    {
      id: "c1",
      kind: "chunk",
      label: "chunk-1",
      properties: { text: "Net new ARR closed at $8.4M.", page: 1 },
    },
    {
      id: "c2",
      kind: "chunk",
      label: "chunk-2",
      properties: { text: "Expansion dragged plan by $0.4M.", page: 1 },
    },
    {
      id: "c3",
      kind: "chunk",
      label: "chunk-3",
      properties: { text: "Renewal cohort slipped four contracts.", page: 2 },
    },
    {
      id: "t1",
      kind: "topic",
      label: "arr-walk",
      properties: { keywords: ["netnew", "churn", "plan"] },
    },
    {
      id: "t2",
      kind: "topic",
      label: "renewal-slip",
      properties: { keywords: ["renewal", "SLA"] },
    },
    {
      id: "e1",
      kind: "entity",
      label: "$8.4M NetNew",
      properties: { type: "monetary" },
    },
    {
      id: "e2",
      kind: "entity",
      label: "FY26 plan",
      properties: { type: "reference" },
    },
  ],
  edges: [
    { id: "edge1", kind: "belongs_to", source_id: "c1", target_id: "t1", properties: {} },
    { id: "edge2", kind: "belongs_to", source_id: "c2", target_id: "t1", properties: {} },
    { id: "edge3", kind: "belongs_to", source_id: "c3", target_id: "t2", properties: {} },
    { id: "edge4", kind: "has_entity", source_id: "c1", target_id: "e1", properties: {} },
    { id: "edge5", kind: "has_entity", source_id: "c2", target_id: "e1", properties: {} },
    { id: "edge6", kind: "has_entity", source_id: "c2", target_id: "e2", properties: {} },
  ],
};

describe("projectGraph", () => {
  it("groups nodes by kind and orders chunks by page", () => {
    const out = projectGraph(FIXTURE);
    expect(out.chunks.map((c) => c.id)).toEqual(["c1", "c2", "c3"]);
    expect(out.topics.map((t) => t.id)).toEqual(["t1", "t2"]); // alpha
    // "$8.4M NetNew" (e1) sorts before "FY26 plan" (e2) by
    // localeCompare — `$` (U+0024) < `F` (U+0046). Pin the order so a
    // regression in projectGraph's sort is loud.
    expect(out.entities.map((e) => e.id)).toEqual(["e1", "e2"]);
  });

  it("hydrates per-chunk topic + entities from the edges", () => {
    const out = projectGraph(FIXTURE);
    const c1 = out.chunks.find((c) => c.id === "c1")!;
    const c2 = out.chunks.find((c) => c.id === "c2")!;
    const c3 = out.chunks.find((c) => c.id === "c3")!;
    expect(c1.topicId).toBe("t1");
    expect(c1.entityIds.sort()).toEqual(["e1"]);
    expect(c2.topicId).toBe("t1");
    expect(c2.entityIds.sort()).toEqual(["e1", "e2"]);
    expect(c3.topicId).toBe("t2");
    expect(c3.entityIds).toEqual([]);
  });

  it("hydrates topic→chunks and entity→chunks reverse maps", () => {
    const out = projectGraph(FIXTURE);
    expect([...out.topicToChunks.get("t1")!].sort()).toEqual(["c1", "c2"]);
    expect([...out.topicToChunks.get("t2")!].sort()).toEqual(["c3"]);
    expect([...out.entityToChunks.get("e1")!].sort()).toEqual(["c1", "c2"]);
    expect([...out.entityToChunks.get("e2")!].sort()).toEqual(["c2"]);
  });

  it("returns empty arrays for an empty projection", () => {
    const out = projectGraph({
      document_id: "x",
      version_id: "v",
      generated_at: "x",
      schema_version: "v0.2",
      nodes: [],
      edges: [],
    });
    expect(out.chunks).toEqual([]);
    expect(out.topics).toEqual([]);
    expect(out.entities).toEqual([]);
  });

  it("returns empty arrays for null payload", () => {
    const out = projectGraph(null);
    expect(out.chunks).toEqual([]);
  });
});

describe("useLinkedObjects", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns 'idle' for null id and never fetches", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useLinkedObjects(null));
    expect(result.current.status).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  describe("with a real id", () => {
    beforeEach(() => {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        makeJsonResponse(FIXTURE),
      );
    });

    it("loads + projects the graph and resolves to 'ok'", async () => {
      const { result } = renderHook(() => useLinkedObjects("doc-1"));
      expect(result.current.status).toBe("loading");
      await waitFor(() => expect(result.current.status).toBe("ok"));
      expect(result.current.data.chunks).toHaveLength(3);
      expect(result.current.data.topics).toHaveLength(2);
      expect(result.current.data.entities).toHaveLength(2);
    });
  });

  it("resolves to 'empty' when the projection has no nodes", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        document_id: "x",
        version_id: "v",
        generated_at: "x",
        schema_version: "v0.2",
        nodes: [],
        edges: [],
      }),
    );
    const { result } = renderHook(() => useLinkedObjects("doc-1"));
    await waitFor(() => expect(result.current.status).toBe("empty"));
    expect(result.current.data.chunks).toEqual([]);
  });

  it("resolves to 'error' on a network failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Down"));
    const { result } = renderHook(() => useLinkedObjects("doc-1"));
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error?.message).toBe("Down");
  });
});
