/**
 * Knowledge-graph v0.2 typed views.
 *
 * The wire contract uses a flat ``properties: dict[str, scalar]`` shape
 * (see ``apps/api/app/schemas/knowledge.py`` and
 * ``docs/architecture/knowledge_graph_payload.md``). The generated TS
 * schema in ``apps/web/src/api/generated/schema.ts`` reflects that flat
 * shape verbatim — so it does not, by itself, surface the *typed*
 * properties that v0.2 promises (chunk indices, topic keywords, edge
 * weights …).
 *
 * This file mirrors the v0.2 property tables as TypeScript interfaces
 * and provides thin helpers that turn a generic ``ApiGraphNode`` /
 * ``ApiGraphEdge`` into a typed view when the discriminating ``kind``
 * matches. The mirrors are **structural** assertions — there is no
 * runtime validation here, and we do not bring in zod/io-ts/yup just
 * for this. The server is the source of truth; if it breaks the
 * contract, types will be wrong and downstream code will need to
 * defend with optional chaining.
 *
 * Only Lane D (Orbital frontend) consumes this file. Lane A (API
 * schema) updates ``knowledge.py`` and the generated schema, then
 * Lane D re-aligns the typed mirrors below.
 *
 * NOTE on kind widening: until #140 / Wave 2 lands, the generated
 * schema still reports ``kind: "document" | "version" | "section" |
 * "entity"`` for nodes and ``"part_of" | "has_entity"`` for edges. We
 * widen those locally to the full v0.2 enum so the rest of the
 * frontend (component code, mock fixtures, tests) can be written
 * against the final shape today. The runtime payload from a v0.1
 * backend remains a strict subset of the v0.2 enum, so widening is
 * additive and backwards-compatible.
 */

import type { ApiGraphEdge, ApiGraphNode } from "../../api/types";

// ─── Kind enums (v0.2) ──────────────────────────────────────────────────────

/**
 * All node kinds the frontend can render.
 *
 * Six total: the four v0.1 kinds plus ``chunk`` and ``topic`` from
 * #140. Any ``Record<GraphNodeKindV02, …>`` literal must enumerate all
 * six — the compiler will yell otherwise, which is the whole point.
 */
export type GraphNodeKindV02 =
  | "document"
  | "version"
  | "section"
  | "chunk"
  | "topic"
  | "entity";

/**
 * All edge kinds the frontend can render.
 *
 * Eight total: the two v0.1 kinds plus the six v0.2 additions from
 * #140 (``has_version``, ``has_chunk``, ``belongs_to``, ``related_to``,
 * ``shares_keyword``, ``same_topic_as``).
 */
export type GraphEdgeKindV02 =
  | "part_of"
  | "has_entity"
  | "has_version"
  | "has_chunk"
  | "belongs_to"
  | "related_to"
  | "shares_keyword"
  | "same_topic_as";

// ─── Widened wire types ─────────────────────────────────────────────────────

/**
 * v0.2-aware node — same flat shape as ``ApiGraphNode`` but with the
 * full kind enum. Read from a payload by casting through
 * ``asGraphNodeV02``.
 */
export interface GraphNodeV02 {
  id: string;
  kind: GraphNodeKindV02;
  label: string;
  properties: Record<string, string | number | boolean | string[] | null>;
}

/**
 * v0.2-aware edge — same flat shape as ``ApiGraphEdge`` but with the
 * full kind enum. The properties dict allows ``string[]`` so edge
 * properties like ``shared_keywords: string[]`` (for
 * ``shares_keyword`` edges) typecheck.
 */
export interface GraphEdgeV02 {
  id: string;
  kind: GraphEdgeKindV02;
  source_id: string;
  target_id: string;
  properties: Record<string, string | number | boolean | string[] | null>;
}

/**
 * v0.2 projection — accepts both the v0.1 ``schema_version`` literal
 * and the v0.2 one until the backend bumps. The frontend treats both
 * payload shapes identically; new kinds simply do not appear in v0.1
 * payloads.
 */
export interface KnowledgeGraphProjectionV02 {
  document_id: string;
  version_id: string;
  schema_version: "v0.1" | "v0.2";
  generated_at: string;
  nodes: GraphNodeV02[];
  edges: GraphEdgeV02[];
}

// ─── Typed property mirrors (v0.2) ──────────────────────────────────────────

/**
 * Properties carried on a ``chunk`` node.
 *
 * Mirrors the v0.2 contract table for chunk nodes. ``index`` is the
 * 0-based position within the parent ``section`` (or document, when
 * sectioning is unavailable); ``token_count`` is the post-tokeniser
 * length; ``keywords`` is the optional shortlist used by
 * ``shares_keyword`` edges.
 */
export interface ChunkNodeProperties {
  /** 0-based position of the chunk inside its parent. */
  index: number;
  /** Token count for the chunk body (post-tokeniser). */
  token_count: number;
  /** ID of the parent section, when chunking is section-scoped. */
  section_id?: string | null;
  /** Optional keyword shortlist driving ``shares_keyword`` edges. */
  keywords?: string[];
}

/**
 * Properties carried on a ``topic`` node.
 *
 * Mirrors the v0.2 contract table. A topic groups a set of chunks
 * (via ``belongs_to``) under a discovered theme; ``keywords`` is the
 * representative term list, ``size`` is the chunk count, ``score``
 * is the topic-model coherence score in ``[0, 1]``.
 */
export interface TopicNodeProperties {
  /** Representative keywords for the topic. */
  keywords: string[];
  /** Number of chunks attached to the topic. */
  size: number;
  /** Coherence / quality score in ``[0, 1]`` (optional). */
  score?: number | null;
}

/**
 * Properties carried on chunk-relation edges (``related_to``,
 * ``shares_keyword``, ``same_topic_as``).
 *
 * ``weight`` is the edge strength in ``[0, 1]`` (similarity score,
 * keyword overlap ratio …); ``shared_keywords`` is populated on
 * ``shares_keyword`` edges; ``topic_id`` is populated on
 * ``same_topic_as`` edges.
 */
export interface ChunkRelationEdgeProperties {
  weight?: number;
  shared_keywords?: string[];
  topic_id?: string | null;
}

/**
 * Properties carried on ``belongs_to`` edges (chunk → topic).
 *
 * ``score`` is the membership probability in ``[0, 1]``; on hard
 * clustering it is ``1.0`` and may be omitted.
 */
export interface TopicMembershipEdgeProperties {
  score?: number;
}

/**
 * Properties carried on structural edges (``part_of``, ``has_version``,
 * ``has_chunk``).
 *
 * Currently empty — the v0.2 contract reserves the type for forward
 * compatibility. Defined explicitly so component code can still ask
 * for ``asStructuralEdgeProperties`` without special-casing.
 */
export interface StructuralEdgeProperties {
  // Reserved for future fields per the contract table.
  [key: string]: string | number | boolean | string[] | null | undefined;
}

// ─── Helpers ────────────────────────────────────────────────────────────────

/**
 * Generic node — accepts either the generated v0.1 ``ApiGraphNode``
 * or the local v0.2 ``GraphNodeV02``. Helpers below take this so they
 * can be called against either source without juggling overloads.
 */
export type AnyGraphNode = ApiGraphNode | GraphNodeV02;

/**
 * Generic edge — same idea as ``AnyGraphNode``.
 */
export type AnyGraphEdge = ApiGraphEdge | GraphEdgeV02;

/**
 * Widen a generic node to the v0.2 enum.
 *
 * Pure structural cast — the runtime shape is identical; only the
 * type-level kind enum is broader. Useful when writing component code
 * that needs to switch over the full v0.2 enum but reads from a
 * generated v0.1 ``ApiGraphNode``.
 */
export function asGraphNodeV02(node: AnyGraphNode): GraphNodeV02 {
  // The generated `properties` dict only allows scalar values; the
  // v0.2 widening additionally allows `string[]`. Casting through
  // `unknown` keeps strict mode happy.
  return node as unknown as GraphNodeV02;
}

/**
 * Widen a generic edge to the v0.2 enum. Same notes as
 * ``asGraphNodeV02``.
 */
export function asGraphEdgeV02(edge: AnyGraphEdge): GraphEdgeV02 {
  return edge as unknown as GraphEdgeV02;
}

/**
 * Return a typed view of a chunk node, or ``undefined`` if the kind
 * doesn't match.
 *
 * Intended use:
 *
 * ```ts
 * const props = asChunkNodeProperties(node);
 * if (props) console.log(props.index, props.token_count);
 * ```
 *
 * The returned object is the same dict the server sent — we do not
 * deep-clone, and we do not validate. If the server breaks the
 * contract, properties will be ``undefined`` at the call site; treat
 * the server as the source of truth.
 */
export function asChunkNodeProperties(
  node: AnyGraphNode,
): ChunkNodeProperties | undefined {
  if (node.kind !== "chunk") return undefined;
  return node.properties as unknown as ChunkNodeProperties;
}

/**
 * Return a typed view of a topic node, or ``undefined`` if the kind
 * doesn't match. See :func:`asChunkNodeProperties` for usage notes.
 */
export function asTopicNodeProperties(
  node: AnyGraphNode,
): TopicNodeProperties | undefined {
  if (node.kind !== "topic") return undefined;
  return node.properties as unknown as TopicNodeProperties;
}

/**
 * Return a typed view of a chunk-to-chunk relation edge, or
 * ``undefined`` if the edge kind isn't one of ``related_to``,
 * ``shares_keyword``, ``same_topic_as``.
 */
export function asChunkRelationEdgeProperties(
  edge: AnyGraphEdge,
): ChunkRelationEdgeProperties | undefined {
  if (
    edge.kind !== "related_to" &&
    edge.kind !== "shares_keyword" &&
    edge.kind !== "same_topic_as"
  ) {
    return undefined;
  }
  return edge.properties as unknown as ChunkRelationEdgeProperties;
}

/**
 * Return a typed view of a topic-membership edge, or ``undefined``
 * if the edge kind isn't ``belongs_to``.
 */
export function asTopicMembershipEdgeProperties(
  edge: AnyGraphEdge,
): TopicMembershipEdgeProperties | undefined {
  if (edge.kind !== "belongs_to") return undefined;
  return edge.properties as unknown as TopicMembershipEdgeProperties;
}

/**
 * Return a typed view of a structural edge, or ``undefined`` if the
 * edge kind isn't one of ``part_of``, ``has_version``, ``has_chunk``.
 */
export function asStructuralEdgeProperties(
  edge: AnyGraphEdge,
): StructuralEdgeProperties | undefined {
  if (
    edge.kind !== "part_of" &&
    edge.kind !== "has_version" &&
    edge.kind !== "has_chunk"
  ) {
    return undefined;
  }
  return edge.properties as unknown as StructuralEdgeProperties;
}
