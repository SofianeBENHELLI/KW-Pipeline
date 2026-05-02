/**
 * Shared graph-feature types and pure helpers (#164).
 *
 * Anything *not* tied to React rendering or NVL canvas state lives
 * here so future graph consumers — e.g. a chat surface that walks
 * the v0.2 payload, or the #78 widget compact mode — can reuse the
 * palette, filter machinery, and NVL adapter without pulling in
 * `KnowledgeGraphView.tsx` (which imports `@neo4j-nvl/react`).
 *
 * The wire-contract types themselves (`ApiGraphNode`, `ApiGraphEdge`,
 * `ApiKnowledgeGraphProjection`) come from the OpenAPI-generated
 * `api/types.ts` re-exports — never duplicate them here.
 */
import type { ApiGraphEdge, ApiGraphNode } from "../../api/types";

// ─── Color palette (mirrors the kinds enum on GraphNode) ─────────────────────
//
// Keep these in sync with `KnowledgeGraphProjection` from the backend. The
// hex values pull from the existing palette in styles.css so the legend
// fits the rest of the workspace visually.
export const NODE_KIND_COLORS: Record<ApiGraphNode["kind"], string> = {
  document: "#1867c9", // --action
  version: "#0f4f9e", // --action-strong
  section: "#147a45", // --success (legacy v0.1 — kept for back-compat)
  chunk: "#2d8c8a", // teal
  topic: "#7a4ec4", // purple
  entity: "#9a6400", // --warning
};

export const NODE_KIND_LABELS: Record<ApiGraphNode["kind"], string> = {
  document: "Document",
  version: "Version",
  section: "Section",
  chunk: "Chunk",
  topic: "Topic",
  entity: "Entity",
};

export const EDGE_KIND_LABELS: Record<ApiGraphEdge["kind"], string> = {
  part_of: "part of",
  has_version: "has version",
  has_chunk: "has chunk",
  belongs_to: "belongs to",
  related_to: "related to",
  shares_keyword: "shares keyword",
  same_topic_as: "same topic",
  has_entity: "has entity",
};

// Edge kinds that count as "deterministic chunk-to-chunk semantic
// relations" — surfaced by the `Relations` filter (#150) and by the
// inspector's "shared_keywords" rendering (#151).
export const SEMANTIC_RELATION_KINDS: ReadonlySet<ApiGraphEdge["kind"]> = new Set([
  "related_to",
  "shares_keyword",
  "same_topic_as",
]);

// ─── Filter modes ────────────────────────────────────────────────────────────

export type FilterMode =
  | "all"
  | "chunks"
  | "topics"
  | "entities"
  | "relations"
  | "source-backed";

export const FILTER_OPTIONS: { id: FilterMode; label: string }[] = [
  { id: "all", label: "All" },
  { id: "chunks", label: "Chunks" },
  { id: "topics", label: "Topics" },
  { id: "entities", label: "Entities" },
  { id: "relations", label: "Relations" },
  { id: "source-backed", label: "Source-backed" },
];

export interface FilteredProjection {
  nodes: ApiGraphNode[];
  edges: ApiGraphEdge[];
}

/**
 * Filter the projection to the subset relevant for ``mode``. The
 * function never mutates its input and tolerates partial/empty
 * payloads (acceptance criterion for #150 — the filter must not
 * crash on an empty graph).
 *
 * Filter semantics:
 *
 *   * ``all`` — identity.
 *   * ``chunks`` — chunk + topic nodes (so chunks remain attached to
 *     their topic context) + structural edges between them.
 *   * ``topics`` — topic nodes + the chunks that belong to them +
 *     ``belongs_to`` edges.
 *   * ``entities`` — entity nodes + ``has_entity`` edges.
 *   * ``relations`` — chunks + the deterministic chunk-to-chunk
 *     semantic edges between them.
 *   * ``source-backed`` — anything with a ``source_reference_id`` /
 *     non-zero ``source_reference_count`` property. Mirrors
 *     ADR-009's needs-review gate at the visualisation layer.
 */
export function filterProjection(
  projection: FilteredProjection,
  mode: FilterMode,
): FilteredProjection {
  const { nodes, edges } = projection;
  if (mode === "all") return { nodes, edges };

  let kept: ApiGraphNode[];
  if (mode === "chunks") {
    kept = nodes.filter((n) => n.kind === "chunk" || n.kind === "topic");
  } else if (mode === "topics") {
    const topicChunkIds = new Set<string>();
    for (const node of nodes) {
      if (node.kind === "topic") {
        const ids = node.properties["chunk_ids"];
        if (Array.isArray(ids)) for (const id of ids) topicChunkIds.add(id);
      }
    }
    kept = nodes.filter(
      (n) => n.kind === "topic" || (n.kind === "chunk" && topicChunkIds.has(n.id)),
    );
  } else if (mode === "entities") {
    kept = nodes.filter((n) => n.kind === "entity");
  } else if (mode === "relations") {
    kept = nodes.filter((n) => n.kind === "chunk");
  } else {
    // source-backed: nodes with a non-empty source_reference_count
    // property (chunks today; sections in legacy v0.1).
    kept = nodes.filter((n) => isSourceBacked(n));
  }

  const keptIds = new Set(kept.map((n) => n.id));
  let keptEdges = edges.filter(
    (e) => keptIds.has(e.source_id) && keptIds.has(e.target_id),
  );

  if (mode === "relations") {
    keptEdges = keptEdges.filter((e) => SEMANTIC_RELATION_KINDS.has(e.kind));
  } else if (mode === "topics") {
    keptEdges = keptEdges.filter((e) => e.kind === "belongs_to");
  } else if (mode === "entities") {
    keptEdges = keptEdges.filter((e) => e.kind === "has_entity");
  } else if (mode === "source-backed") {
    keptEdges = keptEdges.filter((e) => isSourceBackedEdge(e));
  }

  return { nodes: kept, edges: keptEdges };
}

export function isSourceBacked(node: ApiGraphNode): boolean {
  const count = node.properties["source_reference_count"];
  return typeof count === "number" && count > 0;
}

export function isSourceBackedEdge(edge: ApiGraphEdge): boolean {
  return (
    typeof edge.properties["source_reference_id"] === "string" &&
    edge.properties["source_reference_id"].length > 0
  );
}

// ─── NVL adapter ─────────────────────────────────────────────────────────────

export interface NvlNode {
  id: string;
  captions: { value: string }[];
  color: string;
}

export interface NvlRelationship {
  id: string;
  from: string;
  to: string;
  captions: { value: string }[];
}

export function toNvlNodes(nodes: ApiGraphNode[]): NvlNode[] {
  return nodes.map((node) => ({
    id: node.id,
    captions: [{ value: node.label }],
    color: NODE_KIND_COLORS[node.kind] ?? "#627085",
  }));
}

export function toNvlRelationships(edges: ApiGraphEdge[]): NvlRelationship[] {
  return edges.map((edge) => ({
    id: edge.id,
    from: edge.source_id,
    to: edge.target_id,
    captions: [{ value: EDGE_KIND_LABELS[edge.kind] ?? edge.kind }],
  }));
}
