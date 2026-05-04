/**
 * Hierarchical graph canvas — clusters → documents → chunks → concepts.
 *
 * Port of the design's `graph.jsx`. Three layout modes coexist in one
 * `useLayout` hook:
 *
 *   1. Focus-root mode (when `focusRoot` is set): BFS from the focal
 *      node up to `depth` hops along every relation kind, placed in
 *      concentric rings.
 *   2. Corpus view: clusters at fixed positions; expanding a cluster
 *      reveals its docs in a ring; expanding a doc reveals its
 *      chunks in an inner ring; concepts orbit the canvas.
 *   3. Concepts view: focal concept at the centre with related
 *      concepts on a near ring, evidence chunks + their parent docs
 *      on outer rings.
 *
 * The canvas dispatches `kx-focus-root` events when the user double-
 * clicks (or shift-clicks) a node, so the host App can push focus
 * history without prop-drilling a callback through every node type.
 */

import React from "react";

import {
  CLUSTERS,
  DOC_TYPES,
  type ChunkConceptLink,
  type ConceptEdge,
  type ExplorerChunk,
  type ExplorerConcept,
  type ExplorerDocEdge,
  type ExplorerDocument,
  type ExplorerSnapshot,
  chunkById,
  chunksForConcept,
  chunksForDoc,
  conceptById,
  conceptsForChunk,
  docById,
} from "../state/explorer-data";
import { ACCENT, NAVY, NAVY2 } from "./icons";

export type GraphView = "corpus" | "concepts";

export type FocusKind = "cluster" | "doc" | "chunk" | "concept";

export interface FocusRoot {
  kind: FocusKind;
  id: string;
  label: string;
}

export interface NodeSelection {
  kind: FocusKind;
  id: string;
  doc?: ExplorerDocument;
  chunk?: ExplorerChunk;
  concept?: ExplorerConcept;
  cluster?: string;
}

interface LayoutNodeBase {
  id: string;
  x: number;
  y: number;
  focal?: boolean;
}
interface ClusterNodeData extends LayoutNodeBase {
  kind: "cluster";
  cluster: string;
  docCount: number;
  chunkCount: number;
}
interface DocNodeData extends LayoutNodeBase {
  kind: "doc";
  doc: ExplorerDocument;
  expanded: boolean;
  clusterKey?: string;
}
interface ChunkNodeData extends LayoutNodeBase {
  kind: "chunk";
  chunk: ExplorerChunk;
  parent?: string;
}
interface ConceptNodeData extends LayoutNodeBase {
  kind: "concept";
  concept: ExplorerConcept;
}
type LayoutNode = ClusterNodeData | DocNodeData | ChunkNodeData | ConceptNodeData;

interface LayoutEdge {
  a: string;
  b: string;
  type: ExplorerDocEdge["type"] | ConceptEdge[2] | "mentions";
  weight?: number;
}

interface Layout {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  W: number;
  H: number;
}

interface GraphCanvasProps {
  snapshot: ExplorerSnapshot;
  view: GraphView;
  selectedId: string | null;
  conceptFocus: string;
  onSelect: (n: NodeSelection) => void;
  onToggleCluster: (key: string) => void;
  onToggleDoc: (id: string) => void;
  expandedClusters: Set<string>;
  expandedDocs: Set<string>;
  showClusters: boolean;
  showConfHeat: boolean;
  theme: "light" | "dark";
  depth: number;
  hoveredId: string | null;
  onHover: (id: string | null) => void;
  search: string;
  focusRoot: FocusRoot | null;
}

const CLUSTER_COLOR = (key: string): string => {
  const c = CLUSTERS[key];
  if (!c) return "#888";
  return `oklch(0.78 0.06 ${c.hue})`;
};
const CLUSTER_FILL = (key: string): string => {
  const c = CLUSTERS[key];
  if (!c) return "#EEE";
  return `oklch(0.94 0.04 ${c.hue})`;
};

export function confColor(c: number): string {
  if (c >= 0.9) return "#3F8E60";
  if (c >= 0.85) return "#9CB142";
  if (c >= 0.75) return "#D9892C";
  return "#C2453A";
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function useLayout(props: {
  snapshot: ExplorerSnapshot;
  view: GraphView;
  conceptFocus: string;
  expandedClusters: Set<string>;
  expandedDocs: Set<string>;
  focusRoot: FocusRoot | null;
  focusDepth: number;
}): Layout {
  const { snapshot, view, conceptFocus, expandedClusters, expandedDocs, focusRoot, focusDepth } = props;
  return React.useMemo<Layout>(() => {
    const W = 1000;
    const H = 720;
    const nodes: LayoutNode[] = [];
    const edges: LayoutEdge[] = [];
    const ALL_CLUSTERS = Object.keys(CLUSTERS);

    // FOCUS-ROOT MODE — BFS expansion from a chosen node.
    if (focusRoot) {
      const center = { x: W * 0.5, y: H * 0.5 };
      const { kind, id } = focusRoot;
      const rootId = kind === "cluster" ? "cl_" + id : id;

      const adj: Record<string, Array<{ to: string; type: string }>> = {};
      const addEdge = (a: string, b: string, type: string) => {
        (adj[a] = adj[a] ?? []).push({ to: b, type });
        (adj[b] = adj[b] ?? []).push({ to: a, type });
      };
      snapshot.documents.forEach((d) => addEdge("cl_" + d.cluster, d.id, "contains"));
      snapshot.chunks.forEach((c) => addEdge(c.doc, c.id, "contains"));
      snapshot.docEdges.forEach((e) => addEdge(e.a, e.b, e.type));
      snapshot.chunkConcept.forEach(([cid, kid]) => addEdge(cid, kid, "mentions"));
      snapshot.conceptEdges.forEach(([a, b]) => addEdge(a, b, "related"));

      const distance: Record<string, number> = { [rootId]: 0 };
      const seenEdge = new Set<string>();
      const edgeSet: Array<{ a: string; b: string; type: string }> = [];
      let frontier = [rootId];
      const maxDepth = focusDepth >= 99 ? 99 : focusDepth;
      for (let d = 0; d < maxDepth; d++) {
        const next: string[] = [];
        frontier.forEach((nodeId) => {
          (adj[nodeId] ?? []).forEach(({ to, type }) => {
            const k = nodeId < to ? nodeId + "|" + to : to + "|" + nodeId;
            if (!seenEdge.has(k)) {
              seenEdge.add(k);
              edgeSet.push({ a: nodeId, b: to, type });
            }
            if (!(to in distance)) {
              distance[to] = d + 1;
              next.push(to);
            }
          });
        });
        frontier = next;
        if (!frontier.length) break;
      }

      const idsByDist: Record<string, string[]> = {};
      Object.entries(distance).forEach(([nid, dist]) => {
        (idsByDist[dist] = idsByDist[dist] ?? []).push(nid);
      });

      const resolveKind = (nid: string): LayoutNode | null => {
        if (nid.startsWith("cl_")) {
          const cluster = nid.slice(3);
          const docs = snapshot.documents.filter((d) => d.cluster === cluster);
          return {
            id: nid,
            x: 0,
            y: 0,
            kind: "cluster",
            cluster,
            docCount: docs.length,
            chunkCount: docs.reduce((a, d) => a + d.chunks, 0),
          };
        }
        const doc = docById(snapshot, nid);
        if (doc) return { id: nid, x: 0, y: 0, kind: "doc", doc, expanded: expandedDocs.has(nid) };
        const chunk = chunkById(snapshot, nid);
        if (chunk) return { id: nid, x: 0, y: 0, kind: "chunk", chunk };
        const concept = conceptById(snapshot, nid);
        if (concept) return { id: nid, x: 0, y: 0, kind: "concept", concept };
        return null;
      };

      const RING = [0, 150, 260, 360, 450, 530, 600, 660, 710];
      Object.keys(idsByDist)
        .sort((a, b) => +a - +b)
        .forEach((distStr) => {
          const dist = +distStr;
          const ring = RING[Math.min(dist, RING.length - 1)];
          const arr = idsByDist[distStr];
          arr.forEach((nid, i) => {
            const meta = resolveKind(nid);
            if (!meta) return;
            if (dist === 0) {
              meta.x = center.x;
              meta.y = center.y;
              meta.focal = true;
            } else {
              const a = (i / arr.length) * Math.PI * 2 - Math.PI / 2 + dist * 0.2;
              meta.x = center.x + Math.cos(a) * ring;
              meta.y = center.y + Math.sin(a) * ring;
            }
            nodes.push(meta);
          });
        });
      const nodeIdSet = new Set(nodes.map((n) => n.id));
      edgeSet.forEach((e) => {
        if (nodeIdSet.has(e.a) && nodeIdSet.has(e.b)) {
          edges.push({ a: e.a, b: e.b, type: e.type as LayoutEdge["type"] });
        }
      });
      return { nodes, edges, W, H };
    }

    if (view === "corpus") {
      const clusterPos: Record<string, { x: number; y: number }> = {
        hr: { x: 0.18, y: 0.30 },
        product: { x: 0.50, y: 0.20 },
        eng: { x: 0.80, y: 0.34 },
        legal: { x: 0.28, y: 0.78 },
        finance: { x: 0.74, y: 0.74 },
        unknown: { x: 0.5, y: 0.5 },
      };
      ALL_CLUSTERS.forEach((ck) => {
        const cp = clusterPos[ck] ?? { x: 0.5, y: 0.5 };
        const cx = cp.x * W;
        const cy = cp.y * H;
        const docs = snapshot.documents.filter((d) => d.cluster === ck);
        const isExpanded = expandedClusters.has(ck);
        if (!isExpanded) {
          nodes.push({
            id: "cl_" + ck,
            kind: "cluster",
            x: cx,
            y: cy,
            cluster: ck,
            docCount: docs.length,
            chunkCount: docs.reduce((a, d) => a + d.chunks, 0),
          });
        } else {
          const r = 90 + Math.min(docs.length, 12) * 6;
          docs.forEach((d, i) => {
            const a = (i / Math.max(docs.length, 1)) * Math.PI * 2 - Math.PI / 2;
            const dx = cx + Math.cos(a) * r;
            const dy = cy + Math.sin(a) * r;
            const docExpanded = expandedDocs.has(d.id);
            nodes.push({
              id: d.id,
              kind: "doc",
              x: dx,
              y: dy,
              doc: d,
              expanded: docExpanded,
              clusterKey: ck,
            });
            if (docExpanded) {
              const chunks = chunksForDoc(snapshot, d.id);
              chunks.forEach((c, j) => {
                const angle = (j / Math.max(chunks.length, 1)) * Math.PI * 2;
                const cr = 38;
                nodes.push({
                  id: c.id,
                  kind: "chunk",
                  x: dx + Math.cos(angle) * cr,
                  y: dy + Math.sin(angle) * cr,
                  chunk: c,
                  parent: d.id,
                });
                edges.push({ a: d.id, b: c.id, type: "contains", weight: 1 });
              });
            }
          });
        }
      });
      // Document-level edges
      snapshot.docEdges.forEach((e) => {
        const docA = docById(snapshot, e.a);
        const docB = docById(snapshot, e.b);
        if (!docA || !docB) return;
        const aClExp = expandedClusters.has(docA.cluster);
        const bClExp = expandedClusters.has(docB.cluster);
        const idA = aClExp ? docA.id : "cl_" + docA.cluster;
        const idB = bClExp ? docB.id : "cl_" + docB.cluster;
        if (idA === idB) return;
        edges.push({ a: idA, b: idB, type: e.type, weight: e.weight });
      });
      // Chunk-concept halo
      const chunkIds = new Set(nodes.filter((n): n is ChunkNodeData => n.kind === "chunk").map((n) => n.id));
      if (chunkIds.size) {
        const conceptIds = new Set<string>();
        chunkIds.forEach((cid) => conceptsForChunk(snapshot, cid).forEach((k) => conceptIds.add(k.id)));
        const ckList = [...conceptIds];
        ckList.forEach((kid, i) => {
          const a = (i / Math.max(ckList.length, 1)) * Math.PI * 2;
          const concept = conceptById(snapshot, kid);
          if (!concept) return;
          nodes.push({
            id: kid,
            kind: "concept",
            x: W * 0.5 + Math.cos(a) * 460,
            y: H * 0.5 + Math.sin(a) * 320,
            concept,
          });
        });
        snapshot.chunkConcept.forEach(([cid, kid]: ChunkConceptLink) => {
          if (chunkIds.has(cid) && conceptIds.has(kid)) {
            edges.push({ a: cid, b: kid, type: "mentions", weight: 1 });
          }
        });
      }
    }

    if (view === "concepts") {
      const focalId = (conceptFocus && conceptById(snapshot, conceptFocus))
        ? conceptFocus
        : snapshot.concepts[0]?.id;
      if (!focalId) return { nodes, edges, W, H };
      const center = { x: W * 0.5, y: H * 0.5 };
      const focal = conceptById(snapshot, focalId);
      if (!focal) return { nodes, edges, W, H };
      nodes.push({ id: focal.id, kind: "concept", x: center.x, y: center.y, concept: focal, focal: true });
      const related = snapshot.conceptEdges
        .filter(([a, b]) => a === focalId || b === focalId)
        .map(([a, b]) => (a === focalId ? b : a))
        .map((id) => conceptById(snapshot, id))
        .filter((x): x is ExplorerConcept => Boolean(x));
      related.forEach((k, i) => {
        const a = (i / Math.max(related.length, 1)) * Math.PI * 2 - Math.PI / 2;
        nodes.push({
          id: k.id,
          kind: "concept",
          x: center.x + Math.cos(a) * 180,
          y: center.y + Math.sin(a) * 180,
          concept: k,
        });
        edges.push({ a: focalId, b: k.id, type: "related" });
      });
      const chunks = chunksForConcept(snapshot, focalId);
      chunks.forEach((c, i) => {
        const a = (i / Math.max(chunks.length, 1)) * Math.PI * 2 + Math.PI / 6;
        nodes.push({
          id: c.id,
          kind: "chunk",
          x: center.x + Math.cos(a) * 320,
          y: center.y + Math.sin(a) * 320,
          chunk: c,
        });
        edges.push({ a: focalId, b: c.id, type: "mentions" });
        const parent = docById(snapshot, c.doc);
        if (parent && !nodes.find((n) => n.id === parent.id)) {
          nodes.push({
            id: parent.id,
            kind: "doc",
            x: center.x + Math.cos(a) * 430,
            y: center.y + Math.sin(a) * 430,
            doc: parent,
            expanded: false,
          });
        }
        edges.push({ a: c.id, b: c.doc, type: "contains" });
      });
    }
    return { nodes, edges, W, H };
  }, [snapshot, view, conceptFocus, expandedClusters, expandedDocs, focusRoot, focusDepth]);
}

// ─── Node renderers ──────────────────────────────────────────────────────────

const ClusterNode: React.FC<{
  node: ClusterNodeData;
  selected: boolean;
  dim: boolean;
  onToggle: () => void;
}> = ({ node, selected, dim, onToggle }) => {
  const c = CLUSTERS[node.cluster];
  const r = 36 + Math.log(Math.max(node.chunkCount, 4)) * 3;
  return (
    <g transform={`translate(${node.x}, ${node.y})`} opacity={dim ? 0.35 : 1}>
      <circle r={r + 10} fill={CLUSTER_FILL(node.cluster)} opacity={0.5} />
      <circle
        r={r}
        fill={CLUSTER_FILL(node.cluster)}
        stroke={selected ? "#E8A23F" : CLUSTER_COLOR(node.cluster)}
        strokeWidth={selected ? 2.5 : 1.5}
      />
      <text textAnchor="middle" y={-4} fontSize="11" fontFamily="Inter, sans-serif" fontWeight={700} fill={NAVY}>
        {(c?.label ?? node.cluster).toUpperCase()}
      </text>
      <text textAnchor="middle" y={10} fontSize="9" fontFamily="JetBrains Mono, monospace" fill={NAVY2}>
        {node.docCount} docs · {node.chunkCount} chk
      </text>
      <g
        transform={`translate(${r - 6}, ${-r + 6})`}
        style={{ cursor: "pointer" }}
        onClick={(e) => {
          e.stopPropagation();
          onToggle();
        }}
      >
        <circle r={9} fill="white" stroke={NAVY} strokeWidth={1} />
        <path d="M -4 0 L 4 0 M 0 -4 L 0 4" stroke={NAVY} strokeWidth={1.4} strokeLinecap="round" />
      </g>
    </g>
  );
};

const ExpandedClusterHalo: React.FC<{
  cluster: string;
  x: number;
  y: number;
  w: number;
  h: number;
  theme: "light" | "dark";
  onCollapse: () => void;
}> = ({ cluster, x, y, w, h, theme, onCollapse }) => {
  const c = CLUSTERS[cluster];
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={14} fill={CLUSTER_COLOR(cluster)} opacity={0.08} />
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={14}
        fill="none"
        stroke={CLUSTER_COLOR(cluster)}
        strokeWidth="1"
        strokeDasharray="4 3"
        opacity={0.55}
      />
      <text
        x={x + 12}
        y={y + 16}
        fontSize="10"
        fontFamily="JetBrains Mono, monospace"
        fill={theme === "dark" ? "#9CC0F0" : NAVY2}
        opacity={0.85}
      >
        {(c?.label ?? cluster).toUpperCase()}
      </text>
      <g
        transform={`translate(${x + w - 14}, ${y + 14})`}
        style={{ cursor: "pointer" }}
        onClick={(e) => {
          e.stopPropagation();
          onCollapse();
        }}
      >
        <title>Collapse cluster</title>
        <circle r={9} fill={theme === "dark" ? "#0E1A2E" : "white"} stroke={CLUSTER_COLOR(cluster)} strokeWidth={1.2} />
        <path d="M -4 0 L 4 0" stroke={NAVY} strokeWidth={1.5} strokeLinecap="round" />
      </g>
    </g>
  );
};

const DocNode: React.FC<{
  node: DocNodeData;
  selected: boolean;
  dim: boolean;
  theme: "light" | "dark";
  showConfHeat: boolean;
  onToggle: () => void;
}> = ({ node, selected, dim, theme, showConfHeat, onToggle }) => {
  const d = node.doc;
  const dt = DOC_TYPES[d.type];
  const r = 9;
  const baseFill = theme === "dark" ? "#0E1A2E" : "#FFFFFF";
  return (
    <g transform={`translate(${node.x}, ${node.y})`} opacity={dim ? 0.3 : 1} style={{ cursor: "pointer" }}>
      <rect
        x={-r}
        y={-r - 1}
        width={r * 2}
        height={r * 2 + 2}
        rx={2}
        fill={baseFill}
        stroke={selected ? "#E8A23F" : theme === "dark" ? "#7CA8E8" : NAVY}
        strokeWidth={selected ? 1.8 : 1}
      />
      <rect x={-r + 1.5} y={-r + 0.5} width={3} height={r * 2 - 1} rx={1} fill={dt?.color ?? "#999"} />
      <line x1={-r + 6} y1={-r + 4} x2={r - 2} y2={-r + 4} stroke={NAVY2} strokeWidth={0.5} opacity={0.5} />
      <line x1={-r + 6} y1={-r + 8} x2={r - 4} y2={-r + 8} stroke={NAVY2} strokeWidth={0.5} opacity={0.5} />
      <line x1={-r + 6} y1={-r + 12} x2={r - 2} y2={-r + 12} stroke={NAVY2} strokeWidth={0.5} opacity={0.5} />
      {showConfHeat && (
        <rect x={-r} y={r} width={r * 2 * d.confidence} height={1.5} fill={confColor(d.confidence)} />
      )}
      <text
        textAnchor="middle"
        y={r + 11}
        fontSize="8.5"
        fontFamily="Inter, sans-serif"
        fill={theme === "dark" ? "#B0C0D8" : NAVY2}
      >
        {truncate(d.title, 18)}
      </text>
      {!node.expanded && (
        <g
          transform={`translate(${r - 1}, ${-r - 1})`}
          style={{ cursor: "pointer" }}
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
        >
          <circle r={5} fill="white" stroke={NAVY} strokeWidth={0.8} />
          <path d="M -2.2 0 L 2.2 0 M 0 -2.2 L 0 2.2" stroke={NAVY} strokeWidth={1} strokeLinecap="round" />
        </g>
      )}
      {node.expanded && (
        <g
          transform={`translate(${r - 1}, ${-r - 1})`}
          style={{ cursor: "pointer" }}
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
        >
          <title>Collapse chunks</title>
          <circle r={6} fill={NAVY} stroke={NAVY} strokeWidth={0.8} />
          <path d="M -2.6 0 L 2.6 0" stroke="white" strokeWidth={1.3} strokeLinecap="round" />
        </g>
      )}
    </g>
  );
};

const ChunkNode: React.FC<{
  node: ChunkNodeData;
  selected: boolean;
  dim: boolean;
  theme: "light" | "dark";
  showConfHeat: boolean;
}> = ({ node, selected, dim, theme, showConfHeat }) => {
  const c = node.chunk;
  const r = 4.5;
  return (
    <g transform={`translate(${node.x}, ${node.y})`} opacity={dim ? 0.3 : 1}>
      <circle
        r={r + 2.5}
        fill={theme === "dark" ? "#0E1A2E" : "#F4F6FB"}
        stroke={selected ? "#E8A23F" : theme === "dark" ? "#7CA8E8" : NAVY}
        strokeWidth={selected ? 1.5 : 0.7}
      />
      <circle r={r} fill={showConfHeat ? confColor(c.confidence) : theme === "dark" ? "#3A6CB8" : ACCENT} />
    </g>
  );
};

function hexPoints(s: number): string {
  const w = s * 1.6;
  return [
    [-w * 0.5, 0],
    [-w * 0.25, -s],
    [w * 0.25, -s],
    [w * 0.5, 0],
    [w * 0.25, s],
    [-w * 0.25, s],
  ]
    .map((p) => p.join(","))
    .join(" ");
}

const ConceptNode: React.FC<{
  node: ConceptNodeData;
  selected: boolean;
  dim: boolean;
  theme: "light" | "dark";
}> = ({ node, selected, dim, theme }) => {
  const k = node.concept;
  const s = node.focal ? 24 : 16;
  return (
    <g transform={`translate(${node.x}, ${node.y})`} opacity={dim ? 0.3 : 1}>
      <polygon
        points={hexPoints(s)}
        fill={theme === "dark" ? "#0E1A2E" : "#F4F6FB"}
        stroke={selected || node.focal ? "#E8A23F" : theme === "dark" ? "#7CA8E8" : NAVY}
        strokeWidth={selected || node.focal ? 1.8 : 1}
      />
      <text
        textAnchor="middle"
        y={3}
        fontSize={node.focal ? 10 : 8.5}
        fontFamily="Inter, sans-serif"
        fontWeight={600}
        fill={theme === "dark" ? "#D6E1F0" : NAVY}
      >
        {truncate(k.name, node.focal ? 14 : 11)}
      </text>
    </g>
  );
};

interface EdgeStyle {
  stroke: string;
  dash: string;
  width: number;
  arrow?: boolean;
}

function edgeStyle(type: string, theme: "light" | "dark"): EdgeStyle {
  const c = theme === "dark" ? "#5B7AA8" : "#9DAEC8";
  switch (type) {
    case "contains":
      return { stroke: c, dash: "", width: 0.8 };
    case "reference":
      return { stroke: c, dash: "", width: 1, arrow: true };
    case "similar":
      return { stroke: c, dash: "4 3", width: 1 };
    case "mentions":
      return { stroke: c, dash: "1 3", width: 0.8 };
    case "related":
      return { stroke: c, dash: "4 3", width: 1.1 };
    case "contradict":
      return { stroke: "#C2453A", dash: "2 2", width: 1.4 };
    default:
      return { stroke: c, dash: "", width: 1 };
  }
}

export const GraphCanvas: React.FC<GraphCanvasProps> = ({
  snapshot,
  view,
  selectedId,
  conceptFocus,
  onSelect,
  onToggleCluster,
  onToggleDoc,
  expandedClusters,
  expandedDocs,
  showClusters,
  showConfHeat,
  theme,
  depth,
  hoveredId,
  onHover,
  search,
  focusRoot,
}) => {
  const layout = useLayout({
    snapshot,
    view,
    conceptFocus,
    expandedClusters,
    expandedDocs,
    focusRoot,
    focusDepth: depth,
  });
  const { nodes, edges, W, H } = layout;
  const nodeMap = React.useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n] as const)), [nodes]);

  const visibleSet = React.useMemo<Set<string> | null>(() => {
    if (focusRoot) return null;
    if (!selectedId || depth >= 99) return null;
    const adj: Record<string, string[]> = {};
    nodes.forEach((n) => {
      adj[n.id] = [];
    });
    edges.forEach((e) => {
      (adj[e.a] = adj[e.a] ?? []).push(e.b);
      (adj[e.b] = adj[e.b] ?? []).push(e.a);
    });
    if (!adj[selectedId]) return null;
    const seen = new Set<string>([selectedId]);
    let frontier = [selectedId];
    for (let i = 0; i < depth; i++) {
      const next: string[] = [];
      frontier.forEach((id) => {
        (adj[id] ?? []).forEach((nb) => {
          if (!seen.has(nb)) {
            seen.add(nb);
            next.push(nb);
          }
        });
      });
      frontier = next;
    }
    return seen;
  }, [nodes, edges, selectedId, depth, focusRoot]);

  const isDim = (id: string): boolean => {
    if (search) {
      const q = search.toLowerCase();
      const n = nodeMap[id];
      if (!n) return false;
      let txt = "";
      if (n.kind === "doc") txt = n.doc.title;
      else if (n.kind === "chunk") txt = n.chunk.label;
      else if (n.kind === "concept") txt = n.concept.name;
      else if (n.kind === "cluster") txt = CLUSTERS[n.cluster]?.label ?? "";
      return !txt.toLowerCase().includes(q);
    }
    if (!visibleSet) return false;
    return !visibleSet.has(id);
  };

  const hulls = React.useMemo(() => {
    if (view !== "corpus" || !showClusters) return [];
    type DocOrChunk = DocNodeData | ChunkNodeData;
    const groups: Record<string, DocOrChunk[]> = {};
    nodes.forEach((n) => {
      if (n.kind === "doc" && n.clusterKey) {
        (groups[n.clusterKey] = groups[n.clusterKey] ?? []).push(n);
      }
    });
    nodes.forEach((n) => {
      if (n.kind === "chunk" && n.parent) {
        const dn = nodeMap[n.parent];
        if (dn && dn.kind === "doc" && dn.clusterKey) {
          (groups[dn.clusterKey] = groups[dn.clusterKey] ?? []).push(n);
        }
      }
    });
    return Object.entries(groups).map(([key, arr]) => {
      const xs = arr.map((n) => n.x);
      const ys = arr.map((n) => n.y);
      const x0 = Math.min(...xs) - 26;
      const x1 = Math.max(...xs) + 26;
      const y0 = Math.min(...ys) - 24;
      const y1 = Math.max(...ys) + 18;
      return { key, x: x0, y: y0, w: x1 - x0, h: y1 - y0, label: CLUSTERS[key]?.label ?? key };
    });
  }, [nodes, nodeMap, view, showClusters]);

  const dispatchFocus = (n: LayoutNode): void => {
    const sel = toSelection(n);
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("kx-focus-root", { detail: sel }));
    }
  };

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      style={{ width: "100%", height: "100%", display: "block" }}
    >
      <defs>
        <pattern id="kx-grid" width="24" height="24" patternUnits="userSpaceOnUse">
          <path
            d="M 24 0 L 0 0 0 24"
            fill="none"
            stroke={theme === "dark" ? "#142339" : "#E8ECF3"}
            strokeWidth="0.5"
          />
        </pattern>
        <marker
          id="kx-arrow"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L10,5 L0,10 z" fill={theme === "dark" ? "#5B7AA8" : "#9DAEC8"} />
        </marker>
      </defs>
      <rect width={W} height={H} fill={theme === "dark" ? "#0A1628" : "#FBFCFE"} />
      <rect width={W} height={H} fill="url(#kx-grid)" />

      {hulls.map((h) => (
        <ExpandedClusterHalo
          key={h.key}
          cluster={h.key}
          x={h.x}
          y={h.y}
          w={h.w}
          h={h.h}
          theme={theme}
          onCollapse={() => onToggleCluster(h.key)}
        />
      ))}

      <g>
        {edges.map((e, i) => {
          const a = nodeMap[e.a];
          const b = nodeMap[e.b];
          if (!a || !b) return null;
          const dim = isDim(e.a) || isDim(e.b);
          const s = edgeStyle(e.type, theme);
          return (
            <line
              key={i}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={s.stroke}
              strokeWidth={s.width}
              strokeDasharray={s.dash}
              opacity={dim ? 0.1 : 0.6}
              markerEnd={s.arrow ? "url(#kx-arrow)" : undefined}
            />
          );
        })}
      </g>

      <g>
        {nodes.map((n) => {
          const sel = selectedId === n.id || hoveredId === n.id;
          const dim = isDim(n.id);
          const handleClick = (e: React.MouseEvent): void => {
            const selection = toSelection(n);
            if (e.shiftKey || e.altKey) {
              onSelect(selection);
              dispatchFocus(n);
            } else {
              onSelect(selection);
            }
          };
          const handleDouble = (e: React.MouseEvent): void => {
            e.stopPropagation();
            dispatchFocus(n);
          };
          return (
            <g
              key={n.id}
              style={{ cursor: "pointer" }}
              onClick={handleClick}
              onDoubleClick={handleDouble}
              onMouseEnter={() => onHover(n.id)}
              onMouseLeave={() => onHover(null)}
            >
              {n.kind === "cluster" && (
                <ClusterNode node={n} selected={sel} dim={dim} onToggle={() => onToggleCluster(n.cluster)} />
              )}
              {n.kind === "doc" && (
                <DocNode
                  node={n}
                  selected={sel}
                  dim={dim}
                  theme={theme}
                  showConfHeat={showConfHeat}
                  onToggle={() => onToggleDoc(n.doc.id)}
                />
              )}
              {n.kind === "chunk" && (
                <ChunkNode node={n} selected={sel} dim={dim} theme={theme} showConfHeat={showConfHeat} />
              )}
              {n.kind === "concept" && <ConceptNode node={n} selected={sel} dim={dim} theme={theme} />}
            </g>
          );
        })}
      </g>
    </svg>
  );
};

function toSelection(n: LayoutNode): NodeSelection {
  if (n.kind === "cluster") return { kind: "cluster", id: n.cluster, cluster: n.cluster };
  if (n.kind === "doc") return { kind: "doc", id: n.doc.id, doc: n.doc };
  if (n.kind === "chunk") return { kind: "chunk", id: n.chunk.id, chunk: n.chunk };
  return { kind: "concept", id: n.concept.id, concept: n.concept };
}
