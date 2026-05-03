import { useCallback, useEffect, useRef, useState } from "react";
import "./styles.css";
import { ApiError, getDocument, listDocuments } from "./api/client";
import type { ApiDocument } from "./api/types";
import { PipelineWidget } from "./features/pipeline/PipelineWidget";
import { ReviewWorkspace } from "./features/review/ReviewWorkspace";

/**
 * Centralised document-catalog hook.
 *
 * Owns the list of documents, the currently-selected document, and the
 * refresh primitives that mutating actions (upload / extract / generate /
 * validate / reject) call after they succeed. Concurrent calls to the
 * same refresh path dedup onto a single in-flight promise so a flurry
 * of mutations doesn't fan out into N parallel network calls.
 *
 * `lastMutationAt` is bumped after every successful mutation so child
 * panels (notably <KnowledgeGraphView>) can take it as a `refreshKey`
 * prop and refetch their own derived state without us coordinating
 * directly with them.
 */
/** Catalog filter state surfaced by ``useDocumentCatalog`` (#86). */
export interface CatalogFilter {
  /** Empty array = no status filter. */
  status: string[];
  /** Empty string = no filename filter. */
  q: string;
}

export const EMPTY_CATALOG_FILTER: CatalogFilter = { status: [], q: "" };

export interface DocumentCatalog {
  documents: ApiDocument[];
  selected: ApiDocument | null;
  selectedId: string | null;
  loadingDocuments: boolean;
  loadingSelected: boolean;
  error: ApiError | string | null;
  refreshError: string | null;
  lastMutationAt: number;
  filter: CatalogFilter;
  setFilter: (next: CatalogFilter) => void;
  refreshAll: () => Promise<void>;
  refreshSelected: () => Promise<void>;
  selectDocument: (id: string | null) => void;
  bumpMutation: () => void;
}

export function useDocumentCatalog(): DocumentCatalog {
  const [documents, setDocuments] = useState<ApiDocument[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loadingDocuments, setLoadingDocuments] = useState(true);
  const [loadingSelected, setLoadingSelected] = useState(false);
  const [error, setError] = useState<ApiError | string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [lastMutationAt, setLastMutationAt] = useState(0);
  const [filter, setFilterState] = useState<CatalogFilter>(EMPTY_CATALOG_FILTER);

  // Mirror filter state into a ref so the in-flight dedup callbacks
  // see the latest values without forcing the callbacks themselves to
  // re-create on every keystroke.
  const filterRef = useRef<CatalogFilter>(EMPTY_CATALOG_FILTER);
  filterRef.current = filter;

  // In-flight dedup. We hold raw Promises so a second concurrent caller
  // short-circuits onto the existing one rather than firing another fetch.
  const listInFlight = useRef<Promise<void> | null>(null);
  const selectedInFlight = useRef<Map<string, Promise<void>>>(new Map());

  const refreshAll = useCallback(async (): Promise<void> => {
    if (listInFlight.current !== null) return listInFlight.current;
    const task = (async () => {
      try {
        const { status, q } = filterRef.current;
        const page = await listDocuments({
          status: status.length > 0 ? status : undefined,
          q: q || undefined,
        });
        setDocuments(page.items);
        setError(null);
        setRefreshError(null);
        // If nothing is selected yet, default to the first document.
        setSelectedId((current) => {
          if (current !== null) return current;
          return page.items.length > 0 ? page.items[0].id : null;
        });
      } catch (err: unknown) {
        if (err instanceof ApiError) setError(err);
        else if (err instanceof Error) setError(err.message);
        else setError("Failed to load documents.");
      } finally {
        listInFlight.current = null;
      }
    })();
    listInFlight.current = task;
    return task;
  }, []);

  const refreshSelected = useCallback(async (): Promise<void> => {
    const id = selectedId;
    if (id === null) return;
    const existing = selectedInFlight.current.get(id);
    if (existing) return existing;

    setLoadingSelected(true);
    setRefreshError(null);
    const task = (async () => {
      try {
        const fresh = await getDocument(id);
        // Keep the previously-loaded document visible on failure; only
        // overwrite on success.
        setDocuments((prev) => {
          const existsInList = prev.some((d) => d.id === fresh.id);
          if (!existsInList) return [fresh, ...prev];
          return prev.map((d) => (d.id === fresh.id ? fresh : d));
        });
      } catch (err: unknown) {
        const message =
          err instanceof Error ? err.message : "Failed to refresh document.";
        setRefreshError(message);
      } finally {
        selectedInFlight.current.delete(id);
        setLoadingSelected(false);
      }
    })();
    selectedInFlight.current.set(id, task);
    return task;
  }, [selectedId]);

  const selectDocument = useCallback((id: string | null) => {
    setSelectedId(id);
  }, []);

  const bumpMutation = useCallback(() => {
    setLastMutationAt(Date.now());
  }, []);

  const setFilter = useCallback((next: CatalogFilter) => {
    setFilterState(next);
  }, []);

  // Refresh whenever filter changes — drops any in-flight cursor
  // pagination since the cursor's semantics are "next page within
  // the current filter set".
  useEffect(() => {
    let cancelled = false;
    setLoadingDocuments(true);
    setError(null);
    const { status, q } = filter;
    listDocuments({
      status: status.length > 0 ? status : undefined,
      q: q || undefined,
    })
      .then((page) => {
        if (cancelled) return;
        setDocuments(page.items);
        if (page.items.length > 0) {
          setSelectedId((current) => {
            // Keep the selection if it's still in the filtered list,
            // otherwise default to the first matching document.
            if (current !== null && page.items.some((d) => d.id === current)) {
              return current;
            }
            return page.items[0].id;
          });
        } else {
          setSelectedId(null);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError) setError(err);
        else if (err instanceof Error) setError(err.message);
        else setError("Failed to load documents.");
      })
      .finally(() => {
        if (!cancelled) setLoadingDocuments(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filter]);

  const selected = documents.find((d) => d.id === selectedId) ?? null;

  return {
    documents,
    selected,
    selectedId,
    loadingDocuments,
    loadingSelected,
    error,
    refreshError,
    lastMutationAt,
    filter,
    setFilter,
    refreshAll,
    refreshSelected,
    selectDocument,
    bumpMutation,
  };
}

export default function App() {
  const catalog = useDocumentCatalog();
  const {
    documents,
    selected,
    selectedId,
    loadingDocuments,
    loadingSelected,
    error,
    refreshError,
    lastMutationAt,
    filter,
    setFilter,
    refreshAll,
    refreshSelected,
    selectDocument,
    bumpMutation,
  } = catalog;

  const handleUploaded = useCallback(
    async (newDocumentId: string) => {
      await refreshAll();
      selectDocument(newDocumentId);
      bumpMutation();
    },
    [refreshAll, selectDocument, bumpMutation],
  );

  const handleMutationCompleted = useCallback(async () => {
    await Promise.all([refreshSelected(), refreshAll()]);
    bumpMutation();
  }, [refreshAll, refreshSelected, bumpMutation]);

  if (loadingDocuments && documents.length === 0) {
    return (
      <main className="app-shell" aria-label="Orbital document review workbench">
        <p className="muted" role="status" aria-live="polite">
          Loading documents…
        </p>
      </main>
    );
  }

  if (error !== null && documents.length === 0) {
    const message = error instanceof ApiError ? error.detail : error;
    return (
      <main className="app-shell" aria-label="Orbital document review workbench">
        <div className="notice danger" role="alert">
          <strong>Failed to load documents</strong>
          <span>{message}</span>
          <button
            className="text-button"
            type="button"
            onClick={() => void refreshAll()}
          >
            Retry
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="app-shell" aria-label="Orbital document review workbench">
      <PipelineWidget
        documents={documents}
        selectedDocumentId={selectedId ?? ""}
        onSelectDocument={selectDocument}
        onUploaded={handleUploaded}
        filter={filter}
        onFilterChange={setFilter}
      />
      {selected !== null ? (
        <ReviewWorkspace
          document={selected}
          loadingSelected={loadingSelected}
          refreshError={refreshError}
          lastMutationAt={lastMutationAt}
          onMutationCompleted={handleMutationCompleted}
        />
      ) : (
        <section className="workspace">
          <p className="muted">No documents found. Upload a document to get started.</p>
        </section>
      )}
    </main>
  );
}
