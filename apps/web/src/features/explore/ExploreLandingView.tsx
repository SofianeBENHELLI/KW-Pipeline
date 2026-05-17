/**
 * Knowledge Explorer — Atlas landing (ADR-028 §"Information Architecture").
 *
 * Default home for the Explorer. Renders the atlas's high-signal summary
 * blocks (validation coverage as metric cards + top topics as a list)
 * without ever fetching the full-corpus graph — ADR-028 forbids that as
 * the default render path.
 *
 * The top-10 topics list is fetched separately via `GET /knowledge/topics`
 * to satisfy the spec literally; the atlas already surfaces top topics
 * but the explicit `/knowledge/topics` call paginates and links into
 * the Topic Detail view by DocumentTopic.id.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  ApiError,
  getKnowledgeAtlas,
  listKnowledgeTopics,
} from "../../api/client";
import type {
  ApiKnowledgeAtlas,
  ApiKnowledgeTopic,
} from "../../api/types";

const TOP_TOPICS_LIMIT = 10;

export function ExploreLandingView() {
  const [atlas, setAtlas] = useState<ApiKnowledgeAtlas | null>(null);
  const [topics, setTopics] = useState<ApiKnowledgeTopic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      getKnowledgeAtlas(),
      listKnowledgeTopics({ limit: TOP_TOPICS_LIMIT }),
    ])
      .then(([atlasResponse, topicsResponse]) => {
        if (cancelled) return;
        setAtlas(atlasResponse);
        setTopics(topicsResponse.items);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError) setError(err);
        else if (err instanceof Error) setError(err.message);
        else setError("Failed to load the atlas.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 403 — same posture as the admin views: surface a Forbidden state
  // when the backend tells us the caller can't access the surface. We
  // never derive role / scope client-side.
  if (error instanceof ApiError && error.status === 403) {
    return (
      <main className="app-shell" aria-label="Knowledge Explorer atlas">
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
    <main className="app-shell" aria-label="Knowledge Explorer atlas">
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Knowledge Explorer</p>
            <h2>Atlas</h2>
            <p className="muted">
              Corpus-wide summary. Drill into a topic to open its focused
              graph lens.
            </p>
          </div>
        </header>

        {loading ? (
          <p className="muted" role="status" aria-live="polite">
            Loading atlas…
          </p>
        ) : error !== null ? (
          <div className="notice danger" role="alert">
            <strong>Failed to load</strong>
            <span>
              {error instanceof ApiError ? error.detail : error}
            </span>
          </div>
        ) : atlas !== null ? (
          <>
            <div className="metric-grid" data-testid="atlas-metric-grid">
              <div className="metric-card">
                <span>Total documents</span>
                <strong data-testid="atlas-total-documents">
                  {atlas.validation_coverage.total_documents}
                </strong>
              </div>
              <div className="metric-card">
                <span>Validated</span>
                <strong data-testid="atlas-validated">
                  {atlas.validation_coverage.validated_count}
                </strong>
              </div>
              <div className="metric-card warning">
                <span>Needs review</span>
                <strong data-testid="atlas-needs-review">
                  {atlas.validation_coverage.needs_review_count}
                </strong>
              </div>
            </div>

            <header className="section-heading">
              <h2>Top topics</h2>
            </header>
            {topics.length === 0 ? (
              <p className="muted" data-testid="atlas-empty-topics">
                No topics extracted yet. Validate a document to populate
                the atlas.
              </p>
            ) : (
              <ul
                className="explore-topic-list"
                data-testid="atlas-topic-list"
              >
                {topics.map((topic) => (
                  <li key={topic.id}>
                    <Link
                      to={`/kf/explore/topics/${encodeURIComponent(topic.id)}`}
                      data-testid={`atlas-topic-link-${topic.id}`}
                    >
                      <strong>{topic.label}</strong>
                      {topic.keywords.length > 0 ? (
                        <span className="muted">
                          {topic.keywords.slice(0, 4).join(" · ")}
                        </span>
                      ) : null}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : null}
      </section>
    </main>
  );
}

export default ExploreLandingView;
