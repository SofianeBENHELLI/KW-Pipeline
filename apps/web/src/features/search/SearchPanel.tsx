/**
 * Vector-search panel — Phase 3 / ADR-015.
 *
 * Calls ``GET /knowledge/search`` with a debounced query and renders
 * the top-K matching chunks. The panel is self-contained: it owns its
 * input state, debounce timer, abort controller, and result list.
 *
 * Three failure modes the UI surfaces explicitly:
 *
 * - **Phase 3 disabled** (503 + ``KW_VECTOR_SEARCH_DISABLED``). The
 *   route's :class:`ApiError` envelope ships a remediation string;
 *   we render it verbatim so operators see exactly which env vars to
 *   set.
 * - **Network / 5xx**. Generic error banner with the message the API
 *   returned.
 * - **No matches**. Empty state — the indexed graph is fine, the
 *   query just didn't hit anything.
 */

import { useEffect, useRef, useState } from "react";

import { ApiError, searchKnowledgeChunks } from "../../api/client";
import type { ApiChunkSearchResult } from "../../api/types";

const SEARCH_DEBOUNCE_MS = 300;
const DEFAULT_LIMIT = 10;

export interface SearchPanelProps {
  /**
   * Click handler invoked when a result is activated. Lets the parent
   * navigate to the chunk's document/version. Optional — when absent,
   * the rows render as informational only.
   */
  onSelectResult?: (result: ApiChunkSearchResult) => void;
}

export function SearchPanel({ onSelectResult }: SearchPanelProps) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [results, setResults] = useState<ApiChunkSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | string | null>(null);
  const [embeddingModel, setEmbeddingModel] = useState<string | null>(null);

  // Debounce the input → the network call. Trailing-edge debounce keeps
  // typing snappy while collapsing keystroke bursts into one request.
  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedQuery(query), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [query]);

  // Abort the in-flight request when the debounced query changes again
  // before the response lands — otherwise an older slow response can
  // race in and overwrite the newer one.
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
      signal: controller.signal,
    })
      .then((response) => {
        if (controller.signal.aborted) return;
        setResults(response.results);
        setEmbeddingModel(response.embedding_model);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        // Aborted fetch surfaces as DOMException("AbortError"); the
        // signal-aborted check above usually wins, but the network
        // layer can still throw before the abort propagates.
        if (err instanceof DOMException && err.name === "AbortError") return;
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
  }, [debouncedQuery]);

  const isDisabled =
    error instanceof ApiError && error.code === "KW_VECTOR_SEARCH_DISABLED";

  return (
    <section
      className="workspace search-panel"
      aria-label="Knowledge search"
      data-testid="search-panel"
    >
      <header className="search-panel__header">
        <h2>Search</h2>
        {embeddingModel !== null && results.length > 0 && (
          <p className="muted small">
            Ranked by cosine similarity · model{" "}
            <code>{embeddingModel}</code>
          </p>
        )}
      </header>

      <label className="search-panel__input">
        <span className="visually-hidden">Search query</span>
        <input
          type="search"
          placeholder="Search across validated documents…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="Search query"
          data-testid="search-panel-input"
        />
      </label>

      {isDisabled && error instanceof ApiError && (
        <div
          className="search-panel__notice search-panel__notice--disabled"
          role="status"
          data-testid="search-panel-disabled"
        >
          <strong>Vector search is disabled.</strong>
          <p>{error.message}</p>
          {error.remediation !== null && <p className="muted">{error.remediation}</p>}
        </div>
      )}

      {error !== null && !isDisabled && (
        <div
          className="search-panel__notice search-panel__notice--error"
          role="alert"
          data-testid="search-panel-error"
        >
          {error instanceof Error ? error.message : error}
        </div>
      )}

      {loading && (
        <p className="muted" role="status" aria-live="polite">
          Searching…
        </p>
      )}

      {!loading && error === null && debouncedQuery.trim() !== "" && results.length === 0 && (
        <p className="muted" data-testid="search-panel-empty">
          No matches for <code>{debouncedQuery}</code>.
        </p>
      )}

      {results.length > 0 && (
        <ol className="search-panel__results" data-testid="search-panel-results">
          {results.map((result) => {
            const score = (result.score * 100).toFixed(1);
            const interactive = onSelectResult !== undefined;
            return (
              <li
                key={result.chunk_id}
                className="search-panel__result"
                data-testid="search-panel-result"
              >
                {interactive ? (
                  <button
                    type="button"
                    className="search-panel__result-button"
                    onClick={() => onSelectResult(result)}
                  >
                    <ResultBody result={result} score={score} />
                  </button>
                ) : (
                  <ResultBody result={result} score={score} />
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

function ResultBody({
  result,
  score,
}: {
  result: ApiChunkSearchResult;
  score: string;
}) {
  return (
    <>
      <div className="search-panel__result-meta">
        <span className="search-panel__result-score">{score}%</span>
        <code className="search-panel__result-id">{result.chunk_id}</code>
      </div>
      {result.snippet !== null && result.snippet !== "" && (
        <p className="search-panel__result-snippet">{result.snippet}</p>
      )}
      <p className="muted small">
        document <code>{result.document_id}</code> · version{" "}
        <code>{result.version_id}</code>
      </p>
    </>
  );
}
