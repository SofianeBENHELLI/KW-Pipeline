/**
 * Vector-search section — Phase 3 / ADR-015 surface for the widget.
 *
 * Mirrors the web ``<SearchPanel/>`` (apps/web/src/features/search/) so
 * 3DEXPERIENCE users get the same retrieval surface the standalone web
 * app already exposes. Self-contained: owns its input state, debounce,
 * abort controller, and result list.
 *
 * Three failure modes the UI surfaces explicitly:
 *
 * - **Phase 3 disabled** — backend returns 503 +
 *   ``KW_VECTOR_SEARCH_DISABLED``. The route's ``ApiError`` envelope
 *   ships a remediation string; we render it verbatim so operators see
 *   exactly which env vars to set.
 * - **Network / 5xx** — generic error banner with the message the API
 *   returned.
 * - **No matches** — empty state; the indexed graph is fine, the query
 *   just didn't hit anything.
 */

import React, { useEffect, useRef, useState } from "react";

import { ApiError, searchKnowledgeChunks } from "../api/client";
import type { ChunkSearchResult } from "../api/types";
import { Icon } from "../components/icons";
import { SectionHeader } from "../components/SectionHeader";

const SEARCH_DEBOUNCE_MS = 300;
const DEFAULT_LIMIT = 10;

interface Props {
  apiBaseUrl: string;
  refreshTick: number;
  /**
   * Optional click hook for navigating to a hit — wired by the parent
   * when integration with the documents view lands. When absent the
   * row renders as informational only.
   */
  onSelectResult?: (result: ChunkSearchResult) => void;
}

export const SearchPanel: React.FC<Props> = ({
  apiBaseUrl,
  refreshTick,
  onSelectResult,
}) => {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [results, setResults] = useState<ChunkSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | string | null>(null);
  const [embeddingModel, setEmbeddingModel] = useState<string | null>(null);

  // Debounce typing → network. Trailing-edge debounce keeps the input
  // snappy while collapsing keystroke bursts into one request.
  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedQuery(query), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [query]);

  // Re-run the query when the API base URL changes, when the parent's
  // refreshTick increments (header refresh button), or when the
  // debounced query changes. Aborting the in-flight request when a new
  // query lands prevents older slow responses from racing in.
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    abortRef.current?.abort();
    const trimmed = debouncedQuery.trim();
    if (trimmed === "") {
      setResults([]);
      setError(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    searchKnowledgeChunks(trimmed, {
      limit: DEFAULT_LIMIT,
      baseUrl: apiBaseUrl,
      signal: controller.signal,
    })
      .then((response) => {
        if (controller.signal.aborted) return;
        setResults(response.results);
        setEmbeddingModel(response.embedding_model);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if ((err as { name?: string })?.name === "AbortError") return;
        if (err instanceof ApiError) {
          setError(err);
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError("Search failed.");
        }
        setResults([]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [apiBaseUrl, debouncedQuery, refreshTick]);

  const isDisabled =
    error instanceof ApiError && error.code === "KW_VECTOR_SEARCH_DISABLED";

  const meta =
    embeddingModel !== null && results.length > 0
      ? `${results.length} hit${results.length === 1 ? "" : "s"} · ${embeddingModel}`
      : undefined;

  return (
    <section
      className="kw-section"
      aria-label="Knowledge search"
      data-testid="search-panel"
    >
      <SectionHeader icon="search" title="Search" meta={meta} />

      <div className="kw-search">
        <Icon name="search" />
        <input
          type="search"
          placeholder="Search across validated documents…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search query"
          data-testid="search-panel-input"
        />
      </div>

      {isDisabled && error instanceof ApiError && (
        <div className="kw-empty" role="status" data-testid="search-panel-disabled">
          <span className="kw-empty__glyph" aria-hidden="true">
            <Icon name="info" size={18} />
          </span>
          <div className="kw-empty__title">Vector search is disabled</div>
          <div className="kw-empty__body">{error.detail}</div>
          {error.remediation !== null && (
            <div className="kw-empty__body kw-search__remediation">
              {error.remediation}
            </div>
          )}
        </div>
      )}

      {error !== null && !isDisabled && (
        <div className="kw-error" role="alert" data-testid="search-panel-error">
          {error instanceof ApiError
            ? `${error.code}: ${error.detail}`
            : error}
        </div>
      )}

      {loading && <div className="kw-status">Searching…</div>}

      {!loading &&
        error === null &&
        debouncedQuery.trim() !== "" &&
        results.length === 0 && (
          <div className="kw-empty" data-testid="search-panel-empty">
            <span className="kw-empty__glyph" aria-hidden="true">
              <Icon name="search" size={18} />
            </span>
            <div className="kw-empty__title">No matches</div>
            <div className="kw-empty__body">
              Nothing in the indexed corpus is similar to{" "}
              <code>{debouncedQuery}</code>.
            </div>
          </div>
        )}

      {results.length > 0 && (
        <ol className="kw-search-results" data-testid="search-panel-results">
          {results.map((result) => {
            const score = (result.score * 100).toFixed(1);
            const interactive = onSelectResult !== undefined;
            const body = (
              <>
                <div className="kw-search-results__meta">
                  <span className="kw-search-results__score">{score}%</span>
                  <code className="kw-search-results__id">{result.chunk_id}</code>
                </div>
                {result.snippet !== null && result.snippet !== "" && (
                  <p className="kw-search-results__snippet">{result.snippet}</p>
                )}
                <p className="kw-search-results__loc">
                  document <code>{result.document_id}</code> · version{" "}
                  <code>{result.version_id}</code>
                </p>
              </>
            );
            return (
              <li
                key={result.chunk_id}
                className="kw-search-results__item"
                data-testid="search-panel-result"
              >
                {interactive ? (
                  <button
                    type="button"
                    className="kw-search-results__btn"
                    onClick={() => onSelectResult(result)}
                  >
                    {body}
                  </button>
                ) : (
                  body
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
};
