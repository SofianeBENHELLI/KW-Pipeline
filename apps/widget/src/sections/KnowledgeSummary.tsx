import React, { useEffect, useState } from "react";

import { ApiError, getKnowledgeGraph } from "../api/client";

interface Props {
  apiBaseUrl: string;
  refreshTick: number;
}

interface Counts {
  nodes: number;
  edges: number;
  byKind: Record<string, number>;
  truncated: boolean;
}

type State =
  | { kind: "loading" }
  | { kind: "ok"; counts: Counts }
  | { kind: "err"; message: string };

const PAGE_LIMIT = 200;
const MAX_PAGES = 10;

export const KnowledgeSummary: React.FC<Props> = ({ apiBaseUrl, refreshTick }) => {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    const run = async () => {
      setState({ kind: "loading" });
      try {
        let nodes = 0;
        let edges = 0;
        const byKind: Record<string, number> = {};
        let cursor: string | null = null;
        let pages = 0;
        do {
          // eslint-disable-next-line no-await-in-loop -- pagination is sequential by design
          const page = await getKnowledgeGraph({
            limit: PAGE_LIMIT,
            cursor: cursor ?? undefined,
            baseUrl: apiBaseUrl,
            signal: controller.signal,
          });
          nodes += page.nodes.length;
          edges += page.edges.length;
          for (const n of page.nodes) {
            byKind[n.kind] = (byKind[n.kind] ?? 0) + 1;
          }
          cursor = page.next_cursor;
          pages += 1;
        } while (cursor && pages < MAX_PAGES);

        if (!cancelled) {
          setState({
            kind: "ok",
            counts: { nodes, edges, byKind, truncated: cursor !== null },
          });
        }
      } catch (error) {
        if (cancelled) return;
        if ((error as { name?: string })?.name === "AbortError") return;
        const message =
          error instanceof ApiError
            ? `${error.code}: ${error.detail}`
            : error instanceof Error
              ? error.message
              : "Failed to load knowledge graph";
        setState({ kind: "err", message });
      }
    };

    void run();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [apiBaseUrl, refreshTick]);

  return (
    <section className="kw-card" aria-label="Knowledge layer">
      <h3 className="kw-card__title">Knowledge layer</h3>
      {state.kind === "loading" && <div className="kw-status">Loading…</div>}
      {state.kind === "err" && <div className="kw-error">{state.message}</div>}
      {state.kind === "ok" && (
        <>
          <div className="kw-counts">
            <div className="kw-counts__item">
              <span className="kw-counts__num">{state.counts.nodes}</span>
              <span className="kw-counts__label">Nodes</span>
            </div>
            <div className="kw-counts__item">
              <span className="kw-counts__num">{state.counts.edges}</span>
              <span className="kw-counts__label">Edges</span>
            </div>
            {Object.entries(state.counts.byKind)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 4)
              .map(([kind, count]) => (
                <div key={kind} className="kw-counts__item">
                  <span className="kw-counts__num">{count}</span>
                  <span className="kw-counts__label">{kind}</span>
                </div>
              ))}
          </div>
          {state.counts.truncated && (
            <div className="kw-status" style={{ marginTop: 4 }}>
              Showing first {MAX_PAGES * PAGE_LIMIT} nodes — graph is larger.
            </div>
          )}
        </>
      )}
    </section>
  );
};
