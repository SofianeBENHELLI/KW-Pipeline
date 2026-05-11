import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, listDocuments } from "../api/client";
import type { components } from "../api/generated/schema";

import { CatalogRail, type CatalogView as ViewId, viewToStatuses } from "./CatalogRail";
import { CatalogTable } from "./CatalogTable";
import { OrbShell } from "./Shell";

type ApiDocument = components["schemas"]["Document"];

const SEARCH_DEBOUNCE_MS = 300;

/**
 * Phase-1 catalog view — the `/orb` route's main entry point. Wires the
 * new shell to the real backend via `listDocuments`. Saved-view filters
 * map onto the existing `status[]` query param; the search input is
 * debounced. Selecting a row sets local state only; deep wiring into
 * the review workspace is Phase 2 territory.
 */
export function OrbCatalogView() {
  const [view, setView] = useState<ViewId>("recent");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [documents, setDocuments] = useState<ApiDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Debounce the filename filter so each keystroke doesn't fan out into
  // its own GET /documents request.
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
      if (!controller.signal.aborted) {
        setDocuments(response.items ?? []);
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : String(err);
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

  return (
    <OrbShell rail={<CatalogRail view={view} onView={setView} query={query} onQuery={setQuery} />}>
      <div className="orb-catalog">
        <div className="orb-catalog__head">
          <h1 className="orb-catalog__title">
            {view === "recent"
              ? "Recent documents"
              : view === "review"
                ? "Awaiting review"
                : view === "validated"
                  ? "Validated documents"
                  : "Failed documents"}
          </h1>
          <span className="orb-catalog__meta">
            {documents.length} shown
            {loading && documents.length > 0 ? " · refreshing" : ""}
          </span>
        </div>
        <CatalogTable
          documents={documents}
          loading={loading}
          error={error}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
        <div className="orb-catalog__footer">
          <span>
            View: <strong>{view}</strong>
            {debouncedQuery ? ` · filtered by "${debouncedQuery}"` : ""}
          </span>
          <span className="orb-catalog__footer-spacer" />
          <span className="orb-mono">GET /documents</span>
        </div>
      </div>
    </OrbShell>
  );
}
