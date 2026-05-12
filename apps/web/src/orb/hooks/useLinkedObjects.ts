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
  /**
   * Stable id of the section this chunk belongs to. Used by the
   * document viewer to group chunks under their section heading
   * instead of rendering one big undifferentiated stream of spans.
   */
  sectionId: string | null;
  /** Section heading the backend captured on this chunk. */
  sectionHeading: string | null;
  topicId: string | null;
  entityIds: string[];
}

/**
 * One section in the document viewer's left-pane structure. Sections
 * come from grouping chunks by `section_id`; the heading is taken
 * from the chunks' `heading` property, which the backend writes from
 * the parser's section title.
 */
export interface LinkedSection {
  /** Stable id (matches `chunk.sectionId`). */
  id: string;
  /** Heading text. May be null/empty when the parser couldn't extract one. */
  heading: string | null;
  /** Lowest page number this section spans (drives ordering). */
  page: number | null;
  /** Chunk ids in section order. */
  chunkIds: string[];
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
  /** Document sections in order. Drives the doc viewer structure. */
  sections: LinkedSection[];
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
  sections: [],
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

  // Group chunks by section so the document viewer can render
  // sections-with-headings instead of one undifferentiated chunk
  // stream. Chunks without a `section_id` collapse into a synthetic
  // "untitled" section keyed by the empty string — keeps the rendering
  // contract uniform for every doc, even when the parser couldn't
  // identify section breaks.
  const sectionMap = new Map<string, LinkedSection>();
  for (const chunk of chunks) {
    const sectionId = chunk.sectionId ?? "";
    let entry = sectionMap.get(sectionId);
    if (!entry) {
      entry = {
        id: sectionId,
        heading: chunk.sectionHeading,
        page: chunk.page ?? null,
        chunkIds: [],
      };
      sectionMap.set(sectionId, entry);
    }
    entry.chunkIds.push(chunk.id);
    if (
      chunk.page != null &&
      (entry.page == null || chunk.page < entry.page)
    ) {
      entry.page = chunk.page;
    }
    // Prefer the first non-null heading we see for this section id.
    if (!entry.heading && chunk.sectionHeading) {
      entry.heading = chunk.sectionHeading;
    }
  }
  const sections = [...sectionMap.values()].sort((a, b) => {
    const ap = a.page ?? Number.POSITIVE_INFINITY;
    const bp = b.page ?? Number.POSITIVE_INFINITY;
    if (ap !== bp) return ap - bp;
    return (a.heading ?? "").localeCompare(b.heading ?? "");
  });

  return {
    topics,
    entities,
    chunks,
    sections,
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
  // The backend's projector writes `text_preview` on chunk nodes as
  // a 200-char preview of the chunk body. Fall back to the legacy
  // `text` / `snippet` / `content` keys for older projections, then
  // to the node label as a last resort.
  const text =
    pickStringProp(node, "text_preview", "text", "snippet", "content") ??
    node.label ??
    "";
  const page = pickNumberProp(node, "page", "page_number");
  const sectionId = pickStringProp(node, "section_id");
  const sectionHeading = pickStringProp(node, "heading", "section_heading");
  return {
    id: node.id,
    text,
    page,
    sectionId,
    sectionHeading,
    topicId: null,
    entityIds: [],
  };
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
