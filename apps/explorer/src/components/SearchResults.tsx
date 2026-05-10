/**
 * Server-backed grouped semantic search results dropdown for the
 * Explorer's header search box (#319 / #313, ADR-028).
 *
 * Drives off ``useExploreSearch`` snapshots — the parent owns the
 * input state and the API base URL; this component is purely
 * presentational.
 *
 * Render branches:
 *
 *   - ``"idle"``   → nothing (input is empty)
 *   - ``"loading"`` → "Searching…" affordance
 *   - ``"data"``    → the grouped results
 *   - ``"empty"``   → "No matches" with the query echoed back
 *   - ``"error"``   → red banner with the message + code
 *   - ``"disabled"`` → call out the 503 envelope and let the parent
 *                      render the legacy local typeahead beside it
 *
 * The dropdown is the same ``.kx-search-pop`` shell the legacy
 * typeahead uses, so it slots into the existing header without
 * layout churn.
 */

import type { ReactElement } from "react";

import type {
  ExploreSearchChunk,
  ExploreSearchDocument,
  ExploreSearchTopic,
} from "../api/types";
import type { ExploreSearchSnapshot } from "../state/use-explore-search";

export type SearchHitKind = "doc" | "chunk" | "topic";

export interface SearchHit {
  kind: SearchHitKind;
  /** ``document_id`` / ``chunk_id`` / ``topic_id`` depending on kind. */
  id: string;
  /** Used to drive the existing DetailPanel selection. */
  documentId?: string;
}

export interface SearchResultsProps {
  snapshot: ExploreSearchSnapshot;
  /** Toggle for the "validated only" filter (default ``true``). */
  validatedOnly: boolean;
  onToggleValidated: (next: boolean) => void;
  onPick: (hit: SearchHit) => void;
}

function formatScore(score: number): string {
  return `${(score * 100).toFixed(1)}%`;
}

function trustLabel(validationStatus: string | null, isSourceBacked: boolean): string {
  if (validationStatus === "VALIDATED") return "validated";
  if (isSourceBacked) return "source-backed";
  if (validationStatus === "REJECTED") return "rejected";
  return "candidate";
}

function isVisible(
  validationStatus: string | null,
  isSourceBacked: boolean,
  validatedOnly: boolean,
): boolean {
  if (!validatedOnly) return true;
  return validationStatus === "VALIDATED" || isSourceBacked;
}

export function SearchResults({
  snapshot,
  validatedOnly,
  onToggleValidated,
  onPick,
}: SearchResultsProps): ReactElement | null {
  if (snapshot.state === "idle") {
    return null;
  }

  return (
    <div className="kx-search-pop" data-testid="kx-search-pop" data-state={snapshot.state}>
      <div className="kx-search-toolbar">
        <label className="kx-search-toggle">
          <input
            type="checkbox"
            checked={validatedOnly}
            onChange={(e) => onToggleValidated(e.target.checked)}
            data-testid="kx-search-validated-toggle"
          />
          <span>Validated / source-backed only</span>
        </label>
        {snapshot.response !== null && (
          <span className="kx-mute kx-mono kx-search-meta">
            {snapshot.response.embedding_model}
          </span>
        )}
      </div>

      {snapshot.state === "loading" && (
        <div className="kx-search-empty" data-testid="kx-search-loading">
          Searching…
        </div>
      )}

      {snapshot.state === "disabled" && (
        <div className="kx-search-empty" data-testid="kx-search-disabled">
          <strong>Vector search is disabled.</strong>
          <div className="kx-mute">
            Set <code>KW_KNOWLEDGE_LAYER_ENABLED=true</code> and{" "}
            <code>VOYAGE_API_KEY</code> to enable. Local search still works.
          </div>
        </div>
      )}

      {snapshot.state === "error" && (
        <div className="kx-search-empty kx-search-error" role="alert" data-testid="kx-search-error">
          {snapshot.error instanceof Error
            ? snapshot.error.message
            : typeof snapshot.error === "string"
              ? snapshot.error
              : "Search failed."}
        </div>
      )}

      {snapshot.state === "empty" && (
        <div className="kx-search-empty" data-testid="kx-search-empty">
          No matches for &quot;<b>{snapshot.query}</b>&quot;
        </div>
      )}

      {snapshot.state === "data" && snapshot.response !== null && (
        <DataSections
          response={snapshot.response}
          validatedOnly={validatedOnly}
          onPick={onPick}
        />
      )}
    </div>
  );
}

interface DataSectionsProps {
  response: NonNullable<ExploreSearchSnapshot["response"]>;
  validatedOnly: boolean;
  onPick: (hit: SearchHit) => void;
}

function DataSections({ response, validatedOnly, onPick }: DataSectionsProps): ReactElement {
  const visibleDocs = response.documents.filter((d) =>
    isVisible(d.validation_status, d.is_source_backed, validatedOnly),
  );
  // Chunks: filter by the chunk's validation_status when populated; in
  // v0.1 it's always null, so the validated-only toggle effectively
  // hides chunks until v0.2 lights up chunk-level trust. That's the
  // documented contract on the backend.
  const visibleChunks = response.chunks.filter((c) =>
    isVisible(c.validation_status, c.is_source_backed, validatedOnly),
  );

  const totalVisible = visibleDocs.length + visibleChunks.length + response.topics.length;
  if (totalVisible === 0) {
    return (
      <div className="kx-search-empty" data-testid="kx-search-empty-after-filter">
        No <b>validated</b> matches. Toggle the filter to widen.
      </div>
    );
  }

  return (
    <>
      <DocumentSection items={visibleDocs} onPick={onPick} />
      <ChunkSection items={visibleChunks} onPick={onPick} />
      <TopicSection items={response.topics} onPick={onPick} />
    </>
  );
}

function DocumentSection({
  items,
  onPick,
}: {
  items: ExploreSearchDocument[];
  onPick: (hit: SearchHit) => void;
}): ReactElement | null {
  if (items.length === 0) return null;
  return (
    <div className="kx-search-sec" data-testid="kx-search-section-documents">
      <div className="kx-search-h">DOCUMENTS · {items.length}</div>
      {items.map((d) => (
        <button
          key={d.document_id}
          type="button"
          className="kx-search-row kx-search-row--btn"
          onClick={() => onPick({ kind: "doc", id: d.document_id, documentId: d.document_id })}
          data-testid="kx-search-row-document"
        >
          <span className="kx-search-row-score kx-mono">{formatScore(d.score)}</span>
          <span className="kx-search-row-title">{d.title}</span>
          <span className="kx-search-row-trust">
            {trustLabel(d.validation_status, d.is_source_backed)}
          </span>
        </button>
      ))}
    </div>
  );
}

function ChunkSection({
  items,
  onPick,
}: {
  items: ExploreSearchChunk[];
  onPick: (hit: SearchHit) => void;
}): ReactElement | null {
  if (items.length === 0) return null;
  return (
    <div className="kx-search-sec" data-testid="kx-search-section-chunks">
      <div className="kx-search-h">CHUNKS · {items.length}</div>
      {items.map((c) => (
        <button
          key={c.chunk_id}
          type="button"
          className="kx-search-row kx-search-row--btn"
          onClick={() =>
            onPick({ kind: "chunk", id: c.chunk_id, documentId: c.document_id })
          }
          data-testid="kx-search-row-chunk"
        >
          <span className="kx-search-row-score kx-mono">{formatScore(c.score)}</span>
          <span className="kx-search-row-title">
            {c.snippet ?? <code className="kx-mono">{c.chunk_id}</code>}
          </span>
        </button>
      ))}
    </div>
  );
}

function TopicSection({
  items,
  onPick,
}: {
  items: ExploreSearchTopic[];
  onPick: (hit: SearchHit) => void;
}): ReactElement | null {
  if (items.length === 0) return null;
  return (
    <div className="kx-search-sec" data-testid="kx-search-section-topics">
      <div className="kx-search-h">TOPICS · {items.length}</div>
      {items.map((t) => (
        <button
          key={t.topic_id}
          type="button"
          className="kx-search-row kx-search-row--btn"
          onClick={() => onPick({ kind: "topic", id: t.topic_id })}
          data-testid="kx-search-row-topic"
        >
          <span className="kx-search-row-score kx-mono">{formatScore(t.score)}</span>
          <span className="kx-search-row-title">{t.label}</span>
          {t.keywords.length > 0 && (
            <span className="kx-search-row-keywords kx-mute">
              {t.keywords.slice(0, 3).join(" · ")}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
