/**
 * useLinkedObjects — fetch the knowledge-graph projection for a doc
 * and project nodes/edges into the shape the Linked View consumes.
 *
 * Wraps `getDocumentGraph(documentId)`. The raw projection is a flat
 * `{nodes, edges}` payload; the Linked View needs grouped collections
 * of `{topics, entities, chunks}` plus quick lookups for the
 * cross-highlight edges:
 *
 *   chunk → topic    (`belongs_to` edge)
 *   chunk → entities (`has_entity` edge)
 *
 * Both directions are precomputed once on each fetch so hover events
 * are O(1) reads — instant cross-highlight is non-negotiable per the
 * design handoff §3.4.
 *
 * Empty / disabled state: when the projection has no chunks (e.g. the
 * document never reached SEMANTIC_READY) the hook resolves with empty
 * arrays so the Linked View can render its own "nothing to show yet"
 * panel rather than a generic error.
 */

import { useEffect, useMemo, useState } from "react";

import { ApiError, getDocumentGraph } from "../../api/client";
import type {
  ApiGraphEdge,
  ApiGraphNode,
  ApiKnowledgeGraphProjection,
} from "../../api/types";

/** One chunk in the Linked View's right-pane card stack + doc paper. */
export interface LinkedChunk {
  id: string;
  text: string;
  page: number | null;
  topicId: string | null;
  entityIds: string[];
}

/** One topic. */
export interface LinkedTopic {
  id: string;
  label: string;
  keywords: string[];
  chunkIds: string[];
}

/** One entity. */
export interface LinkedEntity {
  id: string;
  label: string;
  type: string;
  chunkIds: string[];
}

export interface LinkedObjects {
  topics: LinkedTopic[];
  entities: LinkedEntity[];
  chunks: LinkedChunk[];
  /** chunk id → topic id (or null when none). */
  chunkToTopic: ReadonlyMap<string, string | null>;
  /** chunk id → set of entity ids. */
  chunkToEntities: ReadonlyMap<string, ReadonlySet<string>>;
  /** topic id → set of chunk ids belonging to it. */
  topicToChunks: ReadonlyMap<string, ReadonlySet<string>>;
  /** entity id → set of chunk ids containing it. */
  entityToChunks: ReadonlyMap<string, ReadonlySet<string>>;
}

const EMPTY: LinkedObjects = Object.freeze({
  topics: [],
  entities: [],
  chunks: [],
  chunkToTopic: new Map(),
  chunkToEntities: new Map(),
  topicToChunks: new Map(),
  entityToChunks: new Map(),
});

export type LinkedObjectsStatus =
  | "idle"
  | "loading"
  | "ok"
  | "empty"
  | "error";

export interface UseLinkedObjectsResult {
  status: LinkedObjectsStatus;
  data: LinkedObjects;
  error: Error | null;
  refetch: () => void;
}

/**
 * Project the raw graph payload into the Linked View shape.
 *
 * Exported so unit tests can pin the projection logic without touching
 * the network. Pure function — no React, no fetch.
 */
export function projectGraph(
  payload: ApiKnowledgeGraphProjection | null,
): LinkedObjects {
  if (!payload || payload.nodes.length === 0) return EMPTY;

  const topics: LinkedTopic[] = [];
  const entities: LinkedEntity[] = [];
  const chunks: LinkedChunk[] = [];
  const chunkToTopic = new Map<string, string | null>();
  const chunkToEntities = new Map<string, Set<string>>();
  const topicToChunks = new Map<string, Set<string>>();
  const entityToChunks = new Map<string, Set<string>>();

  for (const node of payload.nodes) {
    if (node.kind === "chunk") {
      chunks.push(buildChunk(node));
      chunkToEntities.set(node.id, new Set());
      chunkToTopic.set(node.id, null);
    } else if (node.kind === "topic") {
      topics.push(buildTopic(node));
      topicToChunks.set(node.id, new Set());
    } else if (node.kind === "entity") {
      entities.push(buildEntity(node));
      entityToChunks.set(node.id, new Set());
    }
  }

  for (const edge of payload.edges) {
    applyEdge(edge, {
      chunkToTopic,
      chunkToEntities,
      topicToChunks,
      entityToChunks,
      chunks,
    });
  }

  // Re-hydrate the per-chunk topic/entity ids so the right-pane cards
  // render meta lines without an extra lookup at render time.
  for (const ch of chunks) {
    ch.topicId = chunkToTopic.get(ch.id) ?? null;
    ch.entityIds = [...(chunkToEntities.get(ch.id) ?? new Set())];
  }
  // Hydrate topic/entity → chunk membership lists too.
  for (const t of topics) {
    t.chunkIds = [...(topicToChunks.get(t.id) ?? new Set())];
  }
  for (const e of entities) {
    e.chunkIds = [...(entityToChunks.get(e.id) ?? new Set())];
  }

  // Stable order: chunks by page asc, topics + entities by label asc.
  chunks.sort((a, b) => (a.page ?? 0) - (b.page ?? 0));
  topics.sort((a, b) => a.label.localeCompare(b.label));
  entities.sort((a, b) => a.label.localeCompare(b.label));

  return {
    topics,
    entities,
    chunks,
    chunkToTopic,
    chunkToEntities,
    topicToChunks,
    entityToChunks,
  };
}

interface EdgeAcc {
  chunkToTopic: Map<string, string | null>;
  chunkToEntities: Map<string, Set<string>>;
  topicToChunks: Map<string, Set<string>>;
  entityToChunks: Map<string, Set<string>>;
  chunks: LinkedChunk[];
}

function applyEdge(edge: ApiGraphEdge, acc: EdgeAcc): void {
  if (edge.kind === "belongs_to") {
    const chunkId = edge.source_id;
    const topicId = edge.target_id;
    if (acc.chunkToTopic.has(chunkId)) {
      acc.chunkToTopic.set(chunkId, topicId);
    }
    const set = acc.topicToChunks.get(topicId);
    if (set) set.add(chunkId);
  } else if (edge.kind === "has_entity") {
    // The graph emits the edge from the chunk side per ADR-019.
    const chunkId = edge.source_id;
    const entityId = edge.target_id;
    const ents = acc.chunkToEntities.get(chunkId);
    if (ents) ents.add(entityId);
    const set = acc.entityToChunks.get(entityId);
    if (set) set.add(chunkId);
  }
  // `has_chunk` (section → chunk) and the rest don't drive the Linked
  // View cross-highlight; ignore for now.
}

function pickStringProp(
  node: ApiGraphNode,
  ...keys: string[]
): string | null {
  for (const k of keys) {
    const v = node.properties?.[k];
    if (typeof v === "string" && v.length > 0) return v;
  }
  return null;
}

function pickNumberProp(
  node: ApiGraphNode,
  ...keys: string[]
): number | null {
  for (const k of keys) {
    const v = node.properties?.[k];
    if (typeof v === "number") return v;
  }
  return null;
}

function pickStringArrayProp(
  node: ApiGraphNode,
  ...keys: string[]
): string[] {
  for (const k of keys) {
    const v = node.properties?.[k];
    if (Array.isArray(v)) return v.filter((x): x is string => typeof x === "string");
  }
  return [];
}

function buildChunk(node: ApiGraphNode): LinkedChunk {
  const text =
    pickStringProp(node, "text", "snippet", "content") ?? node.label ?? "";
  const page = pickNumberProp(node, "page", "page_number");
  return { id: node.id, text, page, topicId: null, entityIds: [] };
}

function buildTopic(node: ApiGraphNode): LinkedTopic {
  return {
    id: node.id,
    label: node.label,
    keywords: pickStringArrayProp(node, "keywords"),
    chunkIds: [],
  };
}

function buildEntity(node: ApiGraphNode): LinkedEntity {
  return {
    id: node.id,
    label: node.label,
    type: pickStringProp(node, "type", "kind") ?? "entity",
    chunkIds: [],
  };
}

export function useLinkedObjects(
  documentId: string | null | undefined,
): UseLinkedObjectsResult {
  const [state, setState] = useState<{
    status: LinkedObjectsStatus;
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
          payload.nodes.length === 0
            ? ("empty" as const)
            : ("ok" as const);
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

  const data = useMemo(() => projectGraph(state.payload), [state.payload]);

  return {
    status: state.status,
    data,
    error: state.error,
    refetch: () => setTick((n) => n + 1),
  };
}
