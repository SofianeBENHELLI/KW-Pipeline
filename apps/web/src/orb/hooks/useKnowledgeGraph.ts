/**
 * useKnowledgeGraph — fetch the corpus-wide knowledge-graph projection
 * for the /kf/graph view.
 *
 * Wraps `getKnowledgeGraph(limit, cursor)` from the existing client.
 * The graph endpoint paginates; PR 6 fetches the first page only and
 * surfaces the cursor so a future "load more" button can append.
 *
 * Returns the raw `{nodes, edges}` plus a `byKind` index so the filter
 * toolbar can switch between All / Topics / Entities / Chunks /
 * Source-backed without a refetch (per design §5: "pure-function
 * projections of the same dataset — no backend hit on toggle").
 */

import { useEffect, useMemo, useState } from "react";

import { ApiError, getKnowledgeGraph } from "../../api/client";
import type {
  ApiGraphEdge,
  ApiGraphNode,
  ApiKnowledgeGraphPage,
} from "../../api/types";

export type GraphFilter =
  | "all"
  | "topics"
  | "entities"
  | "chunks"
  | "relations"
  | "sourcebacked";

export type GraphStatus = "idle" | "loading" | "ok" | "empty" | "error";

export interface UseKnowledgeGraphResult {
  status: GraphStatus;
  nodes: ApiGraphNode[];
  edges: ApiGraphEdge[];
  nextCursor: string | null;
  error: Error | null;
  refetch: () => void;
}

export function useKnowledgeGraph(
  limit = 200,
): UseKnowledgeGraphResult {
  const [state, setState] = useState<{
    status: GraphStatus;
    page: ApiKnowledgeGraphPage | null;
    error: Error | null;
  }>({ status: "loading", page: null, error: null });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, status: "loading", error: null }));
    getKnowledgeGraph(limit)
      .then((page) => {
        if (cancelled) return;
        const status =
          page.nodes.length === 0 ? ("empty" as const) : ("ok" as const);
        setState({ status, page, error: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        const error =
          err instanceof ApiError || err instanceof Error
            ? err
            : new Error(String(err));
        setState({ status: "error", page: null, error });
      });
    return () => {
      cancelled = true;
    };
  }, [limit, tick]);

  return {
    status: state.status,
    nodes: state.page?.nodes ?? [],
    edges: state.page?.edges ?? [],
    nextCursor: state.page?.next_cursor ?? null,
    error: state.error,
    refetch: () => setTick((n) => n + 1),
  };
}

export interface FilterApplied {
  nodes: ApiGraphNode[];
  edges: ApiGraphEdge[];
}

/**
 * Apply a filter to the dataset. Pure: filter switching never refetches.
 *
 * Per design §5.1: "Source-backed shows only nodes that have at least
 * one citation edge." We approximate that by checking for any edge
 * incident to the node (any kind) since the v0 graph schema doesn't
 * yet emit a dedicated `cites` edge kind.
 */
export function applyGraphFilter(
  filter: GraphFilter,
  nodes: ApiGraphNode[],
  edges: ApiGraphEdge[],
): FilterApplied {
  if (filter === "all") return { nodes, edges };

  if (filter === "relations") {
    // Show only nodes that participate in at least one edge.
    const ids = new Set<string>();
    for (const e of edges) {
      ids.add(e.source_id);
      ids.add(e.target_id);
    }
    return {
      nodes: nodes.filter((n) => ids.has(n.id)),
      edges,
    };
  }

  if (filter === "sourcebacked") {
    // Same approximation as relations for v0 (see docstring).
    const ids = new Set<string>();
    for (const e of edges) {
      ids.add(e.source_id);
      ids.add(e.target_id);
    }
    return {
      nodes: nodes.filter((n) => ids.has(n.id)),
      edges,
    };
  }

  const kindMap: Record<
    "topics" | "entities" | "chunks",
    ApiGraphNode["kind"]
  > = {
    topics: "topic",
    entities: "entity",
    chunks: "chunk",
  };
  const target = kindMap[filter];
  const filteredNodes = nodes.filter((n) => n.kind === target);
  const ids = new Set(filteredNodes.map((n) => n.id));
  const filteredEdges = edges.filter(
    (e) => ids.has(e.source_id) && ids.has(e.target_id),
  );
  return { nodes: filteredNodes, edges: filteredEdges };
}

/**
 * Compute neighbours of a node (incoming + outgoing edges).
 *
 * Returns flat arrays so the inspector drawer can render them straight.
 */
export function neighborsOf(
  nodeId: string,
  edges: ApiGraphEdge[],
): { incoming: ApiGraphEdge[]; outgoing: ApiGraphEdge[] } {
  const incoming: ApiGraphEdge[] = [];
  const outgoing: ApiGraphEdge[] = [];
  for (const e of edges) {
    if (e.source_id === nodeId) outgoing.push(e);
    else if (e.target_id === nodeId) incoming.push(e);
  }
  return { incoming, outgoing };
}

/** Memoization helper used by GraphView. */
export function useFilteredGraph(
  filter: GraphFilter,
  nodes: ApiGraphNode[],
  edges: ApiGraphEdge[],
): FilterApplied {
  return useMemo(
    () => applyGraphFilter(filter, nodes, edges),
    [filter, nodes, edges],
  );
}
