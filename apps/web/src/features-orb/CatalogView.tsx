import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, listDocuments } from "../api/client";
import type { components } from "../api/generated/schema";

import {
  type BatchSnapshot,
  pruneSelectionAfterBatch,
  runBatchPipeline,
} from "./batch";
import {
  CatalogRail,
  type CatalogView as ViewId,
  viewToStatuses,
} from "./CatalogRail";
import { OrbChatPanel } from "./ChatPanel";
import { DocPage } from "./DocPage";
import { OrbPurgeAllDialog } from "./PurgeDialogs";
import { OrbSearchPanel } from "./SearchPanel";
import { OrbShell, type OrbNavItem } from "./Shell";

type ApiDocument = components["schemas"]["Document"];

const SEARCH_DEBOUNCE_MS = 300;

const VIEW_TITLES: Record<ViewId, string> = {
  recent: "Recent documents",
  review: "Awaiting review",
  validated: "Validated documents",
  failed: "Failed documents",
};

type SortCol = "filename" | "uploaded" | "status";
type SortDir = "asc" | "desc";

/**
 * The `/orb` route. Variant-A shell + rail + main canvas. Selecting a
 * row in the rail flips the main canvas from the catalog "no document"
 * placeholder to <DocPage>, which owns the breadcrumbs, dochead,
 * Linked-view / Pipeline tabs, and FSM actions.
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
  const [purgeAllOpen, setPurgeAllOpen] = useState(false);
  const [nav, setNav] = useState<OrbNavItem>("review");
  const [sort, setSort] = useState<{ col: SortCol; dir: SortDir }>({
    col: "uploaded",
    dir: "desc",
  });
  const abortRef = useRef<AbortController | null>(null);

  // Debounce the filename filter.
  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedQuery(query.trim()), SEARCH_DEBOUNCE_MS);
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

  const toggleSort = useCallback((col: SortCol) => {
    setSort((current) =>
      current.col === col
        ? { col, dir: current.dir === "asc" ? "desc" : "asc" }
        : { col, dir: col === "filename" ? "asc" : "desc" },
    );
  }, []);

  const handleNav = (next: OrbNavItem) => {
    setNav(next);
    if (next === "admin") window.location.assign("/orb/admin");
  };

  const failures = batchSnapshot?.failures ?? [];
  const progress = batchSnapshot?.progress;
  const title = useMemo(() => VIEW_TITLES[view], [view]);

  return (
    <OrbShell
      activeNav={nav}
      onNav={handleNav}
      buildVersion="v0.1.0-preview.2"
      rail={
        <CatalogRail
          documents={documents}
          loading={loading}
          view={view}
          onView={setView}
          query={query}
          onQuery={setQuery}
          counts={{}}
          selectedId={selectedId}
          onSelect={setSelectedId}
          selection={batchSelection}
          onToggleBatch={toggleBatch}
          onClearBatch={clearBatch}
          onRunBatch={runBatch}
          batchRunning={batchRunning}
          batchProgress={progress}
          sort={sort}
          onSort={toggleSort}
        />
      }
    >
      {nav === "search" ? (
        <OrbSearchPanel onSelectResult={(id) => { setSelectedId(id); setNav("review"); }} />
      ) : nav === "chat" ? (
        <OrbChatPanel onSelectCitation={(id) => { setSelectedId(id); setNav("review"); }} />
      ) : selectedId ? (
        <DocPage
          documentId={selectedId}
          onBack={() => setSelectedId(null)}
          onMutated={() => void refresh()}
        />
      ) : error ? (
        <div className="orb-banner orb-banner--err" role="alert" style={{ borderRadius: 6 }}>
          Failed to load catalog: {error}
        </div>
      ) : (
        <div>
          <h1 className="rwA-title">{title}</h1>
          <p style={{ color: "var(--orb-fg-muted)", fontSize: 13, margin: "8px 0 0" }}>
            Pick a document from the rail to open the review surface. {documents.length} document(s) match the current view.
          </p>
          {failures.length > 0 && (
            <div className="rwA-batchbanner">
              <div className="rwA-batchbanner-h">
                <span className="icon">⚠</span>
                <b>Batch pipeline</b>
                <span className="orb-mono rwA-hint">
                  {Object.values(batchSnapshot?.progress ?? {}).filter((s) => s.stage === "done").length} done · {failures.length} failed
                </span>
                <span style={{ flex: 1 }} />
                <button
                  type="button"
                  className="rwA-link"
                  onClick={() => setBatchSnapshot(null)}
                >
                  dismiss
                </button>
              </div>
              <div className="rwA-batchbanner-fail">
                {failures.map((failure) => (
                  <div key={failure.document_id} className="orb-mono">
                    <span style={{ color: "var(--orb-err)" }}>✗</span> {failure.document_id.slice(0, 8)} ·{" "}
                    <span style={{ color: "var(--orb-fg-muted)" }}>{failure.reason}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="rwA-foot">
            <span>Documents · {documents.length.toLocaleString()}</span>
            <span>·</span>
            <span>view {view}</span>
            {debouncedQuery && (
              <>
                <span>·</span>
                <span>q "{debouncedQuery}"</span>
              </>
            )}
            <span style={{ flex: 1 }} />
            <button
              type="button"
              className="rwA-link"
              onClick={() => setPurgeAllOpen(true)}
              style={{ color: "var(--orb-err-fg)" }}
            >
              purge all…
            </button>
            <span>⌘K commands</span>
            <span>⌘/ search</span>
            <span>j/k row · v validate · r reject</span>
          </div>
        </div>
      )}

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
