import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, getDocumentGraph } from "../api/client";
import type { ApiGraphEdge, ApiGraphNode, ApiKnowledgeGraphProjection } from "../api/types";
import {
  EDGE_KIND_LABELS,
  FILTER_OPTIONS,
  type FilterMode,
  NODE_KIND_COLORS,
  NODE_KIND_LABELS,
  filterProjection,
} from "../features/graph/types";
import { Btn, Card, Mono, SectionHeading } from "../ui/orb";
import { MetaRow } from "../ui/orb/atoms";

export interface GraphPanelProps {
  documentId: string;
  /** Bumped by the parent on every mutation so the panel refetches. */
  refreshKey?: number | string;
}

/**
 * Phase-4 knowledge-graph panel — reuses the pure `filterProjection`
 * logic from `features/graph/types` so the filter semantics stay in
 * lock-step with the legacy NVL canvas. We render a tabular node list
 * + edge list under the six-mode filter toolbar; the NVL canvas
 * theming spike (per docs/roadmap/orbital-redesign.md §10) is a
 * Phase-4 follow-up — operators still get the same information
 * surface here, just in tabular form.
 */
export function GraphPanel({ documentId, refreshKey }: GraphPanelProps) {
  const [projection, setProjection] = useState<ApiKnowledgeGraphProjection | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<FilterMode>("all");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const data = await getDocumentGraph(documentId);
      if (!controller.signal.aborted) setProjection(data);
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
  }, [refresh, refreshKey]);

  const filtered = useMemo(() => {
    if (!projection) return { nodes: [] as ApiGraphNode[], edges: [] as ApiGraphEdge[] };
    return filterProjection({ nodes: projection.nodes, edges: projection.edges }, mode);
  }, [projection, mode]);

  const selectedNode = useMemo(
    () => filtered.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [filtered.nodes, selectedNodeId],
  );

  if (loading && !projection) {
    return <p className="orb-review__placeholder">Loading knowledge graph…</p>;
  }
  if (error) {
    return (
      <div className="orb-review__placeholder orb-review__placeholder--error" role="alert">
        Failed to load knowledge graph: {error}
      </div>
    );
  }
  if (!projection || projection.nodes.length === 0) {
    return (
      <p className="orb-review__placeholder">
        No graph projected yet. Validate this version to populate the knowledge layer.
      </p>
    );
  }

  return (
    <div className="orb-graph">
      <div className="orb-graph__toolbar" role="tablist" aria-label="Filter modes">
        {FILTER_OPTIONS.map((option) => {
          const active = option.id === mode;
          return (
            <Btn
              key={option.id}
              kind={active ? "primary" : "ghost"}
              size="xs"
              role="tab"
              aria-selected={active}
              onClick={() => {
                setMode(option.id);
                setSelectedNodeId(null);
              }}
            >
              {option.label}
            </Btn>
          );
        })}
        <span className="orb-graph__counts orb-mono">
          {filtered.nodes.length} nodes · {filtered.edges.length} edges
        </span>
      </div>

      <div className="orb-graph__split">
        <div className="orb-graph__list-wrap">
          <SectionHeading>Nodes</SectionHeading>
          <ul className="orb-graph__list orb-scroll" role="listbox" aria-label="Filtered nodes">
            {filtered.nodes.length === 0 && (
              <li className="orb-graph__empty">No nodes match this filter.</li>
            )}
            {filtered.nodes.map((node) => {
              const active = node.id === selectedNodeId;
              const color = NODE_KIND_COLORS[node.kind];
              return (
                <li key={node.id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={active}
                    className={`orb-graph__node ${active ? "is-active" : ""}`.trim()}
                    onClick={() => setSelectedNodeId(node.id)}
                  >
                    <span className="orb-graph__node-dot" style={{ background: color }} aria-hidden="true" />
                    <span className="orb-graph__node-label">{node.label ?? node.id}</span>
                    <span className="orb-graph__node-kind orb-mono">{NODE_KIND_LABELS[node.kind]}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="orb-graph__inspector">
          <SectionHeading>Inspector</SectionHeading>
          {selectedNode ? (
            <Card className="orb-review__card">
              <MetaRow label="id">
                <Mono>{selectedNode.id}</Mono>
              </MetaRow>
              <MetaRow label="kind">{NODE_KIND_LABELS[selectedNode.kind]}</MetaRow>
              {selectedNode.label && <MetaRow label="label">{selectedNode.label}</MetaRow>}
              {Object.entries(selectedNode.properties ?? {}).map(([key, value]) => (
                <MetaRow key={key} label={key}>
                  {Array.isArray(value) ? (
                    <Mono>{value.join(", ")}</Mono>
                  ) : value === null ? (
                    <span style={{ color: "var(--orb-fg-faint)" }}>null</span>
                  ) : (
                    <span>{String(value)}</span>
                  )}
                </MetaRow>
              ))}
            </Card>
          ) : (
            <p className="orb-review__placeholder">Select a node to inspect its properties.</p>
          )}

          {filtered.edges.length > 0 && (
            <>
              <SectionHeading>Edges ({filtered.edges.length})</SectionHeading>
              <ul className="orb-graph__edges orb-scroll">
                {filtered.edges.slice(0, 50).map((edge) => (
                  <li key={edge.id} className="orb-graph__edge orb-mono">
                    <span>{edge.source_id.slice(0, 8)}</span>
                    <span className="orb-graph__edge-kind">
                      —{EDGE_KIND_LABELS[edge.kind] ?? edge.kind}→
                    </span>
                    <span>{edge.target_id.slice(0, 8)}</span>
                  </li>
                ))}
                {filtered.edges.length > 50 && (
                  <li className="orb-graph__edge orb-mono">… {filtered.edges.length - 50} more</li>
                )}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
