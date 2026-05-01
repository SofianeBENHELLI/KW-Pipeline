/**
 * KnowledgeGraphView — renders the document-scoped knowledge graph.
 *
 * Wraps `@neo4j-nvl/react`'s <InteractiveNvlWrapper>. This file is the
 * ONLY module that imports from `@neo4j-nvl/react`; consumers should go
 * through the barrel in `./index.ts` so the dep stays contained.
 *
 * Lifecycle:
 *   - documentId === null  → empty-state message, no fetch.
 *   - documentId changes   → fetch GET /documents/{id}/graph, replace state.
 *   - in-flight requests for stale documentIds are dropped via a cancel flag.
 */
import { useEffect, useMemo, useState } from "react";
import { InteractiveNvlWrapper } from "@neo4j-nvl/react";

import { ApiError, getDocumentGraph } from "../../api/client";
import type {
  ApiGraphEdge,
  ApiGraphNode,
  ApiKnowledgeGraphProjection,
} from "../../api/types";

// ─── Color palette (mirrors the kinds enum on GraphNode) ─────────────────────
//
// Keep these in sync with `KnowledgeGraphProjection` from the backend. The
// hex values pull from the existing palette in styles.css so the legend
// fits the rest of the workspace visually.
const NODE_KIND_COLORS: Record<ApiGraphNode["kind"], string> = {
  document: "#1867c9",  // --action
  version: "#0f4f9e",   // --action-strong
  section: "#147a45",   // --success
  entity: "#9a6400",    // --warning
};

const NODE_KIND_LABELS: Record<ApiGraphNode["kind"], string> = {
  document: "Document",
  version: "Version",
  section: "Section",
  entity: "Entity",
};

// ─── NVL adapter ─────────────────────────────────────────────────────────────

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
}

function toNvlNodes(nodes: ApiGraphNode[]): NvlNode[] {
  return nodes.map((node) => ({
    id: node.id,
    captions: [{ value: node.label }],
    color: NODE_KIND_COLORS[node.kind] ?? "#627085",
  }));
}

function toNvlRelationships(edges: ApiGraphEdge[]): NvlRelationship[] {
  return edges.map((edge) => ({
    id: edge.id,
    from: edge.source_id,
    to: edge.target_id,
    captions: [{ value: edge.kind }],
  }));
}

// ─── Component ───────────────────────────────────────────────────────────────

interface KnowledgeGraphViewProps {
  documentId: string | null;
}

export default function KnowledgeGraphView({ documentId }: KnowledgeGraphViewProps) {
  const [projection, setProjection] = useState<ApiKnowledgeGraphProjection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (documentId === null) {
      setProjection(null);
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
        if (!cancelled) setProjection(data);
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
  }, [documentId]);

  const nvlNodes = useMemo(
    () => (projection ? toNvlNodes(projection.nodes) : []),
    [projection],
  );
  const nvlRels = useMemo(
    () => (projection ? toNvlRelationships(projection.edges) : []),
    [projection],
  );

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
          <strong>Failed to load graph</strong>
          <span>{error}</span>
        </div>
      ) : projection === null || nvlNodes.length === 0 ? (
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

// ─── Legend ──────────────────────────────────────────────────────────────────

function GraphLegend() {
  return (
    <ul className="graph-legend" aria-label="Node kind legend">
      {(Object.keys(NODE_KIND_COLORS) as ApiGraphNode["kind"][]).map((kind) => (
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
