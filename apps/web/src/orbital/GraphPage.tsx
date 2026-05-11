import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, getDocumentGraph, getKnowledgeGraph } from "../api/client";
import type {
  ApiGraphEdge,
  ApiGraphNode,
  ApiKnowledgeGraphProjection,
} from "../api/types";

import { Btn, Icon } from "./atoms";

type FilterMode = "all" | "chunks" | "topics" | "entities" | "relations" | "sb";

const MODES: { id: FilterMode; label: string }[] = [
  { id: "all", label: "All" },
  { id: "topics", label: "Topics" },
  { id: "entities", label: "Entities" },
  { id: "chunks", label: "Chunks" },
  { id: "relations", label: "Relations" },
  { id: "sb", label: "Source-backed" },
];

const NODE_COLORS: Record<string, string> = {
  document: "var(--orb-fg)",
  version: "var(--orb-info)",
  section: "var(--orb-purple)",
  chunk: "var(--orb-fg-muted)",
  topic: "var(--orb-warn)",
  entity: "var(--orb-ok)",
};

const NODE_LABELS: Record<string, string> = {
  document: "Document",
  version: "Version",
  section: "Section",
  chunk: "Chunk",
  topic: "Topic",
  entity: "Entity",
};

const RELATION_KINDS = new Set(["related_to", "shares_keyword", "same_topic_as"]);

interface Positioned {
  id: string;
  kind: string;
  label: string;
  x: number;
  y: number;
  r: number;
  sb: boolean;
}

export interface GraphPageProps {
  /** When set, scopes the graph to that document; else fetches the full graph. */
  documentId?: string | null;
  onOpenDocument: (id: string) => void;
}

/**
 * `GraphView` from the mockup. SVG force-graph-style canvas + mode bar
 * + inspector. Real data comes from `GET /documents/{id}/graph` when a
 * document is selected, else `GET /knowledge/graph` (cursor-paginated;
 * we just take the first page for the canvas). Node layout is computed
 * deterministically from id hashes so the picture is stable.
 */
export function GraphPage({ documentId, onOpenDocument }: GraphPageProps) {
  const [projection, setProjection] = useState<ApiKnowledgeGraphProjection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<FilterMode>("all");
  const [sel, setSel] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      if (documentId) {
        const data = await getDocumentGraph(documentId);
        if (!controller.signal.aborted) setProjection(data);
      } else {
        const page = await getKnowledgeGraph(200);
        if (!controller.signal.aborted) {
          setProjection({
            nodes: page.nodes,
            edges: page.edges,
            document_id: "",
            version_id: "",
          } as unknown as ApiKnowledgeGraphProjection);
        }
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      setError(message);
      setProjection(null);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    refresh();
    return () => abortRef.current?.abort();
  }, [refresh]);

  const filtered = useMemo(() => {
    if (!projection) return { nodes: [] as ApiGraphNode[], edges: [] as ApiGraphEdge[] };
    let n = projection.nodes;
    if (mode === "chunks") n = n.filter((x) => ["document", "version", "section", "chunk"].includes(x.kind));
    if (mode === "topics") n = n.filter((x) => ["document", "section", "chunk", "topic"].includes(x.kind));
    if (mode === "entities") n = n.filter((x) => ["document", "chunk", "entity"].includes(x.kind));
    if (mode === "relations") n = n.filter((x) => ["topic", "entity", "section"].includes(x.kind));
    if (mode === "sb")
      n = n.filter((x) => x.kind === "document" || x.kind === "version" || x.properties?.source_reference_count != null);
    const ids = new Set(n.map((x) => x.id));
    const e = projection.edges.filter((edge) => ids.has(edge.source_id) && ids.has(edge.target_id));
    return { nodes: n, edges: e };
  }, [projection, mode]);

  const positioned = useMemo<Positioned[]>(() => {
    return filtered.nodes.map((node) => {
      // Deterministic layout from id hash so the picture is stable across renders.
      let hash = 0;
      for (const ch of node.id) hash = (hash * 31 + ch.charCodeAt(0)) & 0xffffffff;
      const ringByKind: Record<string, [number, number]> = {
        document: [50, 90],
        version: [50, 50],
        section: [30, 30],
        chunk: [50, 18],
        topic: [50, 14],
        entity: [50, 22],
      };
      const center = ringByKind[node.kind] ?? [50, 26];
      const angle = ((hash & 0xffff) / 0xffff) * Math.PI * 2;
      const x = center[0] + Math.cos(angle) * center[1];
      const y = (node.kind === "document" ? 50 : node.kind === "version" ? 90 : 50 + Math.sin(angle) * (center[1] * 1.6)) + 0;
      const r = node.kind === "document" ? 14 : node.kind === "version" ? 9 : node.kind === "topic" ? 11 : node.kind === "entity" ? 6 : node.kind === "section" ? 8 : 5;
      const sb = !!(node.properties && node.properties.source_reference_count != null);
      return { id: node.id, kind: node.kind, label: node.label, x, y, r, sb };
    });
  }, [filtered.nodes]);

  const selNode = sel ? filtered.nodes.find((n) => n.id === sel) : null;
  const selEdges = sel ? filtered.edges.filter((e) => e.source_id === sel || e.target_id === sel) : [];

  const kindCounts = useMemo(() => {
    const m: Record<string, number> = {};
    for (const n of filtered.nodes) m[n.kind] = (m[n.kind] ?? 0) + 1;
    return m;
  }, [filtered.nodes]);

  const W = 100;
  const H = 230;

  return (
    <div className="orb-app gvw">
      <header className="gvw-top">
        <div className="gvw-crumbs">
          <span>{documentId ? "Review" : "Corpus"}</span>
          <span>›</span>
          {documentId && (
            <>
              <span className="orb-mono">{documentId.slice(0, 8)}</span>
              <span>›</span>
            </>
          )}
          <span className="gvw-cur">Knowledge graph</span>
        </div>
        <div style={{ flex: 1 }}></div>
        <Btn xs icon={<Icon name="refresh" />} onClick={() => void refresh()}>
          {loading ? "Loading…" : "Refresh"}
        </Btn>
      </header>

      <div className="gvw-modebar">
        <div className="gvw-modes">
          {MODES.map((m) => (
            <button
              key={m.id}
              className={`gvw-mode ${mode === m.id ? "is-active" : ""}`}
              onClick={() => setMode(m.id)}
            >
              {m.label}
              <span className="gvw-mode-c">
                {m.id === "all" && projection ? projection.nodes.length : ""}
                {m.id === "chunks" && projection ? projection.nodes.filter((n) => n.kind === "chunk").length : ""}
                {m.id === "topics" && projection ? projection.nodes.filter((n) => n.kind === "topic").length : ""}
                {m.id === "entities" && projection ? projection.nodes.filter((n) => n.kind === "entity").length : ""}
                {m.id === "relations" && projection ? projection.edges.filter((e) => RELATION_KINDS.has(e.kind)).length : ""}
                {m.id === "sb" && projection ? projection.nodes.filter((n) => n.properties?.source_reference_count != null).length : ""}
              </span>
            </button>
          ))}
        </div>
        <div style={{ flex: 1 }}></div>
        <div className="gvw-legend">
          {Object.entries(NODE_COLORS).map(([k, c]) => (
            <span key={k} className="gvw-leg-item">
              <span className="gvw-leg-dot" style={{ background: c }}></span>
              <span>{NODE_LABELS[k] ?? k}</span>
              <span className="orb-mono gvw-leg-n">{kindCounts[k] ?? 0}</span>
            </span>
          ))}
        </div>
      </div>

      <div className="gvw-body">
        <div className="gvw-canvas">
          <div className="gvw-grid"></div>

          <svg
            viewBox={`0 -20 ${W} ${H}`}
            preserveAspectRatio="xMidYMid meet"
            className="gvw-svg"
            onClick={() => setSel(null)}
          >
            <defs>
              <marker id="arrow" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="3" markerHeight="3" orient="auto">
                <path d="M0,0 L0,6 L6,3 z" fill="var(--orb-fg-faint)" />
              </marker>
            </defs>
            <g>
              {filtered.edges.map((edge, i) => {
                const na = positioned.find((n) => n.id === edge.source_id);
                const nb = positioned.find((n) => n.id === edge.target_id);
                if (!na || !nb) return null;
                const hl = sel === edge.source_id || sel === edge.target_id;
                const dashed = RELATION_KINDS.has(edge.kind);
                return (
                  <line
                    key={`${edge.id}-${i}`}
                    x1={na.x}
                    y1={na.y}
                    x2={nb.x}
                    y2={nb.y}
                    stroke={hl ? "var(--orb-fg)" : "var(--orb-rule)"}
                    strokeWidth={hl ? 0.55 : 0.3}
                    strokeDasharray={dashed ? "1 1.2" : undefined}
                  />
                );
              })}
            </g>
            <g>
              {positioned.map((n) => {
                const isSel = n.id === sel;
                const isNeighbor = filtered.edges.some(
                  (e) => (e.source_id === sel && e.target_id === n.id) || (e.target_id === sel && e.source_id === n.id),
                );
                const r = n.r * 0.6;
                return (
                  <g
                    key={n.id}
                    onClick={(e) => {
                      e.stopPropagation();
                      setSel(n.id);
                    }}
                    style={{ cursor: "pointer" }}
                  >
                    {isSel && (
                      <circle cx={n.x} cy={n.y} r={r + 3} fill="none" stroke="var(--orb-fg)" strokeWidth="0.6" />
                    )}
                    <circle
                      cx={n.x}
                      cy={n.y}
                      r={r}
                      fill={NODE_COLORS[n.kind] ?? "var(--orb-fg-muted)"}
                      opacity={sel && !isSel && !isNeighbor ? 0.32 : 1}
                      stroke={isSel ? "var(--orb-bg)" : "none"}
                      strokeWidth={0.5}
                    />
                    {(n.kind === "document" || n.kind === "topic" || isSel) && (
                      <text
                        x={n.x}
                        y={n.y + r + 3.5}
                        textAnchor="middle"
                        fontSize="2.4"
                        fontFamily="var(--orb-font-mono)"
                        fill="var(--orb-fg-muted)"
                      >
                        {n.label?.slice(0, 24)}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          </svg>

          <div className="gvw-floatstats orb-mono">
            <span>
              nodes <b>{filtered.nodes.length}</b>
            </span>
            <span>
              edges <b>{filtered.edges.length}</b>
            </span>
            <span>
              mode <b>{mode}</b>
            </span>
            <span style={{ color: projection && projection.nodes.length > 0 ? "var(--orb-ok)" : "var(--orb-warn)" }}>
              ● projection · {projection && projection.nodes.length > 0 ? "COMPLETED" : loading ? "LOADING" : "EMPTY"}
            </span>
          </div>
        </div>

        <aside className="gvw-inspector">
          {error && (
            <div style={{ color: "var(--orb-err-fg)", fontSize: 12 }}>Failed to load graph: {error}</div>
          )}
          {!error && !selNode ? (
            <div className="gvw-empty">
              <div className="gvw-empty-h">No selection</div>
              <p>Click a node to inspect its metadata, neighbors, and source spans.</p>
              {documentId && (
                <p style={{ marginTop: 8, fontSize: 11 }}>
                  Open in <button className="orb-btn orb-btn--ghost orb-btn--xs" onClick={() => onOpenDocument(documentId)}>review workspace</button>
                </p>
              )}
            </div>
          ) : selNode ? (
            <>
              <div className="gvw-ihead">
                <span
                  className="gvw-ikind"
                  style={{ background: NODE_COLORS[selNode.kind] ?? "var(--orb-fg-muted)" }}
                >
                  {(NODE_LABELS[selNode.kind] ?? selNode.kind).toUpperCase()}
                </span>
              </div>
              <h2 className="gvw-ititle">{selNode.label}</h2>
              <div className="gvw-iid">id={selNode.id.slice(0, 20)}</div>

              <div className="gvw-sec">
                <span className="orb-section-h">Properties</span>
                <div className="gvw-kv">
                  {Object.entries(selNode.properties ?? {}).map(([k, v]) => (
                    <div key={k}>
                      <span>{k}</span>
                      <span>{Array.isArray(v) ? v.join(", ") : v === null ? "—" : String(v)}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="gvw-sec">
                <span className="orb-section-h">Neighbors ({selEdges.length})</span>
                <ul className="gvw-nbrs">
                  {selEdges.slice(0, 8).map((e, i) => {
                    const otherId = e.source_id === sel ? e.target_id : e.source_id;
                    const other = filtered.nodes.find((n) => n.id === otherId);
                    if (!other) return null;
                    return (
                      <li key={`${e.id}-${i}`} onClick={() => setSel(other.id)}>
                        <span className="gvw-nbr-edge">{e.kind}</span>
                        <span className="gvw-nbr-name">
                          <span
                            className="gvw-leg-dot"
                            style={{ background: NODE_COLORS[other.kind] ?? "var(--orb-fg-muted)" }}
                          ></span>
                          <span>{other.label}</span>
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
