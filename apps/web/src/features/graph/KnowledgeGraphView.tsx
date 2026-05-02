/**
 * KnowledgeGraphView — renders the document-scoped knowledge graph.
 *
 * Wraps `@neo4j-nvl/react`'s <InteractiveNvlWrapper>. This file is the
 * ONLY module that imports from `@neo4j-nvl/react`; consumers should go
 * through the barrel in `./index.ts` so the dep stays contained.
 *
 * The panel must be readable in five distinct states:
 *
 *   1. Loading                — fetch in flight.
 *   2. Pre-validation         — document is uploaded but not yet VALIDATED;
 *                               the projection has not been computed yet.
 *   3. Knowledge layer off    — operator has not enabled
 *                               KW_KNOWLEDGE_LAYER_ENABLED; backend returns
 *                               an empty projection for every validated doc.
 *   4. Backend / Neo4j down   — fetch failed (5xx, network); show a retry
 *                               button instead of crashing the workspace.
 *   5. Loaded with data       — the existing NVL canvas.
 *
 * Distinguishing 2 vs 3 isn't possible from the wire payload alone — both
 * return `{ nodes: [], edges: [] }` — so the parent passes
 * `documentStatus` and we branch on it. See issue #133.
 *
 * Refresh seam: `refreshKey` is bumped by the parent after a mutation
 * (validate, edit, …) lands. Changes to it re-issue the fetch; an in-flight
 * request from a previous `refreshKey` is dropped via the cancel flag.
 *
 * v0.2 schema: this view accepts both v0.1 and v0.2 payloads. The
 * widened ``GraphNodeKindV02`` / ``GraphEdgeKindV02`` enums (see
 * ``./types.ts``) drive the legend, color map, and edge styling. New
 * v0.2 kinds (``chunk``, ``topic``, ``has_chunk``, ``belongs_to``,
 * ``related_to``, ``shares_keyword``, ``same_topic_as``,
 * ``has_version``) render with placeholder colors / strokes — refining
 * the visual treatment is part of #150 and #151.
 *
 * Mock seam: until #144 plumbs chunks/topics through the live API,
 * callers can pass ``mockData`` to render the demo fixture without
 * hitting the network. The live data flow is unchanged when the prop
 * is absent.
 */
import { useEffect, useMemo, useState } from "react";
import { InteractiveNvlWrapper } from "@neo4j-nvl/react";

import { ApiError, getDocumentGraph } from "../../api/client";
import type {
  ApiKnowledgeGraphProjection,
  DocumentVersionStatus,
} from "../../api/types";
import type {
  GraphEdgeKindV02,
  GraphEdgeV02,
  GraphNodeKindV02,
  GraphNodeV02,
  KnowledgeGraphProjectionV02,
} from "./types";
import { asGraphEdgeV02, asGraphNodeV02 } from "./types";

// ─── Color palette ──────────────────────────────────────────────────────────
//
// Mirrors all six v0.2 ``GraphNodeKindV02`` values. The first four hex
// values pull from the existing palette in styles.css; ``chunk`` and
// ``topic`` are the placeholder colors agreed in the lane handshake
// (teal, purple) — refining them is #150 / #151's polish.
const NODE_KIND_COLORS: Record<GraphNodeKindV02, string> = {
  document: "#1867c9", // --action
  version: "#0f4f9e", // --action-strong
  section: "#147a45", // --success
  chunk: "#2d8c8a", // placeholder teal
  topic: "#7a4ec4", // placeholder purple
  entity: "#9a6400", // --warning
};

const NODE_KIND_LABELS: Record<GraphNodeKindV02, string> = {
  document: "Document",
  version: "Version",
  section: "Section",
  chunk: "Chunk",
  topic: "Topic",
  entity: "Entity",
};

// ─── Edge styling ───────────────────────────────────────────────────────────
//
// Each of the eight v0.2 ``GraphEdgeKindV02`` values gets a distinct
// caption. Structural edges (``part_of``, ``has_version``,
// ``has_chunk``) keep neutral grey; topic membership uses the topic
// purple tint; semantic chunk relations take warmer hues so they read
// as the "interesting" connections in the demo.
//
// NVL takes a single CSS color per relationship — no native dashed
// strokes — so we encode the visual delta in the caption + color
// pair. Refining stroke styles is part of #150 / #151.
interface EdgeStyle {
  color: string;
  /** Caption shown next to the edge — keep short, the canvas is dense. */
  caption: string;
}

const EDGE_KIND_STYLES: Record<GraphEdgeKindV02, EdgeStyle> = {
  part_of: { color: "#94a3b8", caption: "part of" },
  has_entity: { color: "#9a6400", caption: "has entity" },
  has_version: { color: "#94a3b8", caption: "has version" },
  has_chunk: { color: "#94a3b8", caption: "has chunk" },
  belongs_to: { color: "#7a4ec4", caption: "belongs to" },
  related_to: { color: "#c2410c", caption: "related to" },
  shares_keyword: { color: "#c2410c", caption: "shares keyword" },
  same_topic_as: { color: "#7a4ec4", caption: "same topic" },
};

// ─── NVL adapter ────────────────────────────────────────────────────────────

interface NvlNode {
  id: string;
  captions: { value: string }[];
  color: string;
}

interface NvlRelationship {
  id: string;
  from: string;
  to: string;
  captions: { value: string }[];
  color: string;
}

/** Default fallback color for any future node kind we don't know yet. */
const UNKNOWN_NODE_COLOR = "#627085";
/** Default fallback color for any future edge kind we don't know yet. */
const UNKNOWN_EDGE_COLOR = "#94a3b8";

function toNvlNodes(nodes: readonly GraphNodeV02[]): NvlNode[] {
  return nodes.map((node) => ({
    id: node.id,
    captions: [{ value: node.label }],
    // Index access via a runtime string is intentional: a v0.2 server
    // is the source of truth, but a yet-to-be-known v0.3 kind must
    // still render rather than crash the workspace.
    color: NODE_KIND_COLORS[node.kind] ?? UNKNOWN_NODE_COLOR,
  }));
}

function toNvlRelationships(edges: readonly GraphEdgeV02[]): NvlRelationship[] {
  return edges.map((edge) => {
    const style = EDGE_KIND_STYLES[edge.kind];
    return {
      id: edge.id,
      from: edge.source_id,
      to: edge.target_id,
      captions: [{ value: style?.caption ?? edge.kind }],
      color: style?.color ?? UNKNOWN_EDGE_COLOR,
    };
  });
}

// ─── Component ──────────────────────────────────────────────────────────────

interface KnowledgeGraphViewProps {
  documentId: string | null;
  /**
   * Status of the document version the parent is currently displaying.
   * Used to disambiguate "knowledge layer disabled" from "not validated yet"
   * when the backend returns an empty projection. Optional so the component
   * keeps working before the parent (#129) wires the prop through.
   */
  documentStatus?: DocumentVersionStatus | null;
  /**
   * Bumped by the parent after a mutation lands (validate, edit, …) so the
   * panel re-fetches without remounting. Optional; absent → never refreshes
   * unless `documentId` changes. See issue #129 for the wider plumbing.
   */
  refreshKey?: number;
  /**
   * Demo / Storybook seam: if provided, the view skips the live fetch
   * and renders this projection directly. Used by the chunk/topic
   * demo today — the live API path will replace it once #144 ships.
   *
   * When ``mockData`` is set, ``documentId`` is still required for
   * the empty-state branch (``documentId === null``) so the parent
   * can clear the panel; everything else is read from ``mockData``.
   */
  mockData?: KnowledgeGraphProjectionV02 | null;
}

/**
 * Normalize a payload coming from either the live API (v0.1 typed) or
 * the mock seam (v0.2 typed) to the v0.2 widened shape used internally.
 *
 * The ``as readonly any[]`` step exists to thread the union of
 * heterogeneous array types (``ApiGraphNode[] | GraphNodeV02[]``)
 * through ``.map`` without TypeScript's union-of-array narrowing
 * tripping over the callback signature.
 */
function widenProjection(
  projection: ApiKnowledgeGraphProjection | KnowledgeGraphProjectionV02,
): KnowledgeGraphProjectionV02 {
  const nodes = (projection.nodes as ReadonlyArray<Parameters<typeof asGraphNodeV02>[0]>).map(
    asGraphNodeV02,
  );
  const edges = (projection.edges as ReadonlyArray<Parameters<typeof asGraphEdgeV02>[0]>).map(
    asGraphEdgeV02,
  );
  return {
    document_id: projection.document_id,
    version_id: projection.version_id,
    // The generated v0.1 schema reports ``schema_version: "v0.1"``; the
    // mock fixture reports ``"v0.2"``. The widened union here is the
    // forward-compatible literal.
    schema_version: projection.schema_version,
    generated_at: projection.generated_at,
    nodes,
    edges,
  };
}

export default function KnowledgeGraphView({
  documentId,
  documentStatus = null,
  refreshKey = 0,
  mockData = null,
}: KnowledgeGraphViewProps) {
  const [projection, setProjection] = useState<KnowledgeGraphProjectionV02 | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Local counter used solely to re-issue the fetch when the user clicks
  // "Retry". Kept separate from `refreshKey` so a manual retry doesn't
  // require the parent to bump its own counter.
  const [retryAttempt, setRetryAttempt] = useState(0);

  useEffect(() => {
    if (documentId === null) {
      setProjection(null);
      setLoading(false);
      setError(null);
      return;
    }

    // Mock seam: skip the live fetch entirely. The mock fixture is
    // already in v0.2 shape, so widening is a no-op.
    if (mockData) {
      setProjection(widenProjection(mockData));
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    setProjection(null);

    getDocumentGraph(documentId)
      .then((data) => {
        if (!cancelled) setProjection(widenProjection(data));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // 404 means "no projection yet" — treat as empty rather than error.
        if (err instanceof ApiError && err.status === 404) {
          setProjection(null);
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load graph.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // `refreshKey` (parent-driven) and `retryAttempt` (local) both re-issue
    // the fetch; whichever changes last wins, and the cancel flag drops
    // any in-flight result from the previous attempt.
  }, [documentId, refreshKey, retryAttempt, mockData]);

  const nvlNodes = useMemo(
    () => (projection ? toNvlNodes(projection.nodes) : []),
    [projection],
  );
  const nvlRels = useMemo(
    () => (projection ? toNvlRelationships(projection.edges) : []),
    [projection],
  );

  const isEmptyPayload = projection === null || nvlNodes.length === 0;
  // `documentStatus === null` means the parent hasn't passed it yet — fall
  // back to the legacy generic empty-state message in that case.
  const isPreValidation =
    documentStatus !== null && documentStatus !== "VALIDATED";

  return (
    <article className="panel graph-panel" aria-labelledby="graph-panel-title">
      <div className="panel-heading">
        <h3 id="graph-panel-title">Knowledge graph</h3>
        <GraphLegend />
      </div>

      {documentId === null ? (
        <p className="muted" role="status">
          Select a document to view its knowledge graph.
        </p>
      ) : loading ? (
        <p className="muted" role="status" aria-live="polite">
          Loading graph…
        </p>
      ) : error !== null ? (
        <div className="notice danger" role="alert">
          <strong>Couldn&apos;t load the knowledge graph</strong>
          <span>{error}</span>
          <button
            type="button"
            className="button"
            onClick={() => setRetryAttempt((n) => n + 1)}
          >
            Retry
          </button>
        </div>
      ) : isEmptyPayload && isPreValidation ? (
        <p className="muted" role="status">
          The knowledge graph is generated after a reviewer validates this
          document. Validate the document to see its projection here.
        </p>
      ) : isEmptyPayload && documentStatus === "VALIDATED" ? (
        <div className="notice info" role="status">
          <strong>Knowledge graph is an optional add-on</strong>
          <span>
            Enable it by starting the API with{" "}
            <code>KW_KNOWLEDGE_LAYER_ENABLED=true</code> and a Neo4j
            instance — see the Knowledge Layer wiki.
          </span>
        </div>
      ) : isEmptyPayload ? (
        <p className="muted">
          No knowledge graph projection has been generated for this document yet.
        </p>
      ) : (
        <div
          className="graph-canvas"
          data-testid="knowledge-graph-canvas"
          style={{ height: 480, width: "100%" }}
        >
          <InteractiveNvlWrapper
            nodes={nvlNodes}
            rels={nvlRels}
            nvlOptions={{ initialZoom: 0.75, allowDynamicMinZoom: true }}
          />
          {/* NB: `@neo4j-nvl/react`'s <InteractiveNvlWrapper> calls the
              relationship prop `rels` (NVL terminology). Adapter shape
              matches the wider {id, from, to, captions} contract. */}
        </div>
      )}
    </article>
  );
}

// ─── Legend ─────────────────────────────────────────────────────────────────

function GraphLegend() {
  return (
    <ul className="graph-legend" aria-label="Node kind legend">
      {(Object.keys(NODE_KIND_COLORS) as GraphNodeKindV02[]).map((kind) => (
        <li key={kind}>
          <span
            className="graph-legend-swatch"
            style={{ background: NODE_KIND_COLORS[kind] }}
            aria-hidden="true"
          />
          <span>{NODE_KIND_LABELS[kind]}</span>
        </li>
      ))}
    </ul>
  );
}
