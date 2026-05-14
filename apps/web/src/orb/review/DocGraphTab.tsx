/**
 * DocGraphTab — per-document graph tab in the Review Workspace.
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
 *
 * Scope: this component is intentionally scoped to a single document.
 * Knowledge Forge has no corpus-wide graph surface — that's the scope
 * of the Knowledge Explorer app (`apps/explorer`). The tab simply
 * renders the same `GraphCanvas` / `GraphInspector` primitives the
 * Linked View uses, fed by `getDocumentGraph(documentId)`.
 */

import { useMemo, useState } from "react";
import type { ReactElement } from "react";

import { Btn, OrbI } from "../index";
import { GraphCanvas } from "../graph/GraphCanvas";
import { GraphInspector } from "../graph/GraphInspector";
import "../graph/graph.css";
import {
  neighborsOf,
  useDocumentGraph,
  useFilteredGraph,
  type GraphFilter,
} from "../hooks/useDocumentGraph";

const FILTERS: Array<{ id: GraphFilter; label: string }> = [
  { id: "all",          label: "All" },
  { id: "topics",       label: "Topics" },
  { id: "entities",     label: "Entities" },
  { id: "chunks",       label: "Chunks" },
  { id: "relations",    label: "Relations" },
  { id: "sourcebacked", label: "Source-backed" },
];

export interface DocGraphTabProps {
  /** Active document id. ``null`` when no doc is selected yet. */
  documentId: string | null;
}

export function DocGraphTab({ documentId }: DocGraphTabProps): ReactElement {
  const [filter, setFilter] = useState<GraphFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const live = useDocumentGraph(documentId);
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

  if (!documentId) {
    return (
      <section
        className="kf-gv"
        aria-label="Document graph (no document selected)"
      >
        <div className="kf-gv__state">
          Pick a document from the rail to view its knowledge graph.
        </div>
      </section>
    );
  }

  return (
    <section className="kf-gv" aria-label="Document graph">
      <header
        className="kf-gv__toolbar"
        role="toolbar"
        aria-label="Graph filter"
      >
        <div className="kf-gv__toolbar-l">
          <span className="orb-mono kf-gv__toolbar-h">Filter</span>
          <div
            className="kf-gv__filters"
            role="tablist"
            aria-label="Filter graph"
          >
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
            <div className="kf-gv__state">Loading document graph…</div>
          )}
          {live.status === "error" && (
            <div className="kf-gv__state kf-gv__state--err" role="alert">
              Failed to load graph
              {live.error?.message ? (
                <>
                  : <code>{live.error.message}</code>
                </>
              ) : null}
            </div>
          )}
          {live.status === "empty" && (
            <div className="kf-gv__state">
              No graph projected for this document yet — validate the
              latest version on the Pipeline & FSM tab to populate it.
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
          />
        )}
      </div>
    </section>
  );
}
