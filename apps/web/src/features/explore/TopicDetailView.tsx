/**
 * Knowledge Explorer — Topic Detail (ADR-028).
 *
 * Header — topic label/keywords derived from `/knowledge/topics`
 * (filtered client-side by id; the route has no `/{topic_id}` form).
 *
 * Focused lens — bounded subgraph from `/knowledge/neighborhood` at
 * depth 2. Lazy-loaded so the NVL chunk doesn't ship in the initial
 * Explorer bundle. ADR-028 §"Information Architecture" §3 forbids
 * defaulting to the full-corpus graph; the lens always reads the
 * bounded payload.
 *
 * Citations — `/knowledge/explore/search?q=<topicLabel>` chunks list.
 * Each chunk links back to `/kf/review/{doc}?chunk={chunk}` so an
 * operator can jump to the source pane.
 *
 * Inspector — relation click in the lens opens a side panel that
 * uses `/knowledge/relations/aggregate` when both endpoints of the
 * clicked relation are documents.
 */

import { Suspense, lazy, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  aggregateRelations,
  ApiError,
  exploreSearch,
  listKnowledgeTopics,
} from "../../api/client";
import type {
  ApiKnowledgeAggregatedRelation,
  ApiKnowledgeExploreChunk,
  ApiKnowledgeNeighborhoodEdge,
  ApiKnowledgeTopic,
} from "../../api/types";

// NVL canvas is heavy (>500 kB chunk) — keep it out of the Explorer's
// initial bundle. ADR-028 §"Bundle" calls this out explicitly.
const FocusedLens = lazy(() => import("./FocusedLens"));

const TOPIC_LIST_LIMIT = 200;

export function TopicDetailView() {
  const { topicId = "" } = useParams<{ topicId: string }>();

  const [topic, setTopic] = useState<ApiKnowledgeTopic | null>(null);
  const [citations, setCitations] = useState<ApiKnowledgeExploreChunk[]>([]);
  const [headerLoading, setHeaderLoading] = useState(true);
  const [headerError, setHeaderError] = useState<ApiError | string | null>(
    null,
  );
  const [citationsLoading, setCitationsLoading] = useState(false);
  const [selectedRelation, setSelectedRelation] =
    useState<ApiKnowledgeNeighborhoodEdge | null>(null);

  useEffect(() => {
    if (!topicId) return;
    let cancelled = false;
    setHeaderLoading(true);
    setHeaderError(null);
    // No `/knowledge/topics/{id}` route — page-walk the listing and
    // find by id. The first page covers most corpora; if not present,
    // we render the header as best-effort with the topicId only.
    listKnowledgeTopics({ limit: TOPIC_LIST_LIMIT })
      .then((response) => {
        if (cancelled) return;
        const match = response.items.find((t) => t.id === topicId);
        setTopic(match ?? null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError) setHeaderError(err);
        else if (err instanceof Error) setHeaderError(err.message);
        else setHeaderError("Failed to load topic.");
      })
      .finally(() => {
        if (!cancelled) setHeaderLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [topicId]);

  // Citations — search-driven. Use the topic label when we have it;
  // fall back to the topicId so the panel is never empty on cold load.
  useEffect(() => {
    if (!topicId) return;
    const query = topic?.label ?? topicId;
    let cancelled = false;
    setCitationsLoading(true);
    exploreSearch({ q: query, chunkLimit: 10 })
      .then((response) => {
        if (cancelled) return;
        setCitations(response.chunks);
      })
      .catch(() => {
        if (cancelled) return;
        // Citations are best-effort — a missing chunk index shouldn't
        // hide the header / lens. Quietly empty the list.
        setCitations([]);
      })
      .finally(() => {
        if (!cancelled) setCitationsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [topicId, topic?.label]);

  if (headerError instanceof ApiError && headerError.status === 403) {
    return (
      <main className="app-shell" aria-label="Topic detail">
        <section className="workspace">
          <header className="workspace-header">
            <h2>Forbidden</h2>
          </header>
          <p>This view requires Explorer access.</p>
          <p className="muted">{headerError.detail}</p>
        </section>
      </main>
    );
  }

  const headerLabel = topic?.label ?? topicId;

  return (
    <main className="app-shell" aria-label="Topic detail">
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Knowledge Explorer · Topic</p>
            <h2 data-testid="topic-detail-label">{headerLabel}</h2>
            {topic !== null ? (
              <>
                {topic.summary ? (
                  <p
                    className="muted"
                    data-testid="topic-detail-summary"
                  >
                    {topic.summary}
                  </p>
                ) : null}
                {topic.keywords.length > 0 ? (
                  <p
                    className="muted"
                    data-testid="topic-detail-keywords"
                  >
                    {topic.keywords.join(" · ")}
                  </p>
                ) : null}
              </>
            ) : headerLoading ? (
              <p className="muted" role="status" aria-live="polite">
                Loading topic…
              </p>
            ) : (
              <p
                className="muted"
                data-testid="topic-detail-not-found"
              >
                Topic metadata not found. Lens and citations still load
                from the topic id.
              </p>
            )}
          </div>
          <Link to="/kf/explore/topics" className="text-button">
            Back to topics
          </Link>
        </header>

        <section
          className="explore-lens-panel"
          data-testid="topic-detail-lens-panel"
        >
          <header className="section-heading">
            <h2>Focused lens</h2>
          </header>
          <Suspense
            fallback={
              <p className="muted" role="status">
                Loading focused lens…
              </p>
            }
          >
            <FocusedLens
              rootKind="topic"
              rootId={topicId}
              depth={2}
              onSelectRelation={(edge) => setSelectedRelation(edge)}
            />
          </Suspense>
        </section>

        <section
          className="explore-citations"
          data-testid="topic-detail-citations"
        >
          <header className="section-heading">
            <h2>Citations</h2>
          </header>
          {citationsLoading ? (
            <p className="muted" role="status" aria-live="polite">
              Loading citations…
            </p>
          ) : citations.length === 0 ? (
            <p
              className="muted"
              data-testid="topic-detail-citations-empty"
            >
              No supporting chunks found for this topic.
            </p>
          ) : (
            <ul
              className="explore-citation-list"
              data-testid="topic-detail-citations-list"
            >
              {citations.map((chunk) => (
                <li key={chunk.chunk_id}>
                  <Link
                    to={`/kf/review/${encodeURIComponent(
                      chunk.document_id,
                    )}?chunk=${encodeURIComponent(chunk.chunk_id)}`}
                    data-testid={`topic-detail-citation-${chunk.chunk_id}`}
                  >
                    <strong>{chunk.document_id}</strong>
                    {chunk.snippet ? (
                      <span className="muted">{chunk.snippet}</span>
                    ) : null}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </section>

      {selectedRelation !== null ? (
        <RelationInspector
          edge={selectedRelation}
          onClose={() => setSelectedRelation(null)}
        />
      ) : null}
    </main>
  );
}

interface RelationInspectorProps {
  edge: ApiKnowledgeNeighborhoodEdge;
  onClose: () => void;
}

/** Side-panel inspector for the lens's relation-click action. Renders
 *  the relation's intrinsic explain (kind, score, shared keywords) and
 *  — for document-to-document edges — runs the relations/aggregate
 *  call to surface contributing chunk pairs. */
function RelationInspector({ edge, onClose }: RelationInspectorProps) {
  const [aggregate, setAggregate] =
    useState<ApiKnowledgeAggregatedRelation | null>(null);
  const [loading, setLoading] = useState(false);

  // Only document↔document edges have an aggregate explain. The
  // neighborhood payload doesn't surface node kinds inline, but the
  // canonical id pattern (no `:` separator) identifies document ids.
  const isDocPair =
    !edge.source_id.includes(":") && !edge.target_id.includes(":");

  useEffect(() => {
    if (!isDocPair) return;
    let cancelled = false;
    setLoading(true);
    setAggregate(null);
    aggregateRelations({
      sourceDocumentId: edge.source_id,
      targetDocumentId: edge.target_id,
    })
      .then((response) => {
        if (cancelled) return;
        setAggregate(response);
      })
      .catch(() => {
        if (cancelled) return;
        // 404 is expected for non-doc-doc edges; quietly skip.
        setAggregate(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [edge.source_id, edge.target_id, isDocPair]);

  const sharedKeywords = edge.properties["shared_keywords"];
  const reason = edge.properties["reason"];

  return (
    <aside
      className="explore-inspector"
      aria-label="Relation inspector"
      data-testid="relation-inspector"
    >
      <header className="explore-inspector-header">
        <div>
          <p className="eyebrow">Relation</p>
          <h3>{edge.kind}</h3>
        </div>
        <button
          type="button"
          className="text-button"
          onClick={onClose}
          aria-label="Close inspector"
        >
          Close
        </button>
      </header>
      <dl className="explore-inspector-fields">
        <dt>Source</dt>
        <dd>
          <code>{edge.source_id}</code>
        </dd>
        <dt>Target</dt>
        <dd>
          <code>{edge.target_id}</code>
        </dd>
        {edge.score !== null ? (
          <>
            <dt>Score</dt>
            <dd>{edge.score.toFixed(3)}</dd>
          </>
        ) : null}
        {edge.strength_class !== null ? (
          <>
            <dt>Strength</dt>
            <dd>{edge.strength_class}</dd>
          </>
        ) : null}
        {edge.is_bridge === true ? (
          <>
            <dt>Bridge</dt>
            <dd>candidate bridge link</dd>
          </>
        ) : null}
        {edge.is_outlier === true ? (
          <>
            <dt>Outlier</dt>
            <dd>candidate surprising link</dd>
          </>
        ) : null}
        {typeof reason === "string" && reason.length > 0 ? (
          <>
            <dt>Reason</dt>
            <dd>{reason}</dd>
          </>
        ) : null}
        {Array.isArray(sharedKeywords) && sharedKeywords.length > 0 ? (
          <>
            <dt>Shared keywords</dt>
            <dd>{sharedKeywords.join(" · ")}</dd>
          </>
        ) : null}
      </dl>
      {isDocPair ? (
        <section className="explore-inspector-aggregate">
          <header className="section-heading">
            <h4>Contributing pairs</h4>
          </header>
          {loading ? (
            <p className="muted" role="status">
              Loading…
            </p>
          ) : aggregate !== null ? (
            <p className="muted">
              {aggregate.pair_count} contributing chunk pair
              {aggregate.pair_count === 1 ? "" : "s"}.
            </p>
          ) : (
            <p className="muted">No aggregate evidence available.</p>
          )}
        </section>
      ) : null}
    </aside>
  );
}

export default TopicDetailView;
