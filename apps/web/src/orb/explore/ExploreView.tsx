/**
 * KW Explorer MVP (ADR-028).
 *
 * Three sub-routes under ``/kf/explore``:
 *
 *   /kf/explore                — Atlas landing (3 metric cards + top-10 topics)
 *   /kf/explore/topics         — full topic index with search-as-you-type
 *   /kf/explore/topics/:id     — topic detail + focused lens + citations
 *
 * The focused lens reuses the existing GraphCanvas — we never default
 * to a full-corpus render (ADR-028 forbids it) and the lens fetches
 * one neighborhood per topic.
 *
 * Today's MVP intentionally skips the relation-explain standalone
 * page; relation inspection folds into the lens side panel.
 */

import { useEffect, useMemo, useState } from "react";
import type { ReactElement } from "react";
import {
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";

import { GraphCanvas } from "../graph/GraphCanvas";
import {
  ApiError,
  getKnowledgeAtlas,
  getKnowledgeNeighborhood,
  listKnowledgeTopics,
  searchKnowledgeExplore,
} from "../../api/client";
import type {
  ApiAtlasResponse,
  ApiDocumentTopic,
  ApiExploreSearchResponse,
  ApiFocusedNeighborhood,
  ApiGraphEdge,
} from "../../api/types";

// ─── Shared helpers ────────────────────────────────────────────────────────

/** Tighten NeighborhoodEdge down to the GraphEdge shape the existing
 *  GraphCanvas expects. The extra fields (is_bridge / is_outlier /
 *  score / strength_class) are surfaced separately in the inspector
 *  side panel, never inside the canvas. */
function _stripEdge(edge: ApiFocusedNeighborhood["edges"][number]): ApiGraphEdge {
  return {
    id: edge.id,
    kind: edge.kind,
    source_id: edge.source_id,
    target_id: edge.target_id,
    properties: edge.properties,
  };
}

function _forbiddenShell(detail: string) {
  return (
    <main className="app-shell" aria-label="Explorer">
      <section className="workspace">
        <header className="workspace-header">
          <h2>Forbidden</h2>
        </header>
        <p>
          This view requires the <code>admin</code> role.
        </p>
        <p className="muted">{detail}</p>
      </section>
    </main>
  );
}

// ─── Atlas (landing) ───────────────────────────────────────────────────────

function AtlasPage(): ReactElement {
  const navigate = useNavigate();
  const [atlas, setAtlas] = useState<ApiAtlasResponse | null>(null);
  const [error, setError] = useState<ApiError | string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getKnowledgeAtlas({ signal: controller.signal })
      .then((response) => setAtlas(response))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError) setError(err);
        else if (err instanceof Error) setError(err.message);
        else setError("Failed to load atlas.");
      });
    return () => controller.abort();
  }, []);

  if (error instanceof ApiError && error.status === 403) {
    return _forbiddenShell(error.detail);
  }

  return (
    <main className="app-shell" aria-label="Explorer atlas">
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Explorer</p>
            <h2>Knowledge atlas</h2>
            <p className="muted">
              Corpus overview — validation coverage, top topics, and the
              recent inflow. Pick a topic to open the focused lens
              (ADR-028).
            </p>
          </div>
          <div className="action-row">
            <button
              type="button"
              className="text-button"
              onClick={() => navigate("/kf/explore/topics")}
              data-testid="kf-explore-go-topics"
            >
              View all topics →
            </button>
          </div>
        </header>

        {error !== null && !(error instanceof ApiError && error.status === 403) && (
          <div className="notice danger" role="alert">
            <strong>Failed to load atlas.</strong>
            <span>
              {error instanceof ApiError ? error.detail : error}
            </span>
          </div>
        )}

        {atlas === null && error === null && (
          <p className="muted" role="status">
            Loading atlas…
          </p>
        )}

        {atlas !== null && (
          <>
            <div className="metric-grid" data-testid="kf-explore-atlas-cards">
              <div className="metric-card">
                <p className="metric-label">Total documents</p>
                <p className="metric-value">
                  {atlas.validation_coverage.total_documents}
                </p>
              </div>
              <div className="metric-card">
                <p className="metric-label">Validated</p>
                <p className="metric-value">
                  {atlas.validation_coverage.validated_count}
                </p>
              </div>
              <div className="metric-card">
                <p className="metric-label">Needs review</p>
                <p className="metric-value">
                  {atlas.validation_coverage.needs_review_count}
                </p>
              </div>
            </div>

            <h3>Top topics</h3>
            {atlas.top_topics.length === 0 ? (
              <p className="muted" data-testid="kf-explore-atlas-empty-topics">
                No topics yet. The topic-clustering pass populates this list as
                documents are projected.
              </p>
            ) : (
              <ol
                className="kf-explore-topic-list"
                data-testid="kf-explore-atlas-topics"
              >
                {atlas.top_topics.slice(0, 10).map((t) => (
                  <li
                    key={t.topic_id}
                    className="kf-explore-topic-row"
                    data-testid={`kf-explore-atlas-topic-${t.topic_id}`}
                  >
                    <button
                      type="button"
                      className="text-button"
                      onClick={() =>
                        navigate(`/kf/explore/topics/${t.topic_id}`)
                      }
                    >
                      <strong>{t.label || t.topic_id}</strong>
                      <span className="muted">
                        {" "}
                        · {t.document_count} doc
                        {t.document_count === 1 ? "" : "s"} · {t.chunk_count}{" "}
                        chunks
                      </span>
                    </button>
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </section>
    </main>
  );
}

// ─── Topics list ──────────────────────────────────────────────────────────

function TopicsIndexPage(): ReactElement {
  const navigate = useNavigate();
  const [topics, setTopics] = useState<ApiDocumentTopic[] | null>(null);
  const [error, setError] = useState<ApiError | string | null>(null);
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] =
    useState<ApiExploreSearchResponse | null>(null);

  // Initial unfiltered fetch on mount.
  useEffect(() => {
    const controller = new AbortController();
    listKnowledgeTopics({ limit: 100, signal: controller.signal })
      .then((response) => setTopics(response.items))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError) setError(err);
        else if (err instanceof Error) setError(err.message);
        else setError("Failed to load topics.");
      });
    return () => controller.abort();
  }, []);

  // Search-as-you-type — debounced into the explore endpoint.
  useEffect(() => {
    const q = search.trim();
    if (q.length === 0) {
      setSearchResults(null);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      searchKnowledgeExplore(q, { signal: controller.signal })
        .then((response) => setSearchResults(response))
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
          // Search errors are non-fatal — keep the underlying list visible
          // and surface a non-blocking note.
          setSearchResults({
            schema_version: "v0.1",
            query: q,
            embedding_model: "",
            documents: [],
            chunks: [],
            topics: [],
            entities: [],
            relations: [],
          });
        });
    }, 200);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [search]);

  if (error instanceof ApiError && error.status === 403) {
    return _forbiddenShell(error.detail);
  }

  const visibleTopics: Array<{
    topic_id: string;
    label: string;
    keywords: string[];
  }> = searchResults
    ? searchResults.topics.map((t) => ({
        topic_id: t.topic_id,
        label: t.label,
        keywords: t.keywords,
      }))
    : (topics ?? []).map((t) => ({
        topic_id: t.id,
        label: t.label,
        keywords: t.keywords,
      }));

  return (
    <main className="app-shell" aria-label="Explorer topics">
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Explorer</p>
            <h2>Topics</h2>
            <p className="muted">
              Every document theme in the corpus. Click a topic to open
              its focused lens.
            </p>
          </div>
        </header>

        <form
          className="taxonomy-lookup"
          onSubmit={(e) => e.preventDefault()}
        >
          <label htmlFor="kf-explore-topic-search" className="muted">
            Search
          </label>
          <input
            id="kf-explore-topic-search"
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="search-as-you-type…"
            data-testid="kf-explore-topic-search"
          />
        </form>

        {error !== null &&
          !(error instanceof ApiError && error.status === 403) && (
            <div className="notice danger" role="alert">
              <strong>Failed to load topics.</strong>
              <span>
                {error instanceof ApiError ? error.detail : error}
              </span>
            </div>
          )}

        {topics === null && error === null && (
          <p className="muted" role="status">
            Loading topics…
          </p>
        )}

        {visibleTopics.length === 0 && topics !== null && (
          <p className="muted" data-testid="kf-explore-topics-empty">
            No topics match.
          </p>
        )}

        {visibleTopics.length > 0 && (
          <ol
            className="kf-explore-topic-list"
            data-testid="kf-explore-topics-list"
          >
            {visibleTopics.map((t) => (
              <li
                key={t.topic_id}
                className="kf-explore-topic-row"
                data-testid={`kf-explore-topic-row-${t.topic_id}`}
              >
                <button
                  type="button"
                  className="text-button"
                  onClick={() =>
                    navigate(`/kf/explore/topics/${t.topic_id}`)
                  }
                >
                  <strong>{t.label || t.topic_id}</strong>
                  {t.keywords.length > 0 && (
                    <span className="muted">
                      {" "}
                      · {t.keywords.slice(0, 5).join(", ")}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ol>
        )}
      </section>
    </main>
  );
}

// ─── Topic detail + focused lens ──────────────────────────────────────────

function TopicDetailPage(): ReactElement {
  const params = useParams<{ topicId: string }>();
  const topicId = params.topicId ?? "";
  const [neighborhood, setNeighborhood] =
    useState<ApiFocusedNeighborhood | null>(null);
  const [citations, setCitations] = useState<ApiExploreSearchResponse | null>(
    null,
  );
  const [error, setError] = useState<ApiError | string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  useEffect(() => {
    if (!topicId) return;
    const controller = new AbortController();
    getKnowledgeNeighborhood({
      rootKind: "topic",
      rootId: topicId,
      depth: 2,
      signal: controller.signal,
    })
      .then((response) => setNeighborhood(response))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError) setError(err);
        else if (err instanceof Error) setError(err.message);
        else setError("Failed to load neighborhood.");
      });
    return () => controller.abort();
  }, [topicId]);

  // Citations panel — chunks mentioning this topic via the explore
  // search facet. Cheap probe; non-blocking on failure.
  useEffect(() => {
    if (!topicId) return;
    const controller = new AbortController();
    searchKnowledgeExplore(topicId, { signal: controller.signal })
      .then((response) => setCitations(response))
      .catch(() => {
        // Silent on citation failure — the lens is the main surface.
      });
    return () => controller.abort();
  }, [topicId]);

  const edges = useMemo(
    () => (neighborhood?.edges ?? []).map(_stripEdge),
    [neighborhood],
  );

  if (error instanceof ApiError && error.status === 403) {
    return _forbiddenShell(error.detail);
  }

  const selectedEdge = useMemo(() => {
    if (selectedNodeId === null || neighborhood === null) return null;
    return (
      neighborhood.edges.find(
        (e) =>
          e.source_id === selectedNodeId || e.target_id === selectedNodeId,
      ) ?? null
    );
  }, [neighborhood, selectedNodeId]);

  return (
    <main className="app-shell" aria-label="Explorer topic detail">
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Explorer</p>
            <h2>Topic · {topicId}</h2>
            <p className="muted">
              Focused lens centered on this topic at depth 2 (ADR-028).
            </p>
          </div>
        </header>

        {error !== null &&
          !(error instanceof ApiError && error.status === 403) && (
            <div className="notice danger" role="alert">
              <strong>Failed to load neighborhood.</strong>
              <span>
                {error instanceof ApiError ? error.detail : error}
              </span>
            </div>
          )}

        {neighborhood === null && error === null && (
          <p className="muted" role="status">
            Loading lens…
          </p>
        )}

        {neighborhood !== null && neighborhood.nodes.length === 0 && (
          <p
            className="muted"
            data-testid="kf-explore-topic-empty"
          >
            No connections for this topic yet. The graph projector will
            populate the lens once documents land in the cluster.
          </p>
        )}

        {neighborhood !== null && neighborhood.nodes.length > 0 && (
          <div
            className="kf-explore-lens"
            data-testid="kf-explore-topic-lens"
          >
            <GraphCanvas
              nodes={neighborhood.nodes}
              edges={edges}
              selectedId={selectedNodeId}
              onSelect={(id) => setSelectedNodeId(id)}
            />
            {selectedEdge !== null && (
              <aside
                className="kf-explore-inspector"
                data-testid="kf-explore-inspector"
              >
                <h4>Relation</h4>
                <p>
                  <strong>{selectedEdge.kind}</strong>
                </p>
                <p className="muted">
                  {selectedEdge.source_id} → {selectedEdge.target_id}
                </p>
                {selectedEdge.score !== null && (
                  <p className="muted">
                    Score: {selectedEdge.score.toFixed(3)}
                  </p>
                )}
              </aside>
            )}
          </div>
        )}

        <h3>Citations</h3>
        {citations === null ? (
          <p className="muted" role="status">
            Loading citations…
          </p>
        ) : citations.chunks.length === 0 ? (
          <p
            className="muted"
            data-testid="kf-explore-citations-empty"
          >
            No chunk citations match. Try a more specific search from
            the topics index.
          </p>
        ) : (
          <ol
            className="kf-explore-topic-list"
            data-testid="kf-explore-citations"
          >
            {citations.chunks.slice(0, 20).map((c) => (
              <li
                key={c.chunk_id}
                className="kf-explore-topic-row"
                data-testid={`kf-explore-citation-${c.chunk_id}`}
              >
                <span className="orb-mono">{c.chunk_id}</span>
                {c.snippet && (
                  <span className="muted"> · {c.snippet.slice(0, 120)}</span>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>
    </main>
  );
}

// ─── Router ───────────────────────────────────────────────────────────────

export function ExploreView(): ReactElement {
  return (
    <Routes>
      <Route index element={<AtlasPage />} />
      <Route path="topics" element={<TopicsIndexPage />} />
      <Route path="topics/:topicId" element={<TopicDetailPage />} />
      <Route path="*" element={<Navigate to="/kf/explore" replace />} />
    </Routes>
  );
}
