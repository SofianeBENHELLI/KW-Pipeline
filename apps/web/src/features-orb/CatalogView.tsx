import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, listDocuments } from "../api/client";
import type { components } from "../api/generated/schema";
import { Btn, Icon } from "../ui/orb";

import {
  type BatchSnapshot,
  pruneSelectionAfterBatch,
  runBatchPipeline,
} from "./batch";
import { CatalogRail, type CatalogView as ViewId, viewToStatuses } from "./CatalogRail";
import { CatalogTable } from "./CatalogTable";
import { OrbChatPanel } from "./ChatPanel";
import { OrbPurgeAllDialog } from "./PurgeDialogs";
import { ReviewPane } from "./ReviewPane";
import { OrbSearchPanel } from "./SearchPanel";
import { OrbShell, type ShellAside } from "./Shell";

type ApiDocument = components["schemas"]["Document"];

const SEARCH_DEBOUNCE_MS = 300;

const VIEW_TITLES: Record<ViewId, string> = {
  recent: "Recent documents",
  review: "Awaiting review",
  validated: "Validated documents",
  failed: "Failed documents",
};

/**
 * Phase-1/2/3 catalog view — the `/orb` route's entry point. Wires the
 * new shell to the real backend via `listDocuments`. Saved-view filters
 * map onto the existing `status[]` query param; the search input is
 * debounced. Selecting a row opens the review pane (Phase 2); selecting
 * rows via checkbox arms the batch run bar (Phase 3).
 */
export function OrbCatalogView() {
  const [view, setView] = useState<ViewId>("recent");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [documents, setDocuments] = useState<ApiDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [batchSelection, setBatchSelection] = useState<Set<string>>(new Set());
  const [batchSnapshot, setBatchSnapshot] = useState<BatchSnapshot | null>(null);
  const [batchRunning, setBatchRunning] = useState(false);
  const [aside, setAside] = useState<ShellAside>(null);
  const [purgeAllOpen, setPurgeAllOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const handleAsideSelect = useCallback((documentId: string) => {
    setSelectedId(documentId);
    setAside(null);
  }, []);

  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedQuery(query), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query]);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const response = await listDocuments({
        status: viewToStatuses(view),
        q: debouncedQuery,
        limit: 50,
      });
      if (!controller.signal.aborted) setDocuments(response.items ?? []);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      setError(message);
      setDocuments([]);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [view, debouncedQuery]);

  useEffect(() => {
    refresh();
    return () => abortRef.current?.abort();
  }, [refresh]);

  const toggleBatch = useCallback((id: string, next: boolean) => {
    setBatchSelection((current) => {
      const updated = new Set(current);
      if (next) updated.add(id);
      else updated.delete(id);
      return updated;
    });
  }, []);

  const clearBatch = useCallback(() => {
    setBatchSelection(new Set());
    setBatchSnapshot(null);
  }, []);

  const runBatch = useCallback(async () => {
    if (batchRunning || batchSelection.size === 0) return;
    const targets = documents.filter((doc) => batchSelection.has(doc.id));
    if (targets.length === 0) return;
    setBatchRunning(true);
    try {
      const finalSnapshot = await runBatchPipeline(targets, (next) => {
        setBatchSnapshot((prev) =>
          typeof next === "function" ? next(prev ?? { progress: {}, failures: [] }) : next,
        );
      });
      setBatchSelection((current) => pruneSelectionAfterBatch(current, finalSnapshot));
      await refresh();
    } finally {
      setBatchRunning(false);
    }
  }, [batchRunning, batchSelection, documents, refresh]);

  const selectionCount = batchSelection.size;
  const failures = batchSnapshot?.failures ?? [];
  const progress = batchSnapshot?.progress;
  const title = useMemo(() => VIEW_TITLES[view], [view]);

  return (
    <OrbShell
      rail={<CatalogRail view={view} onView={setView} query={query} onQuery={setQuery} />}
      aside={aside}
      onAsideChange={setAside}
      asideContent={
        aside === "search" ? (
          <OrbSearchPanel onSelectResult={handleAsideSelect} />
        ) : aside === "chat" ? (
          <OrbChatPanel onSelectCitation={handleAsideSelect} />
        ) : null
      }
    >
      <div className={selectedId ? "orb-canvas--split" : "orb-canvas--full"}>
        <div className="orb-catalog">
          <div className="orb-catalog__head">
            <h1 className="orb-catalog__title">{title}</h1>
            <span className="orb-catalog__meta">
              {documents.length} shown{loading && documents.length > 0 ? " · refreshing" : ""}
            </span>
            <span className="orb-catalog__head-spacer" />
            {selectionCount > 0 && (
              <div className="orb-catalog__batchbar">
                <span className="orb-mono orb-catalog__batchbar-count">
                  {selectionCount} selected
                </span>
                <button
                  type="button"
                  className="orb-btn orb-btn--ghost orb-btn--xs"
                  onClick={clearBatch}
                  disabled={batchRunning}
                >
                  Clear
                </button>
                <Btn
                  kind="primary"
                  size="xs"
                  icon={<Icon name="bolt" />}
                  onClick={runBatch}
                  disabled={batchRunning}
                >
                  {batchRunning ? "Running…" : "Run pipeline"}
                </Btn>
              </div>
            )}
          </div>
          <CatalogTable
            documents={documents}
            loading={loading}
            error={error}
            selectedId={selectedId}
            onSelect={setSelectedId}
            selection={batchSelection}
            onToggleBatch={toggleBatch}
            batchProgress={progress}
          />
          {failures.length > 0 && (
            <div className="orb-catalog__failures" role="status">
              <strong>{failures.length} document(s) failed during the last batch run:</strong>
              <ul>
                {failures.map((failure) => (
                  <li key={failure.document_id}>
                    <span className="orb-mono orb-catalog__failures-id">{failure.document_id.slice(0, 8)}</span>
                    <span> {failure.filename} — </span>
                    <span className="orb-catalog__failures-reason">{failure.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="orb-catalog__footer">
            <span>
              View: <strong>{view}</strong>
              {debouncedQuery ? ` · filtered by "${debouncedQuery}"` : ""}
            </span>
            <span className="orb-catalog__footer-spacer" />
            <button
              type="button"
              className="orb-btn orb-btn--ghost orb-btn--xs"
              onClick={() => setPurgeAllOpen(true)}
              style={{ color: "var(--orb-err-fg)" }}
            >
              Purge all…
            </button>
            <span className="orb-mono">GET /documents</span>
          </div>
        </div>
        {selectedId && (
          <aside className="orb-canvas__review orb-scroll">
            <div className="orb-canvas__review-head">
              <button
                type="button"
                className="orb-btn orb-btn--ghost orb-btn--xs"
                onClick={() => setSelectedId(null)}
                aria-label="Close review pane"
              >
                ← Back to catalog
              </button>
            </div>
            <ReviewPane documentId={selectedId} onMutated={() => void refresh()} />
          </aside>
        )}
      </div>
      <OrbPurgeAllDialog
        open={purgeAllOpen}
        onClose={() => setPurgeAllOpen(false)}
        onConfirmed={() => {
          setSelectedId(null);
          setBatchSelection(new Set());
          setBatchSnapshot(null);
          void refresh();
        }}
      />
    </OrbShell>
  );
}
