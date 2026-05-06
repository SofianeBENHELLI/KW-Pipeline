/**
 * Hierarchical graph canvas — clusters → documents → chunks → concepts.
 *
 * Port of the design's `graph.jsx`, expanded with the v2 polish pass:
 *
 *   * Pan + wheel-zoom on a controlled `viewBox` (no React Flow dep —
 *     the widget stays self-contained inside the dashboard host).
 *   * Off-center camera on selection: the focal node animates to ~40 %
 *     of width so the right-hand DetailPanel never sits on top of it
 *     (item #1). User-initiated pans suspend auto-centering until the
 *     next selection.
 *   * Hover ghosting (item #3): non-adjacent nodes + their edges drop
 *     to 0.1 opacity; the hovered node + its neighbours keep full
 *     contrast and gain a glow filter alongside selected nodes.
 *   * Level of Detail (item #4): node labels hide at zoom < 0.6, edge
 *     arrow markers drop at zoom < 0.4 to save raster work. Node
 *     renderers are wrapped in React.memo so viewport transforms
 *     don't trigger child re-renders.
 *   * Directional edge gradients (tech note): SVG `<linearGradient>`
 *     paints `reference` / `contains` edges from accent → ink so the
 *     direction is obvious without arrowheads at low zoom.
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

// ─── Layout (cluster / doc / chunk / concept positions) ──────────────────────

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
    // Cluster catalogue: every id the snapshot knows about (taxonomy
    // categories or doc-derived) plus any cluster a real doc is
    // classified to. Empty *computed* clusters are filtered out so
    // the canvas never paints phantom seeds (Product/Engineering/...)
    // against an empty corpus; *imposed* (operator-authored) ones
    // are kept even when empty so the operator's tree is visible.
    const _clusterIds = new Set<string>();
    snapshot.documents.forEach((d) => _clusterIds.add(d.cluster));
    Object.keys(snapshot.clusters).forEach((k) => _clusterIds.add(k));
    const ALL_CLUSTERS = [..._clusterIds].filter((ck) => {
      const hasDocs = snapshot.documents.some((d) => d.cluster === ck);
      const isImposed = snapshot.clusters[ck]?.source === "imposed";
      return hasDocs || isImposed;
    });

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

// ─── Viewport (pan + zoom + animated centering) ──────────────────────────────

interface ViewBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

const BASE_W = 1000;
const BASE_H = 720;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 3.0;
/** Coord-space offset reserved on the right when centering on a selected
 * node. Keeps the focal node in the left ~60 % of the canvas so the
 * detail panel column never visually crowds it. */
const RIGHT_BIAS = 110;

const DEFAULT_VIEWBOX: ViewBox = { x: 0, y: 0, w: BASE_W, h: BASE_H };

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

function useViewport(args: {
  selectedId: string | null;
  focusRoot: FocusRoot | null;
  layout: Layout;
  selectionAnimMs?: number;
}): {
  viewBox: ViewBox;
  zoom: number;
  setViewBox: React.Dispatch<React.SetStateAction<ViewBox>>;
  resetView: () => void;
  panBy: (dx: number, dy: number) => void;
  zoomAt: (factor: number, cx: number, cy: number) => void;
  notePanned: () => void;
} {
  const { selectedId, focusRoot, layout, selectionAnimMs = 800 } = args;
  const [viewBox, setViewBox] = React.useState<ViewBox>(DEFAULT_VIEWBOX);
  const userPanned = React.useRef(false);
  const animRef = React.useRef<number | null>(null);

  const cancelAnim = React.useCallback(() => {
    if (animRef.current !== null) {
      cancelAnimationFrame(animRef.current);
      animRef.current = null;
    }
  }, []);

  const animateTo = React.useCallback(
    (target: ViewBox) => {
      cancelAnim();
      const start = performance.now();
      const from: ViewBox = { ...viewBox };
      const tick = (now: number): void => {
        const t = Math.min(1, (now - start) / selectionAnimMs);
        const e = easeOutCubic(t);
        setViewBox({
          x: from.x + (target.x - from.x) * e,
          y: from.y + (target.y - from.y) * e,
          w: from.w + (target.w - from.w) * e,
          h: from.h + (target.h - from.h) * e,
        });
        if (t < 1) animRef.current = requestAnimationFrame(tick);
        else animRef.current = null;
      };
      animRef.current = requestAnimationFrame(tick);
    },
    [viewBox, selectionAnimMs, cancelAnim],
  );

  // Animate on selection change (or focus-root change) — but only if the
  // user hasn't manually panned since the last selection.
  React.useEffect(() => {
    return () => cancelAnim();
  }, [cancelAnim]);

  React.useEffect(() => {
    if (!selectedId && !focusRoot) return;
    const targetId = focusRoot
      ? focusRoot.kind === "cluster"
        ? "cl_" + focusRoot.id
        : focusRoot.id
      : selectedId;
    if (!targetId) return;
    const node = layout.nodes.find((n) => n.id === targetId);
    if (!node) return;
    // Reset the user-panned flag whenever a new selection arrives — the
    // user explicitly asked us to re-center.
    userPanned.current = false;
    const w = viewBox.w;
    const h = viewBox.h;
    // Place the focal node at ~40 % of width to leave breathing room
    // for the right-side panel (item #1 — asymmetric padding).
    const cx = node.x - w * 0.5 + RIGHT_BIAS;
    const cy = node.y - h * 0.5;
    animateTo({ x: cx, y: cy, w, h });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- viewBox is read-only here
  }, [selectedId, focusRoot]);

  const resetView = React.useCallback(() => {
    cancelAnim();
    userPanned.current = false;
    animateTo(DEFAULT_VIEWBOX);
  }, [animateTo, cancelAnim]);

  const panBy = React.useCallback((dx: number, dy: number) => {
    cancelAnim();
    userPanned.current = true;
    setViewBox((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
  }, [cancelAnim]);

  const zoomAt = React.useCallback((factor: number, cx: number, cy: number) => {
    cancelAnim();
    setViewBox((v) => {
      const newW = clamp(v.w / factor, BASE_W / MAX_ZOOM, BASE_W / MIN_ZOOM);
      const newH = (newW / BASE_W) * BASE_H;
      // Keep (cx, cy) — a coord-space point under the cursor — fixed
      // across the zoom step so wheel-zoom feels anchored.
      const ratio = newW / v.w;
      return {
        x: cx - (cx - v.x) * ratio,
        y: cy - (cy - v.y) * ratio,
        w: newW,
        h: newH,
      };
    });
  }, [cancelAnim]);

  const notePanned = React.useCallback(() => {
    userPanned.current = true;
  }, []);

  const zoom = BASE_W / viewBox.w;
  return { viewBox, zoom, setViewBox, resetView, panBy, zoomAt, notePanned };
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

// ─── Adjacency for ghosting ──────────────────────────────────────────────────

function useNeighborSet(layout: Layout, id: string | null): Set<string> | null {
  return React.useMemo(() => {
    if (!id) return null;
    const set = new Set<string>([id]);
    layout.edges.forEach((e) => {
      if (e.a === id) set.add(e.b);
      if (e.b === id) set.add(e.a);
    });
    return set;
  }, [layout, id]);
}

// ─── Memoised node renderers ─────────────────────────────────────────────────

const ClusterNode = React.memo<{
  node: ClusterNodeData;
  selected: boolean;
  hovered: boolean;
  dim: boolean;
  showLabels: boolean;
  onToggle: () => void;
}>(function ClusterNode({ node, selected, hovered, dim, showLabels, onToggle }) {
  const c = CLUSTERS[node.cluster];
  const r = 36 + Math.log(Math.max(node.chunkCount, 4)) * 3;
  const glow = selected || hovered;
  return (
    <g
      transform={`translate(${node.x}, ${node.y})`}
      opacity={dim ? 0.1 : 1}
      style={glow ? { filter: "drop-shadow(0 0 12px rgba(232,162,63,0.65))" } : undefined}
    >
      <circle r={r + 10} fill={CLUSTER_FILL(node.cluster)} opacity={0.5} />
      <circle
        r={r}
        fill={CLUSTER_FILL(node.cluster)}
        stroke={selected ? "#E8A23F" : CLUSTER_COLOR(node.cluster)}
        strokeWidth={selected ? 2.5 : 1.5}
      />
      {showLabels && (
        <>
          <text textAnchor="middle" y={-4} fontSize="11" fontFamily="Inter, sans-serif" fontWeight={700} fill={NAVY}>
            {(c?.label ?? node.cluster).toUpperCase()}
          </text>
          <text textAnchor="middle" y={10} fontSize="9" fontFamily="JetBrains Mono, monospace" fill={NAVY2}>
            {node.docCount} docs · {node.chunkCount} chk
          </text>
        </>
      )}
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
});

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

const DocNode = React.memo<{
  node: DocNodeData;
  selected: boolean;
  hovered: boolean;
  dim: boolean;
  showLabels: boolean;
  theme: "light" | "dark";
  showConfHeat: boolean;
  onToggle: () => void;
}>(function DocNode({ node, selected, hovered, dim, showLabels, theme, showConfHeat, onToggle }) {
  const d = node.doc;
  const dt = DOC_TYPES[d.type];
  const r = 9;
  const baseFill = theme === "dark" ? "#0E1A2E" : "#FFFFFF";
  const glow = selected || hovered;
  return (
    <g
      transform={`translate(${node.x}, ${node.y})`}
      opacity={dim ? 0.1 : 1}
      style={{ cursor: "pointer", filter: glow ? "drop-shadow(0 0 8px rgba(232,162,63,0.7))" : undefined }}
    >
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
      {showLabels && (
        <text
          textAnchor="middle"
          y={r + 11}
          fontSize="8.5"
          fontFamily="Inter, sans-serif"
          fill={theme === "dark" ? "#B0C0D8" : NAVY2}
        >
          {truncate(d.title, 18)}
        </text>
      )}
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
});

const ChunkNode = React.memo<{
  node: ChunkNodeData;
  selected: boolean;
  hovered: boolean;
  dim: boolean;
  theme: "light" | "dark";
  showConfHeat: boolean;
}>(function ChunkNode({ node, selected, hovered, dim, theme, showConfHeat }) {
  const c = node.chunk;
  const r = 4.5;
  const glow = selected || hovered;
  return (
    <g
      transform={`translate(${node.x}, ${node.y})`}
      opacity={dim ? 0.1 : 1}
      style={glow ? { filter: "drop-shadow(0 0 6px rgba(232,162,63,0.7))" } : undefined}
    >
      <circle
        r={r + 2.5}
        fill={theme === "dark" ? "#0E1A2E" : "#F4F6FB"}
        stroke={selected ? "#E8A23F" : theme === "dark" ? "#7CA8E8" : NAVY}
        strokeWidth={selected ? 1.5 : 0.7}
      />
      <circle r={r} fill={showConfHeat ? confColor(c.confidence) : theme === "dark" ? "#3A6CB8" : ACCENT} />
    </g>
  );
});

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

const ConceptNode = React.memo<{
  node: ConceptNodeData;
  selected: boolean;
  hovered: boolean;
  dim: boolean;
  showLabels: boolean;
  theme: "light" | "dark";
}>(function ConceptNode({ node, selected, hovered, dim, showLabels, theme }) {
  const k = node.concept;
  const s = node.focal ? 24 : 16;
  const glow = selected || hovered || node.focal;
  return (
    <g
      transform={`translate(${node.x}, ${node.y})`}
      opacity={dim ? 0.1 : 1}
      style={glow ? { filter: "drop-shadow(0 0 8px rgba(232,162,63,0.65))" } : undefined}
    >
      <polygon
        points={hexPoints(s)}
        fill={theme === "dark" ? "#0E1A2E" : "#F4F6FB"}
        stroke={selected || node.focal ? "#E8A23F" : theme === "dark" ? "#7CA8E8" : NAVY}
        strokeWidth={selected || node.focal ? 1.8 : 1}
      />
      {showLabels && (
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
      )}
    </g>
  );
});

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
      return { stroke: "url(#kx-grad-contains)", dash: "", width: 0.9 };
    case "reference":
      return { stroke: "url(#kx-grad-reference)", dash: "", width: 1.1, arrow: true };
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

// ─── Canvas ──────────────────────────────────────────────────────────────────

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
  const { nodes, edges } = layout;
  const nodeMap = React.useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n] as const)), [nodes]);
  const { viewBox, zoom, panBy, zoomAt, resetView } = useViewport({
    selectedId,
    focusRoot,
    layout,
  });

  // Hover ghosting: while a node is hovered, only it + first-degree
  // neighbours stay at full opacity (item #3). Falls through to the
  // selection / search / focus dimming logic when nothing is hovered.
  const hoverNeighbors = useNeighborSet(layout, hoveredId);

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

  const isDim = React.useCallback(
    (id: string): boolean => {
      // Hover ghosting wins when active.
      if (hoverNeighbors) return !hoverNeighbors.has(id);
      if (search) {
        const q = search.toLowerCase();
        const n = nodeMap[id];
        if (!n) return false;
        let txt = "";
        if (n.kind === "doc") txt = n.doc.title + " " + n.doc.cluster + " " + n.doc.source;
        else if (n.kind === "chunk") txt = n.chunk.label + " " + n.chunk.summary + " " + n.chunk.kind;
        else if (n.kind === "concept") txt = n.concept.name + " " + n.concept.kind + " " + n.concept.syn.join(" ");
        else if (n.kind === "cluster") txt = CLUSTERS[n.cluster]?.label ?? n.cluster;
        return !txt.toLowerCase().includes(q);
      }
      if (!visibleSet) return false;
      return !visibleSet.has(id);
    },
    [hoverNeighbors, search, nodeMap, visibleSet],
  );

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

  const dispatchFocus = React.useCallback((sel: NodeSelection): void => {
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("kx-focus-root", { detail: sel }));
    }
  }, []);

  // ─── Pan + zoom event handlers ─────────────────────────────────────────────

  const svgRef = React.useRef<SVGSVGElement | null>(null);
  const panState = React.useRef<{ active: boolean; x: number; y: number; moved: boolean }>({
    active: false,
    x: 0,
    y: 0,
    moved: false,
  });

  const eventToCoord = React.useCallback(
    (e: { clientX: number; clientY: number }): { x: number; y: number } => {
      const svg = svgRef.current;
      if (!svg) return { x: 0, y: 0 };
      const rect = svg.getBoundingClientRect();
      const fx = (e.clientX - rect.left) / rect.width;
      const fy = (e.clientY - rect.top) / rect.height;
      return { x: viewBox.x + fx * viewBox.w, y: viewBox.y + fy * viewBox.h };
    },
    [viewBox],
  );

  const onPointerDown = React.useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    // Only start a pan on background drags (target === svg or rect/grid).
    const target = e.target as Element;
    if (target.tagName !== "svg" && target.tagName !== "rect") return;
    panState.current = { active: true, x: e.clientX, y: e.clientY, moved: false };
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  }, []);

  const onPointerMove = React.useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      if (!panState.current.active) return;
      const dx = e.clientX - panState.current.x;
      const dy = e.clientY - panState.current.y;
      if (Math.abs(dx) + Math.abs(dy) < 2) return;
      panState.current.moved = true;
      panState.current.x = e.clientX;
      panState.current.y = e.clientY;
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const cw = viewBox.w / rect.width;
      const ch = viewBox.h / rect.height;
      panBy(-dx * cw, -dy * ch);
    },
    [viewBox, panBy],
  );

  const onPointerUp = React.useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    panState.current.active = false;
    (e.currentTarget as Element).releasePointerCapture?.(e.pointerId);
  }, []);

  const onWheel = React.useCallback(
    (e: React.WheelEvent<SVGSVGElement>) => {
      // Only intercept when the wheel delta is meaningful — keep the
      // page scroll feel for tiny touchpad scrolls outside the canvas.
      if (e.deltaY === 0) return;
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      const { x, y } = eventToCoord(e);
      zoomAt(factor, x, y);
    },
    [zoomAt, eventToCoord],
  );

  // Block default wheel only when over the canvas — registering with
  // passive: false so preventDefault sticks.
  React.useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
    };
    svg.addEventListener("wheel", handler, { passive: false });
    return () => svg.removeEventListener("wheel", handler);
  }, []);

  // ─── LOD thresholds ───────────────────────────────────────────────────────

  const showLabels = zoom >= 0.6;
  const showArrows = zoom >= 0.4;

  // ─── Render ────────────────────────────────────────────────────────────────

  const arrowMarker = `url(#kx-arrow${theme === "dark" ? "-dark" : ""})`;

  return (
    <>
      <svg
        ref={svgRef}
        viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`}
        preserveAspectRatio="xMidYMid meet"
        style={{
          width: "100%",
          height: "100%",
          display: "block",
          touchAction: "none",
          cursor: panState.current.active ? "grabbing" : "grab",
        }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        onWheel={onWheel}
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
            <path d="M0,0 L10,5 L0,10 z" fill="#9DAEC8" />
          </marker>
          <marker
            id="kx-arrow-dark"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L10,5 L0,10 z" fill="#5B7AA8" />
          </marker>
          {/* Directional gradients for source → target edges (tech note).
              Keep userSpaceOnUse so the gradient orientation is computed
              from the actual edge endpoints, not the bounding box. */}
          {edges.map((e, i) => {
            const a = nodeMap[e.a];
            const b = nodeMap[e.b];
            if (!a || !b) return null;
            if (e.type !== "reference" && e.type !== "contains") return null;
            const start = e.type === "reference" ? "#5B7AA8" : "#A8B7CE";
            const end = e.type === "reference" ? ACCENT : "#5B7AA8";
            return (
              <linearGradient
                key={`grad-${i}`}
                id={`kx-edge-${i}`}
                gradientUnits="userSpaceOnUse"
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
              >
                <stop offset="0%" stopColor={start} stopOpacity={0.4} />
                <stop offset="100%" stopColor={end} stopOpacity={0.95} />
              </linearGradient>
            );
          })}
          {/* Aliases used by `edgeStyle`. The actual stroke for each
              edge instance is overridden below by the per-edge gradient
              so these are fallbacks for nodes/edges that don't carry an
              index-keyed gradient. */}
          <linearGradient id="kx-grad-contains" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#A8B7CE" stopOpacity={0.4} />
            <stop offset="100%" stopColor="#5B7AA8" stopOpacity={0.95} />
          </linearGradient>
          <linearGradient id="kx-grad-reference" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#5B7AA8" stopOpacity={0.4} />
            <stop offset="100%" stopColor={ACCENT} stopOpacity={0.95} />
          </linearGradient>
        </defs>
        <rect
          x={viewBox.x}
          y={viewBox.y}
          width={viewBox.w}
          height={viewBox.h}
          fill={theme === "dark" ? "#0A1628" : "#FBFCFE"}
        />
        <rect x={viewBox.x} y={viewBox.y} width={viewBox.w} height={viewBox.h} fill="url(#kx-grid)" />

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
            const hasGradient = e.type === "reference" || e.type === "contains";
            const stroke = hasGradient ? `url(#kx-edge-${i})` : s.stroke;
            return (
              <line
                key={i}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={stroke}
                strokeWidth={s.width}
                strokeDasharray={s.dash}
                opacity={dim ? 0.06 : 0.7}
                markerEnd={s.arrow && showArrows ? arrowMarker : undefined}
              />
            );
          })}
        </g>

        <g>
          {nodes.map((n) => {
            const sel = selectedId === n.id;
            const hov = hoveredId === n.id;
            const dim = isDim(n.id);
            const handleClick = (e: React.MouseEvent): void => {
              if (panState.current.moved) {
                panState.current.moved = false;
                return;
              }
              const selection = toSelection(n);
              if (e.shiftKey || e.altKey) {
                onSelect(selection);
                dispatchFocus(selection);
              } else {
                onSelect(selection);
              }
            };
            const handleDouble = (e: React.MouseEvent): void => {
              e.stopPropagation();
              dispatchFocus(toSelection(n));
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
                  <ClusterNode
                    node={n}
                    selected={sel}
                    hovered={hov}
                    dim={dim}
                    showLabels={showLabels}
                    onToggle={() => onToggleCluster(n.cluster)}
                  />
                )}
                {n.kind === "doc" && (
                  <DocNode
                    node={n}
                    selected={sel}
                    hovered={hov}
                    dim={dim}
                    showLabels={showLabels}
                    theme={theme}
                    showConfHeat={showConfHeat}
                    onToggle={() => onToggleDoc(n.doc.id)}
                  />
                )}
                {n.kind === "chunk" && (
                  <ChunkNode
                    node={n}
                    selected={sel}
                    hovered={hov}
                    dim={dim}
                    theme={theme}
                    showConfHeat={showConfHeat}
                  />
                )}
                {n.kind === "concept" && (
                  <ConceptNode
                    node={n}
                    selected={sel}
                    hovered={hov}
                    dim={dim}
                    showLabels={showLabels}
                    theme={theme}
                  />
                )}
              </g>
            );
          })}
        </g>
      </svg>
      <div className="kx-zoom-pill" aria-label="Viewport status">
        <span className="kx-mono kx-mute">ZOOM</span>
        <span className="kx-mono">{(zoom * 100).toFixed(0)}%</span>
        <button
          className="kx-zoom-btn"
          onClick={() => zoomAt(1.2, viewBox.x + viewBox.w / 2, viewBox.y + viewBox.h / 2)}
          aria-label="Zoom in"
          title="Zoom in"
        >
          +
        </button>
        <button
          className="kx-zoom-btn"
          onClick={() => zoomAt(1 / 1.2, viewBox.x + viewBox.w / 2, viewBox.y + viewBox.h / 2)}
          aria-label="Zoom out"
          title="Zoom out"
        >
          −
        </button>
        <button className="kx-zoom-btn" onClick={resetView} aria-label="Fit view" title="Fit view">
          ⤢
        </button>
      </div>
    </>
  );
};

function toSelection(n: LayoutNode): NodeSelection {
  if (n.kind === "cluster") return { kind: "cluster", id: n.cluster, cluster: n.cluster };
  if (n.kind === "doc") return { kind: "doc", id: n.doc.id, doc: n.doc };
  if (n.kind === "chunk") return { kind: "chunk", id: n.chunk.id, chunk: n.chunk };
  return { kind: "concept", id: n.concept.id, concept: n.concept };
}
