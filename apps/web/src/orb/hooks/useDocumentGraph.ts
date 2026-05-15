/**
 * useDocumentGraph — fetch the per-document knowledge-graph projection.
 *
 * Wraps `getDocumentGraph(documentId)` and exposes the raw
 * `{nodes, edges}` payload alongside a `byKind` filter helper, mirroring
 * the shape the per-document graph tab consumes.
 *
 * Scope note: Knowledge Forge intentionally has no corpus-wide graph
 * surface — corpus exploration belongs to the Knowledge Explorer app.
 * This hook is therefore scoped to a single document id and refuses to
 * fetch when the id is null/undefined.
 */

import { useEffect, useMemo, useState } from "react";

import { ApiError, getDocumentGraph } from "../../api/client";
import type {
  ApiGraphEdge,
  ApiGraphNode,
  ApiKnowledgeGraphProjection,
} from "../../api/types";

export type GraphFilter =
  | "all"
  | "topics"
  | "entities"
  | "chunks"
  | "relations"
  | "sourcebacked";

export type GraphStatus = "idle" | "loading" | "ok" | "empty" | "error";

export interface UseDocumentGraphResult {
  status: GraphStatus;
  nodes: ApiGraphNode[];
  edges: ApiGraphEdge[];
  error: Error | null;
  refetch: () => void;
}

export function useDocumentGraph(
  documentId: string | null | undefined,
): UseDocumentGraphResult {
  const [state, setState] = useState<{
    status: GraphStatus;
    payload: ApiKnowledgeGraphProjection | null;
    error: Error | null;
  }>({
    status: documentId ? "loading" : "idle",
    payload: null,
    error: null,
  });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!documentId) {
      setState({ status: "idle", payload: null, error: null });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, status: "loading", error: null }));
    getDocumentGraph(documentId)
      .then((payload) => {
        if (cancelled) return;
        const status =
          payload.nodes.length === 0 ? ("empty" as const) : ("ok" as const);
        setState({ status, payload, error: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        const error =
          err instanceof ApiError || err instanceof Error
            ? err
            : new Error(String(err));
        setState({ status: "error", payload: null, error });
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, tick]);

  return {
    status: state.status,
    nodes: state.payload?.nodes ?? [],
    edges: state.payload?.edges ?? [],
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
 * "Source-backed" is approximated by checking for any incident edge
 * because the v0 graph schema doesn't yet emit a dedicated `cites`
 * edge kind.
 */
export function applyGraphFilter(
  filter: GraphFilter,
  nodes: ApiGraphNode[],
  edges: ApiGraphEdge[],
): FilterApplied {
  if (filter === "all") return { nodes, edges };

  if (filter === "relations" || filter === "sourcebacked") {
    const ids = new Set<string>();
    for (const e of edges) {
      ids.add(e.source_id);
      ids.add(e.target_id);
    }
    return { nodes: nodes.filter((n) => ids.has(n.id)), edges };
  }

  const kindMap: Record<
    "topics" | "entities" | "chunks",
    ApiGraphNode["kind"]
  > = { topics: "topic", entities: "entity", chunks: "chunk" };
  const target = kindMap[filter];
  const filteredNodes = nodes.filter((n) => n.kind === target);
  const ids = new Set(filteredNodes.map((n) => n.id));
  const filteredEdges = edges.filter(
    (e) => ids.has(e.source_id) && ids.has(e.target_id),
  );
  return { nodes: filteredNodes, edges: filteredEdges };
}

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
