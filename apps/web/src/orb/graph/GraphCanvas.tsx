/**
 * GraphCanvas — lightweight SVG renderer for the Knowledge Forge
 * graph view.
 *
 * PR 6 ships a deterministic radial layout instead of a force-directed
 * sim — keeps it bundle-cheap, predictable in screenshots, and
 * sufficient for the v0 inspector flow. Per design §5.2 the production
 * surface should swap to `@neo4j-nvl/react`'s `<InteractiveNvlWrapper>`
 * inside this slot; that's a follow-up since it adds ~600 KB gz to the
 * cold-start budget.
 *
 * Layout: nodes are placed on N concentric rings by kind:
 *   ring 0 — topics   (warm tint)
 *   ring 1 — entities (ok tint)
 *   ring 2 — chunks   (neutral tint)
 *   ring 3 — section / version / document (faint)
 *
 * Selection raises a node, highlights its incident edges, and lets the
 * parent open the inspector.
 */

import { useMemo } from "react";
import type { ReactElement } from "react";

import type { ApiGraphEdge, ApiGraphNode } from "../../api/types";

const KIND_RING: Record<ApiGraphNode["kind"], number> = {
  topic: 0,
  entity: 1,
  chunk: 2,
  section: 3,
  version: 3,
  document: 3,
};

const KIND_COLOR: Record<ApiGraphNode["kind"], string> = {
  topic: "var(--orb-warn)",
  entity: "var(--orb-ok)",
  chunk: "var(--orb-fg-muted)",
  section: "var(--orb-fg-faint)",
  version: "var(--orb-fg-faint)",
  document: "var(--orb-fg-faint)",
};

const KIND_RADIUS: Record<ApiGraphNode["kind"], number> = {
  topic: 11,
  entity: 9,
  chunk: 6,
  section: 5,
  version: 5,
  document: 6,
};

interface LaidOutNode extends ApiGraphNode {
  cx: number;
  cy: number;
  r: number;
}

export interface GraphCanvasProps {
  nodes: ApiGraphNode[];
  edges: ApiGraphEdge[];
  width?: number;
  height?: number;
  /** Currently-selected node id (used to highlight). */
  selectedId?: string | null;
  onSelect?: (nodeId: string) => void;
}

export function GraphCanvas({
  nodes,
  edges,
  width = 720,
  height = 480,
  selectedId = null,
  onSelect,
}: GraphCanvasProps): ReactElement {
  const cx = width / 2;
  const cy = height / 2;
  const maxR = Math.min(width, height) / 2 - 28;

  const laidOut = useMemo<LaidOutNode[]>(() => {
    if (nodes.length === 0) return [];
    // Group by ring, then place on a circle within the ring.
    const byRing = new Map<number, ApiGraphNode[]>();
    for (const n of nodes) {
      const ring = KIND_RING[n.kind] ?? 3;
      const list = byRing.get(ring) ?? [];
      list.push(n);
      byRing.set(ring, list);
    }
    const result: LaidOutNode[] = [];
    const rings = [...byRing.keys()].sort((a, b) => a - b);
    const ringStep = maxR / Math.max(rings.length, 1);
    for (const ring of rings) {
      const list = byRing.get(ring)!;
      const ringRadius = ringStep * (ring + 0.5);
      const angleStep = (2 * Math.PI) / Math.max(list.length, 1);
      for (let i = 0; i < list.length; i++) {
        const node = list[i];
        const angle = i * angleStep - Math.PI / 2;
        result.push({
          ...node,
          cx: cx + Math.cos(angle) * ringRadius,
          cy: cy + Math.sin(angle) * ringRadius,
          r: KIND_RADIUS[node.kind] ?? 6,
        });
      }
    }
    return result;
  }, [nodes, cx, cy, maxR]);

  const nodeIndex = useMemo(() => {
    const m = new Map<string, LaidOutNode>();
    for (const n of laidOut) m.set(n.id, n);
    return m;
  }, [laidOut]);

  const isHighlit = (edge: ApiGraphEdge) =>
    selectedId != null &&
    (edge.source_id === selectedId || edge.target_id === selectedId);

  return (
    <svg
      className="kf-gv__svg"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Knowledge graph (${nodes.length} nodes, ${edges.length} edges)`}
    >
      {/* Edges first so nodes paint over them. */}
      <g className="kf-gv__edges">
        {edges.map((e) => {
          const a = nodeIndex.get(e.source_id);
          const b = nodeIndex.get(e.target_id);
          if (!a || !b) return null;
          return (
            <line
              key={e.id}
              x1={a.cx}
              y1={a.cy}
              x2={b.cx}
              y2={b.cy}
              className={`kf-gv__edge ${isHighlit(e) ? "is-hl" : ""}`}
            />
          );
        })}
      </g>
      <g className="kf-gv__nodes">
        {laidOut.map((n) => {
          const sel = n.id === selectedId;
          return (
            <g
              key={n.id}
              transform={`translate(${n.cx} ${n.cy})`}
              className={`kf-gv__node ${sel ? "is-sel" : ""}`}
              onClick={onSelect ? () => onSelect(n.id) : undefined}
              data-testid={`kf-gv-node-${n.id}`}
            >
              <circle
                r={n.r + (sel ? 2 : 0)}
                style={{ fill: KIND_COLOR[n.kind] ?? "var(--orb-fg-faint)" }}
                stroke={sel ? "var(--orb-fg)" : "transparent"}
                strokeWidth={sel ? 1.5 : 0}
              />
              {sel && (
                <text
                  className="kf-gv__node-label orb-mono"
                  y={-(n.r + 6)}
                  textAnchor="middle"
                >
                  {n.label}
                </text>
              )}
            </g>
          );
        })}
      </g>
    </svg>
  );
}
