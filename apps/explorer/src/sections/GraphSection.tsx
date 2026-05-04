/**
 * Graph — full-screen knowledge-graph navigation surface.
 *
 * Two scopes:
 *
 *   * "Document" — fetch `/documents/{id}/graph` and render only the
 *     subgraph projected from the active document. This is the entry
 *     scope when the user navigates here from the Document view.
 *   * "Catalog" — walk `/knowledge/graph` cursor-by-cursor and render
 *     the union of every projection. Useful for finding cross-document
 *     relationships, capped at MAX_PAGES * PAGE_LIMIT to bound the wire.
 *
 * Rendering: a lightweight SVG canvas (`<GraphCanvas>`) lays out nodes
 * by `kind` in concentric rings — no force-directed simulation, no
 * `@neo4j-nvl/react`, no extra dep. The widget is a tile, not a full
 * graph workbench; the inspector below the canvas is where users do
 * the real navigation.
 *
 * The legend, filters, and node/edge inspector mirror the contract in
 * apps/web/src/features/graph/KnowledgeGraphView.tsx so users moving
 * between widgets don't have to relearn the panel.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, getDocumentGraph, getKnowledgeGraph } from "../api/client";
import type {
  GraphEdge,
  GraphNode,
  KnowledgeGraphPage,
  KnowledgeGraphProjection,
} from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/icons";
import { SectionHeader } from "../components/SectionHeader";

type Scope = "document" | "catalog";

interface Props {
  apiBaseUrl: string;
  refreshTick: number;
  /** When set, the section opens scoped to this document. */
  documentId: string | null;
  /** Notify the parent the user wants to open a document by id. */
  onOpenDocument: (documentId: string) => void;
}

const NODE_COLORS: Record<string, string> = {
  Document: "#005686",
  Version: "#1F8FBF",
  Section: "#7A8893",
  Chunk: "#B26A00",
  Topic: "#1F7A4A",
  Entity: "#B3261E",
};

const PAGE_LIMIT = 200;
const MAX_PAGES = 10;

interface Selection {
  kind: "node" | "edge";
  id: string;
}

export const GraphSection: React.FC<Props> = ({
  apiBaseUrl,
  refreshTick,
  documentId,
  onOpenDocument,
}) => {
  const [scope, setScope] = useState<Scope>(documentId !== null ? "document" : "catalog");
  const [projection, setProjection] = useState<KnowledgeGraphProjection | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pagesWalked, setPagesWalked] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [filterKind, setFilterKind] = useState<string | "all">("all");
  const [selection, setSelection] = useState<Selection | null>(null);

  // Switch to "document" scope automatically when a documentId arrives.
  useEffect(() => {
    if (documentId !== null) setScope("document");
  }, [documentId]);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setProjection(null);
    setPagesWalked(0);
    setTruncated(false);
    setSelection(null);

    const run = async () => {
      try {
        if (scope === "document" && documentId !== null) {
          const data = await getDocumentGraph(documentId, {
            baseUrl: apiBaseUrl,
            signal: controller.signal,
          });
          if (cancelled) return;
          setProjection(data);
          setPagesWalked(1);
          return;
        }
        // Catalog scope — walk pages.
        const nodes: GraphNode[] = [];
        const edges: GraphEdge[] = [];
        let cursor: string | null = null;
        let pages = 0;
        let schemaVersion = "v0.2";
        do {
          // eslint-disable-next-line no-await-in-loop -- pagination is sequential by design
          const page: KnowledgeGraphPage = await getKnowledgeGraph({
            limit: PAGE_LIMIT,
            cursor: cursor ?? undefined,
            baseUrl: apiBaseUrl,
            signal: controller.signal,
          });
          nodes.push(...page.nodes);
          edges.push(...page.edges);
          schemaVersion = page.schema_version;
          cursor = page.next_cursor;
          pages += 1;
        } while (cursor && pages < MAX_PAGES);
        if (cancelled) return;
        setProjection({ schema_version: schemaVersion, nodes, edges });
        setPagesWalked(pages);
        setTruncated(cursor !== null);
      } catch (err: unknown) {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError && err.status === 404) {
          setProjection({ schema_version: "v0.2", nodes: [], edges: [] });
          return;
        }
        const message = err instanceof Error ? err.message : "Failed to load graph.";
        setError(message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [apiBaseUrl, refreshTick, scope, documentId]);

  const filteredNodes = useMemo(() => {
    if (!projection) return [];
    if (filterKind === "all") return projection.nodes;
    return projection.nodes.filter((n) => n.kind === filterKind);
  }, [projection, filterKind]);

  const filteredEdges = useMemo(() => {
    if (!projection) return [];
    if (filterKind === "all") return projection.edges;
    const visibleIds = new Set(filteredNodes.map((n) => n.id));
    return projection.edges.filter(
      (e) => visibleIds.has(e.source_id) && visibleIds.has(e.target_id),
    );
  }, [projection, filteredNodes, filterKind]);

  const kinds = useMemo(() => {
    if (!projection) return [];
    const map = new Map<string, number>();
    for (const n of projection.nodes) {
      map.set(n.kind, (map.get(n.kind) ?? 0) + 1);
    }
    return Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
  }, [projection]);

  const selectedNode = useMemo(
    () =>
      selection?.kind === "node"
        ? (filteredNodes.find((n) => n.id === selection.id) ?? null)
        : null,
    [selection, filteredNodes],
  );
  const selectedEdge = useMemo(
    () =>
      selection?.kind === "edge"
        ? (filteredEdges.find((e) => `${e.source_id}-${e.kind}-${e.target_id}` === selection.id) ?? null)
        : null,
    [selection, filteredEdges],
  );

  const onCanvasPickNode = useCallback((id: string) => {
    setSelection({ kind: "node", id });
  }, []);

  const meta = projection
    ? `${filteredNodes.length} nodes · ${filteredEdges.length} edges${truncated ? " (truncated)" : ""}`
    : undefined;

  return (
    <section className="kw-section kx-graph-section" aria-labelledby="graph-section-title">
      <SectionHeader icon="graph" title="Knowledge graph" meta={meta} />
      <h2 id="graph-section-title" className="visually-hidden">
        Knowledge graph
      </h2>

      <div className="kx-graph-toolbar" role="toolbar" aria-label="Graph scope and filters">
        <div className="kx-graph-toolbar__group">
          <button
            type="button"
            className={`kw-btn kw-btn--sm${scope === "document" ? " kw-btn--primary" : ""}`}
            disabled={documentId === null}
            onClick={() => setScope("document")}
            aria-pressed={scope === "document"}
            title={
              documentId === null
                ? "Pick a document first to scope the graph"
                : "Scope to the active document"
            }
          >
            This document
          </button>
          <button
            type="button"
            className={`kw-btn kw-btn--sm${scope === "catalog" ? " kw-btn--primary" : ""}`}
            onClick={() => setScope("catalog")}
            aria-pressed={scope === "catalog"}
          >
            Whole catalog
          </button>
        </div>

        {kinds.length > 0 && (
          <div className="kx-graph-toolbar__group" role="group" aria-label="Filter by node kind">
            <button
              type="button"
              className={`kx-chip${filterKind === "all" ? " kx-chip--active" : ""}`}
              aria-pressed={filterKind === "all"}
              onClick={() => setFilterKind("all")}
            >
              all
              <span className="kx-chip__count">{projection?.nodes.length ?? 0}</span>
            </button>
            {kinds.map(([kind, count]) => (
              <button
                key={kind}
                type="button"
                className={`kx-chip${filterKind === kind ? " kx-chip--active" : ""}`}
                aria-pressed={filterKind === kind}
                onClick={() => setFilterKind(kind)}
              >
                <span
                  className="kx-chip__swatch"
                  style={{ background: NODE_COLORS[kind] ?? "#7A8893" }}
                  aria-hidden="true"
                />
                {kind}
                <span className="kx-chip__count">{count}</span>
              </button>
            ))}
          </div>
        )}

        {pagesWalked > 0 && scope === "catalog" && (
          <span className="kw-mono kw-mono--muted">walked {pagesWalked} pages</span>
        )}
      </div>

      {loading ? (
        <p className="kw-status">Loading graph…</p>
      ) : error !== null ? (
        <p className="kw-error" role="alert">
          {error}
        </p>
      ) : projection === null || projection.nodes.length === 0 ? (
        <EmptyState
          icon="graph"
          title="No knowledge graph projection is available"
          body={
            scope === "document"
              ? "The graph is generated after a reviewer validates the document. Validate it from the ingestion widget to see its projection here."
              : "No documents have been projected into the graph yet — validate a few from the ingestion widget."
          }
        />
      ) : (
        <div className="kx-graph-body">
          <GraphCanvas
            nodes={filteredNodes}
            edges={filteredEdges}
            selection={selection}
            onPickNode={onCanvasPickNode}
          />
          <Inspector
            nodes={filteredNodes}
            edges={filteredEdges}
            selection={selection}
            setSelection={setSelection}
            selectedNode={selectedNode}
            selectedEdge={selectedEdge}
            onOpenDocument={onOpenDocument}
          />
        </div>
      )}
    </section>
  );
};

// ─── SVG canvas ──────────────────────────────────────────────────────────────

interface CanvasProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selection: Selection | null;
  onPickNode: (id: string) => void;
}

const GraphCanvas: React.FC<CanvasProps> = ({ nodes, edges, selection, onPickNode }) => {
  // Concentric-ring layout: group by `kind`, then place each kind on
  // its own ring. Deterministic so re-renders don't reshuffle.
  const layout = useMemo(() => layOut(nodes), [nodes]);

  if (nodes.length === 0) {
    return (
      <div className="kx-graph-canvas" aria-label="Knowledge graph canvas">
        <p className="kw-status">No nodes match the current filter.</p>
      </div>
    );
  }

  return (
    <div className="kx-graph-canvas" data-testid="kx-graph-canvas">
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        className="kx-graph-canvas__svg"
        role="img"
        aria-label="Knowledge graph projection"
      >
        <g>
          {edges.map((edge) => {
            const a = layout.positions.get(edge.source_id);
            const b = layout.positions.get(edge.target_id);
            if (!a || !b) return null;
            return (
              <line
                key={`${edge.source_id}-${edge.kind}-${edge.target_id}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                className="kx-graph-canvas__edge"
              />
            );
          })}
        </g>
        <g>
          {nodes.map((node) => {
            const pos = layout.positions.get(node.id);
            if (!pos) return null;
            const isSelected = selection?.kind === "node" && selection.id === node.id;
            return (
              <g
                key={node.id}
                transform={`translate(${pos.x}, ${pos.y})`}
                className={`kx-graph-canvas__node${isSelected ? " kx-graph-canvas__node--selected" : ""}`}
                onClick={() => onPickNode(node.id)}
              >
                <circle
                  r={isSelected ? 9 : 6}
                  fill={NODE_COLORS[node.kind] ?? "#7A8893"}
                />
                <title>
                  {node.kind}: {node.label}
                </title>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
};

interface Layout {
  width: number;
  height: number;
  positions: Map<string, { x: number; y: number }>;
}

function layOut(nodes: GraphNode[]): Layout {
  const W = 720;
  const H = 480;
  const cx = W / 2;
  const cy = H / 2;
  const positions = new Map<string, { x: number; y: number }>();
  if (nodes.length === 0) return { width: W, height: H, positions };

  // Group by kind, deterministic order.
  const byKind = new Map<string, GraphNode[]>();
  for (const n of nodes) {
    const list = byKind.get(n.kind) ?? [];
    list.push(n);
    byKind.set(n.kind, list);
  }
  const kinds = Array.from(byKind.keys()).sort();

  // Concentric rings, biggest groups closer to the centre.
  const ringStep = Math.min(W, H) / (2 * Math.max(kinds.length, 1)) * 0.85;
  kinds.forEach((kind, kindIdx) => {
    const ringRadius = ringStep * (kindIdx + 1);
    const list = byKind.get(kind) ?? [];
    const sorted = list.slice().sort((a, b) => a.id.localeCompare(b.id));
    sorted.forEach((node, idx) => {
      const angle = (2 * Math.PI * idx) / Math.max(sorted.length, 1);
      positions.set(node.id, {
        x: cx + ringRadius * Math.cos(angle),
        y: cy + ringRadius * Math.sin(angle),
      });
    });
  });
  return { width: W, height: H, positions };
}

// ─── Inspector ───────────────────────────────────────────────────────────────

interface InspectorProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selection: Selection | null;
  setSelection: (s: Selection | null) => void;
  selectedNode: GraphNode | null;
  selectedEdge: GraphEdge | null;
  onOpenDocument: (documentId: string) => void;
}

const Inspector: React.FC<InspectorProps> = ({
  nodes,
  edges,
  selection,
  setSelection,
  selectedNode,
  selectedEdge,
  onOpenDocument,
}) => {
  return (
    <aside className="kx-graph-inspector" aria-label="Graph inspector">
      <div className="kx-graph-inspector__lists">
        <div>
          <h4 className="kx-graph-inspector__title">Nodes</h4>
          <ul className="kx-graph-inspector__list">
            {nodes.slice(0, 200).map((node) => {
              const isActive = selection?.kind === "node" && selection.id === node.id;
              return (
                <li key={node.id}>
                  <button
                    type="button"
                    className={`kx-graph-inspector__item${isActive ? " kx-graph-inspector__item--active" : ""}`}
                    onClick={() => setSelection({ kind: "node", id: node.id })}
                  >
                    <span
                      className="kx-chip__swatch"
                      style={{ background: NODE_COLORS[node.kind] ?? "#7A8893" }}
                      aria-hidden="true"
                    />
                    <span className="kx-graph-inspector__label">{node.label}</span>
                    <span className="kw-mono kw-mono--muted">{node.kind}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
        <div>
          <h4 className="kx-graph-inspector__title">Edges</h4>
          <ul className="kx-graph-inspector__list">
            {edges.slice(0, 200).map((edge) => {
              const id = `${edge.source_id}-${edge.kind}-${edge.target_id}`;
              const isActive = selection?.kind === "edge" && selection.id === id;
              return (
                <li key={id}>
                  <button
                    type="button"
                    className={`kx-graph-inspector__item${isActive ? " kx-graph-inspector__item--active" : ""}`}
                    onClick={() => setSelection({ kind: "edge", id })}
                  >
                    <span className="kx-graph-inspector__label">
                      {short(edge.source_id)} → {short(edge.target_id)}
                    </span>
                    <span className="kw-mono kw-mono--muted">{edge.kind}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
      <div className="kx-graph-inspector__detail">
        {selectedNode !== null ? (
          <NodeDetail node={selectedNode} onOpenDocument={onOpenDocument} />
        ) : selectedEdge !== null ? (
          <EdgeDetail edge={selectedEdge} />
        ) : (
          <p className="kw-status">
            Click a node or edge to inspect it. Document and Version nodes
            link back into the document viewer.
          </p>
        )}
      </div>
    </aside>
  );
};

const NodeDetail: React.FC<{
  node: GraphNode;
  onOpenDocument: (documentId: string) => void;
}> = ({ node, onOpenDocument }) => {
  const docId = stringProp(node.properties, "document_id") ?? (node.kind === "Document" ? node.id : null);
  return (
    <dl className="kx-graph-detail">
      <dt>Kind</dt>
      <dd>{node.kind}</dd>
      <dt>Label</dt>
      <dd>{node.label}</dd>
      <dt>Id</dt>
      <dd>
        <code>{node.id}</code>
      </dd>
      {Object.entries(node.properties)
        .filter(([key]) => key !== "document_id")
        .map(([key, value]) => (
          <React.Fragment key={key}>
            <dt>{key}</dt>
            <dd>{renderProp(value)}</dd>
          </React.Fragment>
        ))}
      {docId !== null && (
        <>
          <dt>Open</dt>
          <dd>
            <button
              type="button"
              className="kw-btn kw-btn--sm"
              onClick={() => onOpenDocument(docId)}
            >
              <Icon name="docs" size={12} /> View document
            </button>
          </dd>
        </>
      )}
    </dl>
  );
};

const EdgeDetail: React.FC<{ edge: GraphEdge }> = ({ edge }) => (
  <dl className="kx-graph-detail">
    <dt>Relation</dt>
    <dd>{edge.kind}</dd>
    <dt>From</dt>
    <dd>
      <code>{edge.source_id}</code>
    </dd>
    <dt>To</dt>
    <dd>
      <code>{edge.target_id}</code>
    </dd>
    {Object.entries(edge.properties).map(([key, value]) => (
      <React.Fragment key={key}>
        <dt>{key}</dt>
        <dd>{renderProp(value)}</dd>
      </React.Fragment>
    ))}
  </dl>
);

function stringProp(props: Record<string, unknown>, key: string): string | null {
  const v = props[key];
  return typeof v === "string" && v.length > 0 ? v : null;
}

function renderProp(value: unknown): React.ReactNode {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    return value.map((v) => (typeof v === "string" ? v : JSON.stringify(v))).join(", ");
  }
  return <code>{JSON.stringify(value)}</code>;
}

function short(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}
