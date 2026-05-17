import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import "./styles.css";
import {
  SessionExpiredBanner,
  useSessionGuard,
} from "../../_shared/auth";
import {
  ApiError,
  clearSessionTrigger,
  extractVersion,
  generateSemantic,
  getApiBaseUrl,
  getDocument,
  listDocuments,
  setSessionTrigger,
} from "./api/client";
import type { ApiDocument } from "./api/types";
import { useAdminConfig } from "./api/useAdminConfig";
import { ChatPanel } from "./features/chat";
// Knowledge Forge — full Orbital redesign. New route family `/kf/*`
// shipped over PRs 1-8 (codename Orbital, user-visible Knowledge
// Forge). The legacy reviewer workbench remains the `*` catch-all
// until the user signals the cutover; the previous `/orb*` preview
// (PR #414) was retired in PR 8 of the redesign.
const KnowledgeForgeApp = lazy(() =>
  import("./orb").then((m) => ({ default: m.KnowledgeForgeApp })),
);
import { PipelineWidget } from "./features/pipeline/PipelineWidget";
import { PurgeAllDialog } from "./features/purge/PurgeAllDialog";
import { PurgeDialog } from "./features/purge/PurgeDialog";
import { ReviewWorkspace } from "./features/review/ReviewWorkspace";
import { SearchPanel } from "./features/search";
import { SettingsLauncher } from "./features/settings/SettingsLauncher";
// SettingsModal pulls in the shared DemoToggle (446 LOC) + the admin
// config form, which is only needed when the user clicks the gear.
// Lazy-load it so the initial chunk stays under its bundle budget
// (#125). The Suspense boundary lives on the modal mount-point below.
const SettingsModal = lazy(() =>
  import("./features/settings/SettingsModal").then((m) => ({
    default: m.SettingsModal,
  })),
);
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

export const RECENT_IMPORT_STATUSES = [
  "STORED",
  "EXTRACTING",
  "EXTRACTED",
  "SEMANTIC_READY",
  "NEEDS_REVIEW",
] as const;

export const EMPTY_CATALOG_FILTER: CatalogFilter = {
  status: [...RECENT_IMPORT_STATUSES],
  q: "",
};

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
  /**
   * Document id supplied via the ``?document=…`` deep link from Forge
   * (#292 §4) that did not resolve to any row in the loaded catalog.
   * Surfaced as a dismissible banner so the operator gets a clear "we
   * couldn't open the doc you asked for" signal instead of silently
   * landing on the empty workspace.
   */
  deepLinkError: string | null;
  /** Dismiss the deep-link error banner. */
  clearDeepLinkError: () => void;
  /**
   * One-shot trigger for the catalog list to scroll the selected row
   * into view. Bumped when the deep link auto-selects on mount; the
   * widget consumes the value via ``useEffect`` and resets locally.
   */
  scrollSelectedToken: number;
}

/** Per-document batch pipeline progress (#292 §3 follow-up). */
export type BatchItemStatus =
  | "queued"
  | "extracting"
  | "semantic"
  | "done"
  | "failed";

export interface BatchItemState {
  status: BatchItemStatus;
  /** Populated when ``status === "failed"``. */
  reason?: string;
}

export interface BatchFailure {
  document_id: string;
  filename: string;
  reason: string;
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

  // #292 §4 / b7c5898 — Forge ships an ``Open in Orbital`` button that
  // redirects to ``/?document=doc-…``. We capture the param once on
  // mount (so a subsequent refresh doesn't re-trigger the auto-select),
  // strip it from the URL via ``history.replaceState``, and remember
  // the requested id in a ref so we can surface a 404 banner if the
  // doc isn't in the catalog after the first list load completes.
  const [deepLinkError, setDeepLinkError] = useState<string | null>(null);
  const [scrollSelectedToken, setScrollSelectedToken] = useState(0);
  const deepLinkRequestIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const documentId = params.get("document");
    if (!documentId) return;
    deepLinkRequestIdRef.current = documentId;
    setSelectedId(documentId);
    setScrollSelectedToken((token) => token + 1);
    // Drop the query param so a refresh after navigation doesn't
    // reopen this same row, and the URL stays clean for sharing.
    params.delete("document");
    const next = `${window.location.pathname}${
      params.toString() ? `?${params.toString()}` : ""
    }${window.location.hash}`;
    window.history.replaceState(window.history.state, "", next);
  }, []);

  // After the catalog finishes its first load, validate that the
  // deep-link id (if any) actually resolved. If not, surface a
  // dismissible banner — silently landing on the empty workspace was
  // the half-done shape b7c5898 left behind.
  useEffect(() => {
    if (loadingDocuments) return;
    const requested = deepLinkRequestIdRef.current;
    if (!requested) return;
    deepLinkRequestIdRef.current = null;
    const found = documents.some((d) => d.id === requested);
    if (!found) {
      setDeepLinkError(
        `Document ${requested} could not be found in the catalog.`,
      );
      // Clear the dangling selection so ReviewWorkspace doesn't render
      // a half-empty pane.
      setSelectedId(null);
    }
  }, [documents, loadingDocuments]);

  const clearDeepLinkError = useCallback(() => {
    setDeepLinkError(null);
  }, []);

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
    deepLinkError,
    clearDeepLinkError,
    scrollSelectedToken,
  };
}

/**
 * Top-level app router (D.9 + #215 + #206 follow-up).
 *
 * Admin routes (each lazy-loaded into its own chunk):
 *  - ``/admin/archive`` — Archive listing + per-doc actions (D.9).
 *  - ``/admin/hitl`` — HITL routing dashboard (#215, EPIC-A close-out).
 *  - ``/admin/audit`` — Audit log viewer (#206 follow-up).
 *  - ``/admin/taxonomy`` — Taxonomy versions lineage (EPIC-1 §1.9).
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
const AdminTaxonomyView = lazy(() =>
  import("./features/admin/AdminTaxonomyView").then((mod) => ({
    default: mod.AdminTaxonomyView,
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
      <Route
        path="/admin/taxonomy"
        element={
          <Suspense fallback={ADMIN_FALLBACK}>
            <AdminTaxonomyView />
          </Suspense>
        }
      />
      {/* Knowledge Forge — full redesign shipped over PRs 1–8 of the
          Orbital → Knowledge Forge sprint. As of the cutover, the
          root URL redirects straight onto the new shell so deep
          links from 3DDashboard / the widget land on the redesign.
          The legacy reviewer workbench is preserved at /legacy/* as
          a one-page-refresh escape hatch in case Knowledge Forge
          surfaces a regression in production. */}
      <Route
        path="/kf/*"
        element={
          <Suspense fallback={<div className="kw-loading">Loading Knowledge Forge…</div>}>
            <KnowledgeForgeApp />
          </Suspense>
        }
      />
      <Route path="/legacy/*" element={<ReviewerWorkbench />} />
      {/* Default landing → Knowledge Forge. Preserve any `?document=…`
          deep-link query string so widgets that bookmark /?document=X
          keep working — Knowledge Forge's review route picks the
          param up the same way. */}
      <Route path="*" element={<RootRedirect />} />
    </Routes>
  );
}

/**
 * Forward the root URL (and every other unmatched path) onto the
 * Knowledge Forge shell at /kf/review, preserving any `?document=…`
 * deep-link query string the widget / external bookmarks shipped
 * with. Done as a component (not a `<Navigate to=…>` literal) so the
 * forwarded URL can be computed at render time from
 * `window.location.search`.
 */
function RootRedirect(): ReactElement {
  const search =
    typeof window !== "undefined" ? window.location.search : "";
  return <Navigate to={`/kf/review${search}`} replace />;
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
  // Guard ``hitl`` too, not just ``config``: tests (and any future
  // partial-shape upstream response) can return an admin-config-shaped
  // body without the HITL block, and the bare ``.hitl.force_auto_corpus``
  // access would crash the whole tree. This banner is informational —
  // a missing block should fall through to "not active", not a render crash.
  const forceAutoActive =
    adminConfig.status === "ok" &&
    adminConfig.config?.hitl?.force_auto_corpus === true;

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
    deepLinkError,
    clearDeepLinkError,
    scrollSelectedToken,
  } = catalog;

  // #292 §5 — purge dialog targets. ``null`` keeps the per-row modal
  // hidden; the bulk modal toggles via its own boolean.
  const [purgeTarget, setPurgeTarget] = useState<ApiDocument | null>(null);
  const [purgeAllOpen, setPurgeAllOpen] = useState(false);
  const [selectedBatchIds, setSelectedBatchIds] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchMessage, setBatchMessage] = useState<string | null>(null);
  const [batchProgress, setBatchProgress] = useState<ReadonlyMap<string, BatchItemState>>(
    () => new Map(),
  );
  const [batchFailures, setBatchFailures] = useState<ReadonlyArray<BatchFailure>>([]);

  const handlePurgeRequest = useCallback((document: ApiDocument) => {
    setPurgeTarget(document);
  }, []);

  const handlePurgeAllRequest = useCallback(() => {
    setPurgeAllOpen(true);
  }, []);

  const handlePurged = useCallback(async () => {
    setPurgeTarget(null);
    await refreshAll();
    bumpMutation();
  }, [bumpMutation, refreshAll]);

  const handlePurgedAll = useCallback(async () => {
    setPurgeAllOpen(false);
    await refreshAll();
    bumpMutation();
  }, [bumpMutation, refreshAll]);

  const handleMutationCompleted = useCallback(async () => {
    await Promise.all([refreshSelected(), refreshAll()]);
    bumpMutation();
  }, [refreshAll, refreshSelected, bumpMutation]);

  const handleToggleBatchDocument = useCallback((documentId: string, checked: boolean) => {
    setSelectedBatchIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(documentId);
      else next.delete(documentId);
      return next;
    });
  }, []);

  const handleClearBatchSelection = useCallback(() => {
    setSelectedBatchIds(new Set());
    setBatchMessage(null);
    setBatchProgress(new Map());
    setBatchFailures([]);
  }, []);

  const handleRunBatchPipeline = useCallback(async () => {
    const targets = documents.filter((doc) => selectedBatchIds.has(doc.id));
    if (targets.length === 0 || batchBusy) return;
    setBatchBusy(true);
    setBatchMessage(null);
    setBatchFailures([]);

    // Seed the progress map with every selected doc as ``queued`` so
    // the operator sees the full intent before the loop starts.
    const progress = new Map<string, BatchItemState>(
      targets.map((doc) => [doc.id, { status: "queued" }]),
    );
    setBatchProgress(new Map(progress));

    const setItem = (id: string, state: BatchItemState) => {
      progress.set(id, state);
      setBatchProgress(new Map(progress));
    };

    let completed = 0;
    const failures: BatchFailure[] = [];
    const failedIds = new Set<string>();

    try {
      for (const doc of targets) {
        const version =
          doc.versions.find((v) => v.id === doc.latest_version_id) ?? doc.versions[0];
        if (!version) {
          setItem(doc.id, {
            status: "failed",
            reason: "No version available on this document.",
          });
          failures.push({
            document_id: doc.id,
            filename: doc.original_filename,
            reason: "No version available on this document.",
          });
          failedIds.add(doc.id);
          continue;
        }
        try {
          if (version.status === "STORED") {
            setItem(doc.id, { status: "extracting" });
            await extractVersion(doc.id, version.id);
            setItem(doc.id, { status: "semantic" });
            await generateSemantic(doc.id, version.id);
            completed += 1;
            setItem(doc.id, { status: "done" });
          } else if (
            version.status === "EXTRACTED" ||
            version.status === "SEMANTIC_READY" ||
            version.status === "NEEDS_REVIEW"
          ) {
            setItem(doc.id, { status: "semantic" });
            await generateSemantic(doc.id, version.id);
            completed += 1;
            setItem(doc.id, { status: "done" });
          } else {
            // Status that the pipeline can't operate on (e.g. FAILED,
            // VALIDATED, REJECTED, DUPLICATE_DETECTED). Mark explicitly
            // so the row pill shows "skipped" instead of "queued".
            setItem(doc.id, {
              status: "failed",
              reason: `Cannot run pipeline from status "${version.status}".`,
            });
            failures.push({
              document_id: doc.id,
              filename: doc.original_filename,
              reason: `Cannot run pipeline from status "${version.status}".`,
            });
            failedIds.add(doc.id);
          }
        } catch (err: unknown) {
          const message =
            err instanceof ApiError
              ? err.detail
              : err instanceof Error
                ? err.message
                : "Pipeline step failed.";
          setItem(doc.id, { status: "failed", reason: message });
          failures.push({
            document_id: doc.id,
            filename: doc.original_filename,
            reason: message,
          });
          failedIds.add(doc.id);
        }
      }
      await Promise.all([refreshSelected(), refreshAll()]);
      bumpMutation();
      // Keep failed rows checked so the operator can hit "Run selected
      // pipeline" again to retry them in one click. Succeeded rows
      // drop out of the selection.
      setSelectedBatchIds(new Set(failedIds));
      setBatchMessage(
        `Semantic pipeline completed for ${completed} document${completed === 1 ? "" : "s"}.`,
      );
      setBatchFailures(failures);
    } finally {
      setBatchBusy(false);
    }
  }, [
    batchBusy,
    bumpMutation,
    documents,
    refreshAll,
    refreshSelected,
    selectedBatchIds,
  ]);

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
      <DeepLinkErrorBanner message={deepLinkError} onDismiss={clearDeepLinkError} />
      <PipelineWidget
        documents={documents}
        selectedDocumentId={selectedId ?? ""}
        onSelectDocument={selectDocument}
        filter={filter}
        onFilterChange={setFilter}
        onPurgeRequest={handlePurgeRequest}
        onPurgeAllRequest={handlePurgeAllRequest}
        selectedBatchIds={selectedBatchIds}
        batchBusy={batchBusy}
        batchMessage={batchMessage}
        batchProgress={batchProgress}
        batchFailures={batchFailures}
        onToggleBatchDocument={handleToggleBatchDocument}
        onRunBatchPipeline={() => void handleRunBatchPipeline()}
        onClearBatchSelection={handleClearBatchSelection}
        scrollSelectedToken={scrollSelectedToken}
      />
      <PurgeDialog
        document={
          purgeTarget
            ? {
                id: purgeTarget.id,
                original_filename: purgeTarget.original_filename,
                version_count: purgeTarget.versions.length,
              }
            : null
        }
        onCancel={() => setPurgeTarget(null)}
        onPurged={() => void handlePurged()}
      />
      <PurgeAllDialog
        open={purgeAllOpen}
        documentCount={documents.length}
        onCancel={() => setPurgeAllOpen(false)}
        onPurged={() => void handlePurgedAll()}
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
      {/* The modal only mounts (and only fetches its lazy chunk) the
          first time the user opens it. Suspense fallback is null
          because the launcher already gives the operator a hold —
          no spinner needed for what's effectively a button click. */}
      {settingsOpen && (
        <Suspense fallback={null}>
          <SettingsModal
            open={settingsOpen}
            onClose={() => setSettingsOpen(false)}
            onCorpusRefreshNeeded={() => {
              // Transitional Demo toggle (apps/_shared/demo-toggle): re-fetch
              // the document list once the bundled loader finishes or the
              // dataset is reset, so the pipeline widget / review workspace
              // / search panel reflect the new corpus on the next render.
              void refreshAll();
              bumpMutation();
            }}
          />
        </Suspense>
      )}
    </main>
  );
}

interface DeepLinkErrorBannerProps {
  message: string | null;
  onDismiss: () => void;
}

function DeepLinkErrorBanner({ message, onDismiss }: DeepLinkErrorBannerProps) {
  if (!message) return null;
  return (
    <div
      className="deep-link-error-banner"
      role="alert"
      data-testid="deep-link-error-banner"
    >
      <span>{message}</span>
      <button
        type="button"
        className="deep-link-error-dismiss"
        onClick={onDismiss}
        aria-label="Dismiss deep link error"
      >
        Dismiss
      </button>
    </div>
  );
}
