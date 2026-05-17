/**
 * Knowledge Explorer — Topic Index (ADR-028).
 *
 * Empty input → paginated list of every DocumentTopic via
 * `/knowledge/topics`. Typed query → grouped search via
 * `/knowledge/explore/search?kind=topic` (we read the `topics` group
 * off the multi-kind envelope) with a 300 ms debounce so the user
 * doesn't spam the embedding service on every keystroke.
 */

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  ApiError,
  exploreSearch,
  listKnowledgeTopics,
} from "../../api/client";

const SEARCH_DEBOUNCE_MS = 300;
const PAGE_LIMIT = 50;

interface TopicRow {
  id: string;
  label: string;
  keywords: string[];
}

export function TopicIndexView() {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [rows, setRows] = useState<TopicRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | string | null>(null);

  // Debounce the search input so a 300 ms idle gap is required before
  // the next fetch fires. Keystroke-bound fetches would otherwise
  // hammer the embedding service.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebounced(query.trim());
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query]);

  // Fetch list or search whenever the debounced query changes.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const task =
      debounced.length === 0
        ? listKnowledgeTopics({ limit: PAGE_LIMIT }).then((response) =>
            response.items.map((topic) => ({
              id: topic.id,
              label: topic.label,
              keywords: topic.keywords,
            })),
          )
        : exploreSearch({ q: debounced }).then((response) =>
            response.topics.map((hit) => ({
              id: hit.topic_id,
              label: hit.label,
              keywords: hit.keywords,
            })),
          );

    task
      .then((next) => {
        if (cancelled) return;
        setRows(next);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError) setError(err);
        else if (err instanceof Error) setError(err.message);
        else setError("Failed to load topics.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [debounced]);

  const emptyMessage = useMemo(
    () =>
      debounced.length === 0
        ? "No topics extracted yet."
        : `No topics match "${debounced}".`,
    [debounced],
  );

  if (error instanceof ApiError && error.status === 403) {
    return (
      <main className="app-shell" aria-label="Topic index">
        <section className="workspace">
          <header className="workspace-header">
            <h2>Forbidden</h2>
          </header>
          <p>This view requires Explorer access.</p>
          <p className="muted">{error.detail}</p>
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell" aria-label="Topic index">
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Knowledge Explorer</p>
            <h2>Topics</h2>
          </div>
        </header>

        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search topics…"
          className="explore-search-input"
          aria-label="Search topics"
          data-testid="topic-search-input"
        />

        {loading ? (
          <p className="muted" role="status" aria-live="polite">
            Loading…
          </p>
        ) : error !== null ? (
          <div className="notice danger" role="alert">
            <strong>Failed to load</strong>
            <span>
              {error instanceof ApiError ? error.detail : error}
            </span>
          </div>
        ) : rows.length === 0 ? (
          <p className="muted" data-testid="topic-index-empty">
            {emptyMessage}
          </p>
        ) : (
          <ul className="explore-topic-list" data-testid="topic-index-list">
            {rows.map((row) => (
              <li key={row.id}>
                <Link
                  to={`/kf/explore/topics/${encodeURIComponent(row.id)}`}
                  data-testid={`topic-index-link-${row.id}`}
                >
                  <strong>{row.label}</strong>
                  {row.keywords.length > 0 ? (
                    <span className="muted">
                      {row.keywords.slice(0, 4).join(" · ")}
                    </span>
                  ) : null}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

export default TopicIndexView;
