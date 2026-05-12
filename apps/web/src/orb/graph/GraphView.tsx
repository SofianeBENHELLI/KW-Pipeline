/**
 * GraphView — page shell rendered at /kf/graph.
 *
 *   ┌──────────────────────────────────────────────────────┐
 *   │ Filter toolbar (segmented)                           │
 *   ├──────────────────────────────────────────┬───────────┤
 *   │ <GraphCanvas>                            │ Inspector │
 *   │                                          │  (360px,  │
 *   │                                          │   only    │
 *   │                                          │   when    │
 *   │                                          │   sel)    │
 *   └──────────────────────────────────────────┴───────────┘
 */

import { useMemo, useState } from "react";
import type { ReactElement } from "react";
import { useNavigate } from "react-router-dom";

import "./graph.css";
import { Btn, OrbI } from "../index";
import { GraphCanvas } from "./GraphCanvas";
import { GraphInspector } from "./GraphInspector";
import {
  neighborsOf,
  useFilteredGraph,
  useKnowledgeGraph,
  type GraphFilter,
} from "../hooks/useKnowledgeGraph";

const FILTERS: Array<{ id: GraphFilter; label: string }> = [
  { id: "all",          label: "All" },
  { id: "topics",       label: "Topics" },
  { id: "entities",     label: "Entities" },
  { id: "chunks",       label: "Chunks" },
  { id: "relations",    label: "Relations" },
  { id: "sourcebacked", label: "Source-backed" },
];

export function GraphView(): ReactElement {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<GraphFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const live = useKnowledgeGraph();
  const filtered = useFilteredGraph(filter, live.nodes, live.edges);

  const selectedNode = useMemo(
    () =>
      selectedId
        ? filtered.nodes.find((n) => n.id === selectedId) ?? null
        : null,
    [filtered.nodes, selectedId],
  );

  const neighbors = useMemo(
    () =>
      selectedId
        ? neighborsOf(selectedId, filtered.edges)
        : { incoming: [], outgoing: [] },
    [filtered.edges, selectedId],
  );

  return (
    <section className="kf-gv" aria-label="Knowledge Forge — Graph">
      <header className="kf-gv__toolbar" role="toolbar" aria-label="Graph filter">
        <div className="kf-gv__toolbar-l">
          <span className="orb-mono kf-gv__toolbar-h">Filter</span>
          <div className="kf-gv__filters" role="tablist" aria-label="Filter graph">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                type="button"
                role="tab"
                aria-selected={filter === f.id}
                className={`kf-gv__filter ${filter === f.id ? "is-on" : ""}`}
                onClick={() => {
                  setFilter(f.id);
                  setSelectedId(null);
                }}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
        <div className="kf-gv__toolbar-r">
          <span className="orb-mono kf-gv__toolbar-stat">
            {filtered.nodes.length} nodes · {filtered.edges.length} edges
          </span>
          <Btn xs kind="ghost" icon={OrbI.refresh} onClick={live.refetch}>
            Refresh
          </Btn>
        </div>
      </header>

      <div className="kf-gv__body">
        <div className="kf-gv__canvaswrap">
          {live.status === "loading" && (
            <div className="kf-gv__state">Loading knowledge graph…</div>
          )}
          {live.status === "error" && (
            <div className="kf-gv__state kf-gv__state--err" role="alert">
              Failed to load graph
              {live.error?.message ? <>: <code>{live.error.message}</code></> : null}
            </div>
          )}
          {live.status === "empty" && (
            <div className="kf-gv__state">
              No graph projected yet — validate at least one document on
              the Review tab to populate it.
            </div>
          )}
          {live.status === "ok" && (
            <GraphCanvas
              nodes={filtered.nodes}
              edges={filtered.edges}
              selectedId={selectedId}
              onSelect={(id) => setSelectedId(id)}
            />
          )}
        </div>

        {selectedNode && (
          <GraphInspector
            node={selectedNode}
            incoming={neighbors.incoming}
            outgoing={neighbors.outgoing}
            onClose={() => setSelectedId(null)}
            onOpenInReview={(docId) => navigate(`/kf/review/${docId}`)}
          />
        )}
      </div>
    </section>
  );
}
