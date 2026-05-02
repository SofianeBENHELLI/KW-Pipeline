import React, { useEffect, useState } from "react";

import { ApiError, getKnowledgeGraph } from "../api/client";
import { Icon } from "../components/icons";
import { SectionHeader } from "../components/SectionHeader";

interface Props {
  apiBaseUrl: string;
  refreshTick: number;
}

interface Counts {
  nodes: number;
  edges: number;
  byKind: Record<string, number>;
  truncated: boolean;
  pagesWalked: number;
}

type State =
  | { kind: "loading" }
  | { kind: "ok"; counts: Counts }
  | { kind: "err"; message: string };

const PAGE_LIMIT = 200;
const MAX_PAGES = 10;

function formatNumber(n: number): string {
  return n.toLocaleString("en-US");
}

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
            counts: {
              nodes,
              edges,
              byKind,
              truncated: cursor !== null,
              pagesWalked: pages,
            },
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

  const meta =
    state.kind === "ok"
      ? `walked ${state.counts.pagesWalked} ${state.counts.pagesWalked === 1 ? "page" : "pages"} · ${formatNumber(state.counts.nodes)} nodes`
      : undefined;

  return (
    <section className="kw-section" aria-label="Knowledge layer">
      <SectionHeader icon="graph" title="Knowledge layer" meta={meta} />

      {state.kind === "loading" && <div className="kw-status">Loading…</div>}
      {state.kind === "err" && <div className="kw-error">{state.message}</div>}

      {state.kind === "ok" && (
        <>
          <div className="kw-kg-hero">
            <div className="kw-kg-hero__stat">
              <div className="kw-kg-hero__num">{formatNumber(state.counts.nodes)}</div>
              <div className="kw-kg-hero__lbl">Nodes</div>
            </div>
            <div className="kw-kg-hero__stat">
              <div className="kw-kg-hero__num">{formatNumber(state.counts.edges)}</div>
              <div className="kw-kg-hero__lbl">Edges</div>
            </div>
          </div>

          {Object.keys(state.counts.byKind).length > 0 && (
            <div className="kw-kg-grid">
              {Object.entries(state.counts.byKind)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 4)
                .map(([kind, count]) => (
                  <div key={kind} className="kw-kg-tile">
                    <span className="kw-kg-tile__num">{formatNumber(count)}</span>
                    <span className="kw-kg-tile__lbl">{kind}</span>
                  </div>
                ))}
            </div>
          )}

          {state.counts.truncated && (
            <div className="kw-kg-note">
              <Icon name="info" size={12} />
              Showing first {formatNumber(MAX_PAGES * PAGE_LIMIT)} nodes — graph is larger.
            </div>
          )}
        </>
      )}
    </section>
  );
};
