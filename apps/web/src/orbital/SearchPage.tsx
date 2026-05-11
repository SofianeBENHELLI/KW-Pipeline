import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, searchKnowledgeChunks } from "../api/client";
import type { ApiChunkSearchResponse } from "../api/types";

import { Icon, Kbd } from "./atoms";

const SEARCH_DEBOUNCE_MS = 300;

export interface SearchPageProps {
  onOpenDocument: (id: string) => void;
  onClose: () => void;
}

/**
 * `SearchPanel` from the mockup ported to the real backend.
 * `GET /knowledge/search` with `VOYAGE_API_KEY` gating; 503 responses
 * render the disabled banner with the remediation hint.
 */
export function SearchPage({ onOpenDocument, onClose }: SearchPageProps) {
  const [q, setQ] = useState("");
  const [topK, setTopK] = useState(10);
  const [data, setData] = useState<ApiChunkSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [disabled, setDisabled] = useState(false);
  const debounceRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(
    async (query: string, limit: number) => {
      if (!query.trim()) {
        setData(null);
        setError(null);
        setDisabled(false);
        return;
      }
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      setError(null);
      try {
        const response = await searchKnowledgeChunks(query.trim(), { limit, signal: controller.signal });
        if (controller.signal.aborted) return;
        setData(response);
        setDisabled(false);
      } catch (err) {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 503) {
          setDisabled(true);
          setData(null);
        } else {
          const message =
            err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
          setError(message);
          setData(null);
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => run(q, topK), SEARCH_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, [q, topK, run]);

  const results = data?.results ?? [];

  return (
    <div className="orb-app sp">
      <header className="sp-h">
        <span style={{ display: "inline-flex" }}>
          <Icon name="spark" />
        </span>
        <span style={{ fontWeight: 600 }}>Vector search</span>
        <span className="sp-pill orb-mono">
          <span className="dot" style={{ background: disabled ? "var(--orb-fg-dim)" : "var(--orb-ok)" }}></span>
          {data?.embedding_model ?? "voyage-3"} · {data?.query_embedding_dim ?? "1024"}d
        </span>
        <span style={{ flex: 1 }}></span>
        <Kbd>esc</Kbd>
        <button className="sp-x" onClick={onClose} aria-label="Close search panel">
          <Icon name="x" />
        </button>
      </header>

      <div className="sp-querybar">
        <span className="sp-sicon">
          <Icon name="search" />
        </span>
        <input
          className="sp-q"
          placeholder="Ask in natural language…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select className="sp-k" value={topK} onChange={(e) => setTopK(+e.target.value)}>
          <option value={5}>top-5</option>
          <option value={10}>top-10</option>
          <option value={20}>top-20</option>
        </select>
        <button className="sp-mode">across corpus ▾</button>
      </div>

      <div className="sp-meta orb-mono">
        <span>
          {loading ? "searching…" : `${results.length} chunks${results[0] ? ` · ${results[0].score.toFixed(3)} best` : ""}`}
        </span>
        <span>·</span>
        <span>300ms debounce</span>
        {error && (
          <>
            <span>·</span>
            <span style={{ color: "var(--orb-err-fg)" }}>{error}</span>
          </>
        )}
        <span style={{ flex: 1 }}></span>
        <span>
          <Icon name="bolt" /> {data?.embedding_model ?? ""}
        </span>
      </div>

      <div className="sp-body orb-scroll">
        {disabled && (
          <div style={{ padding: 24, color: "var(--orb-err-fg)", background: "var(--orb-err-bg)", borderRadius: 6, fontSize: 13 }}>
            <b>Vector search disabled.</b> Set <code className="orb-mono">VOYAGE_API_KEY</code> on the backend
            and restart Orbital to enable Phase 3.
          </div>
        )}
        {!disabled && results.length === 0 && !loading && q && (
          <div style={{ padding: 24, color: "var(--orb-fg-muted)", fontSize: 13 }}>
            No matches for <span className="orb-mono">"{q}"</span>.
          </div>
        )}
        {results.map((h, i) => (
          <div key={h.chunk_id} className="sp-hit">
            <div className="sp-hit-h">
              <span className="sp-rank">{String(i + 1).padStart(2, "0")}</span>
              <span className="sp-score orb-mono">{h.score.toFixed(3)}</span>
              <span className="sp-scorebar">
                <span style={{ width: `${Math.min(100, h.score * 100)}%` }}></span>
              </span>
              <span className="sp-hit-doc">{h.document_id.slice(0, 8)}</span>
              <span className="orb-mono sp-hit-meta">
                {h.chunk_id.slice(0, 12)} · v{h.version_id.slice(0, 6)}
              </span>
              <span style={{ flex: 1 }}></span>
              <button className="sp-jump" onClick={() => onOpenDocument(h.document_id)}>
                jump <Icon name="ext" />
              </button>
            </div>
            <div className="sp-hit-body">"{h.snippet ?? "(no snippet)"}"</div>
          </div>
        ))}
      </div>

      <footer className="sp-foot orb-mono">
        <span>GET /knowledge/search?q=…&amp;limit={topK}</span>
        <span style={{ flex: 1 }}></span>
        <span>gated by VOYAGE_API_KEY</span>
        <span style={{ color: disabled ? "var(--orb-err)" : "var(--orb-ok)" }}>
          ● {disabled ? "disabled" : "enabled"}
        </span>
      </footer>
    </div>
  );
}
