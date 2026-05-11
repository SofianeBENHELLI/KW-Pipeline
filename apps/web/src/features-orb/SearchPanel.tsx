import { useEffect, useRef, useState } from "react";

import { ApiError, searchKnowledgeChunks } from "../api/client";
import type { ApiChunkSearchResponse } from "../api/types";
import { Card, Icon, Mono } from "../ui/orb";
import { Input } from "../ui/orb/atoms";

const SEARCH_DEBOUNCE_MS = 300;

export interface OrbSearchPanelProps {
  onSelectResult?: (documentId: string) => void;
}

/**
 * Phase-5 vector search panel — slide-out on the shell's right edge.
 * 300ms debounce; AbortController-cancelled in-flight requests. Handles
 * the 503 KW_VECTOR_SEARCH_DISABLED case with an explicit remediation
 * banner so the reviewer knows it's a config issue not a bug.
 */
export function OrbSearchPanel({ onSelectResult }: OrbSearchPanelProps) {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [data, setData] = useState<ApiChunkSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [disabled, setDisabled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    if (!debounced) {
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
    void (async () => {
      try {
        const result = await searchKnowledgeChunks(debounced, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setData(result);
          setDisabled(false);
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 503) {
          setDisabled(true);
          setData(null);
          setError(null);
        } else {
          const message =
            err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
          setError(message);
          setData(null);
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [debounced]);

  return (
    <div className="orb-aside">
      <div className="orb-aside__head">
        <span className="orb-aside__title">Vector search</span>
        <span className="orb-aside__meta orb-mono">
          {data?.embedding_model ? `model ${data.embedding_model}` : ""}
        </span>
      </div>
      <div className="orb-aside__body">
        <div className="orb-rail__search">
          <span className="orb-rail__search-icon" aria-hidden>
            <Icon name="search" />
          </span>
          <Input
            placeholder="Ask a question or paste a phrase…"
            aria-label="Vector search query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        {loading && <p className="orb-review__placeholder">Searching…</p>}
        {disabled && (
          <div className="orb-review__placeholder orb-review__placeholder--error" role="alert">
            Vector search is disabled. Set <Mono>VOYAGE_API_KEY</Mono> on the backend to enable Phase 3.
          </div>
        )}
        {error && (
          <div className="orb-review__placeholder orb-review__placeholder--error" role="alert">{error}</div>
        )}
        {!loading && !disabled && !error && data && data.results.length === 0 && debounced && (
          <p className="orb-review__placeholder">No matches.</p>
        )}
        {data?.results.map((hit) => (
          <Card key={hit.chunk_id} className="orb-aside__hit" onClick={() => onSelectResult?.(hit.document_id)}>
            <div className="orb-aside__hit-head">
              <Mono className="orb-aside__hit-score">score {hit.score.toFixed(3)}</Mono>
              <Mono className="orb-aside__hit-id">{hit.document_id.slice(0, 8)}</Mono>
            </div>
            <p className="orb-aside__hit-snippet">{hit.snippet ?? "—"}</p>
          </Card>
        ))}
      </div>
    </div>
  );
}
