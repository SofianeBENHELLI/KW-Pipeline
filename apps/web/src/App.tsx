import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { Route, Routes } from "react-router-dom";
import "./styles.css";
import {
  SessionExpiredBanner,
  useSessionGuard,
} from "../../_shared/auth";
import {
  ApiError,
  clearSessionTrigger,
  getApiBaseUrl,
  getDocument,
  listDocuments,
  setSessionTrigger,
} from "./api/client";
import type { ApiDocument } from "./api/types";
import { useAdminConfig } from "./api/useAdminConfig";
import { ChatPanel } from "./features/chat";
import { PipelineWidget } from "./features/pipeline/PipelineWidget";
import { ReviewWorkspace } from "./features/review/ReviewWorkspace";
import { SearchPanel } from "./features/search";
import { SettingsLauncher, SettingsModal } from "./features/settings/SettingsModal";
import { ForceAutoCorpusBanner } from "./ui/ForceAutoCorpusBanner";

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

/**
 * Top-level app router (D.9 + #215 + #206 follow-up).
 *
 * Admin routes (each lazy-loaded into its own chunk):
 *  - ``/admin/archive`` — Archive listing + per-doc actions (D.9).
 *  - ``/admin/hitl`` — HITL routing dashboard (#215, EPIC-A close-out).
 *  - ``/admin/audit`` — Audit log viewer (#206 follow-up).
 *
 * Each handler 403s on a non-admin token and the page renders a
 * "Forbidden" state for that envelope. We never derive admin role
 * client-side — the backend is the single source of truth.
 *
 * Everything else falls through to the legacy reviewer workbench.
 */
// Lazy-load the admin views so they don't ship in the initial app
// chunk — admin routes are admin-only and most users never land here.
// Keeps the index bundle under the 80 KB budget enforced by
// `scripts/check-bundle-size.mjs`. Each admin page lives in its own
// chunk so a power user only pays for what they navigate to.
const AdminArchiveView = lazy(() =>
  import("./features/admin/AdminArchiveView").then((mod) => ({
    default: mod.AdminArchiveView,
  })),
);
const AdminHITLView = lazy(() =>
  import("./features/admin/AdminHITLView").then((mod) => ({
    default: mod.AdminHITLView,
  })),
);
const AdminAuditView = lazy(() =>
  import("./features/admin/AdminAuditView").then((mod) => ({
    default: mod.AdminAuditView,
  })),
);
const AdminHubView = lazy(() =>
  import("./features/admin/AdminHubView").then((mod) => ({
    default: mod.AdminHubView,
  })),
);

// Shared Suspense fallback for every lazy admin route. Hoisted out
// of the JSX so the literal isn't inlined three times in the initial
// chunk (the budget enforcer is tight).
const ADMIN_FALLBACK = <div className="kw-loading">Loading admin view…</div>;

export default function App() {
  return (
    <Routes>
      {/* Bare ``/admin`` is the navigation hub — explicit, no implicit
          redirect to ``/admin/archive``. The hub lists every admin
          sub-tool so an operator landing on /admin sees the full
          surface, not whichever sub-page we picked first. */}
      <Route
        path="/admin"
        element={
          <Suspense fallback={ADMIN_FALLBACK}>
            <AdminHubView />
          </Suspense>
        }
      />
      <Route
        path="/admin/archive"
        element={
          <Suspense fallback={ADMIN_FALLBACK}>
            <AdminArchiveView />
          </Suspense>
        }
      />
      <Route
        path="/admin/hitl"
        element={
          <Suspense fallback={ADMIN_FALLBACK}>
            <AdminHITLView />
          </Suspense>
        }
      />
      <Route
        path="/admin/audit"
        element={
          <Suspense fallback={<div className="kw-loading">Loading admin view…</div>}>
            <AdminAuditView />
          </Suspense>
        }
      />
      <Route path="*" element={<ReviewerWorkbench />} />
    </Routes>
  );
}

function ReviewerWorkbench() {
  const catalog = useDocumentCatalog();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const session = useSessionGuard();
  // EPIC-A A.8 (#215, ADR-023 §6): the corpus-wide force-auto override
  // is surfaced via /admin/config so operators see a non-dismissible
  // banner whenever the deployment is auto-validating every version.
  // Hidden for non-admin users (403) and on fetch errors — the banner
  // is informational and a transient hiccup shouldn't block the app.
  const adminConfig = useAdminConfig(getApiBaseUrl());
  const forceAutoActive =
    adminConfig.status === "ok" && adminConfig.config?.hitl.force_auto_corpus === true;

  // Register the 401-triggered session-expired hook on mount and tear
  // it down on unmount. The trigger is module-level state on the API
  // client (#83 slice 3 / ADR-019 §5) so any code path that throws an
  // ApiError(401) — openapi-fetch's unwrap, the multipart upload's
  // asApiError branch, anything else — flips this banner without
  // per-call-site branching.
  useEffect(() => {
    setSessionTrigger(session.trigger);
    return () => {
      clearSessionTrigger();
    };
  }, [session.trigger]);

  // Dev stub: ``KW_AUTH_MODE=dev`` (default per #245) never returns
  // 401 in normal operation, so reviewers can't see the banner via
  // the live backend. Loading the app with ``#force-session-expired``
  // in the URL hash flips the banner once on mount so the affordance
  // stays reviewable on a demo build. Removed once bearer mode is
  // the default and real 401s show up organically.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.location.hash === "#force-session-expired") {
      session.trigger();
    }
  }, [session]);

  // Sign-in action behaviour:
  //   * dev mode (default): reload picks up a fresh dev user on the
  //     very next request — see ADR-019 §3.
  //   * bearer mode: reload bounces the user through whatever IdP
  //     redirect their token issuer wires up. Until the future
  //     refresh-token slice (ADR-019 follow-up), reload is the only
  //     thing the frontend can do — it has no sign-in form of its
  //     own (out of scope for #83 slice 3).
  const handleSignInAgain = useCallback(() => {
    if (typeof window !== "undefined") window.location.reload();
  }, []);
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

  const handleMutationCompleted = useCallback(async () => {
    await Promise.all([refreshSelected(), refreshAll()]);
    bumpMutation();
  }, [refreshAll, refreshSelected, bumpMutation]);

  // Banners sit at the top of every shell return — loading, error,
  // and ready states all need to surface a 401 the same way.
  // The force-auto banner sits above the session-expired banner so
  // an operator's "every version is auto" alert is visible even
  // when their session has just timed out.
  const banner = (
    <>
      <ForceAutoCorpusBanner visible={forceAutoActive} />
      <SessionExpiredBanner
        visible={session.expired}
        onSignIn={handleSignInAgain}
      />
    </>
  );

  if (loadingDocuments && documents.length === 0) {
    return (
      <main className="app-shell" aria-label="Orbital document review workbench">
        {banner}
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
        {banner}
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
      {banner}
      <PipelineWidget
        documents={documents}
        selectedDocumentId={selectedId ?? ""}
        onSelectDocument={selectDocument}
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
          <p className="muted">No documents found. Import documents from the Forge widget to get started.</p>
        </section>
      )}
      <SearchPanel
        onSelectResult={(result) => selectDocument(result.document_id)}
      />
      <ChatPanel
        onSelectCitation={(citation) => selectDocument(citation.document_id)}
      />
      <SettingsLauncher onClick={() => setSettingsOpen(true)} />
      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />
    </main>
  );
}
