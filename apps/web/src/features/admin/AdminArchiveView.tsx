/**
 * Admin UI — Archive view (D.9, ADR-027 §1.4).
 *
 * Paginated table of flag-archived documents with three per-row
 * actions and one bulk action:
 *
 * 1. **Unarchive** — confirmation modal → POST `/admin/archive/unarchive`
 *    with `?confirm=true`. Reactivates the document on the standard
 *    read path. Idempotent on already-active rows (route returns 200
 *    with `archived_at_before === null`).
 *
 * 2. **Relink scope…** — modal pre-filled from the row's
 *    ``last_active_scope_*`` → dry-run preview → real
 *    `POST /admin/archive/relink_scope` (ADR-027 §1.2 / #269).
 *    Reactivates a soft-removed ``document_scopes`` row. See
 *    ``RelinkModal`` for the form behaviour.
 *
 * 3. **Purge artifacts** — preview modal that first calls the route
 *    with `?dry_run=true` so the operator sees the per-version
 *    tombstone URI list + the freed-bytes estimate. Confirming flips
 *    to `?confirm=true` and renders the real result. Irreversible —
 *    the modal's CTA copy says so verbatim.
 *
 * 4. **Bulk Purge selected (N)…** — per-row checkboxes drive a
 *    bulk action bar above the table. Clicking opens
 *    ``BulkPurgeModal`` which runs the dry-run-then-real flow against
 *    `POST /admin/archive/purge_batch` (ADR-027 §4 / #273). Capped at
 *    100 docs per batch (mirrored client-side so the CTA disables
 *    rather than tripping the 422).
 *
 * No client-side role check: the UI fires the request and renders a
 * "Forbidden" state if the backend responds 403 (`KW_FORBIDDEN`).
 * That keeps the role enforcement single-sourced on the server
 * (ADR-019 §3 / #264) — the frontend never has a stale view of the
 * user's role.
 *
 * UX decisions worth flagging:
 *
 * - The dry-run preview is the **only** path to the real purge. Users
 *   can't skip the preview by clicking faster — the "Permanently
 *   delete" CTA only renders after the dry-run resolves.
 * - The confirmation modal for *unarchive* is just a yes/no — there's
 *   no reversible-state checkbox because the action *is* the reverse.
 * - The action buttons are enabled even on rows where every version
 *   is already PURGED. The dry-run preview will show "0 bytes to free"
 *   and the operator can decide whether to skip; there's no point
 *   gating the click since the catalog row is the audit-keeping trace
 *   (a no-op purge call is idempotent and documented).
 * - The bulk action bar above the table is hidden until ≥ 1 row is
 *   selected. That keeps the table chrome quiet for the common
 *   single-row workflow.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  listArchivedDocuments,
  purgeArtifacts,
  unarchiveDocument,
} from "../../api/client";
import type {
  ApiArchivedDocumentItem,
  ApiPurgeArtifactsResponse,
} from "../../api/types";
import { BulkPurgeModal } from "./BulkPurgeModal";
import { ModalShell } from "./ModalShell";
import { RelinkModal } from "./RelinkModal";

/** Pull the error message off any thrown unknown into a string the
 *  inline notice can render. Centralised because every modal does it. */
function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return fallback;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Render an archived-at timestamp as "N hours/days/months ago".
 *
 * Localised wall-clock formatting is overkill for the admin tool: the
 * relative phrase is what an operator scans for ("two hours ago"
 * means "the cascade fired during the morning incident"). Falls
 * back to the raw ISO string for very-old archives where "months
 * ago" stops being precise enough. */
export function formatRelativeArchived(
  isoString: string,
  now: Date = new Date(),
): string {
  const archived = new Date(isoString);
  const ms = now.getTime() - archived.getTime();
  if (Number.isNaN(ms) || ms < 0) return isoString;
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months === 1 ? "" : "s"} ago`;
  // Far enough back that the relative phrase loses precision; fall
  // back to the date portion so the operator at least sees a year.
  return archived.toISOString().slice(0, 10);
}

/** Format the scope-removed cell. ``"—"`` placeholder when no link
 *  history is recoverable. */
export function formatScopeRemoved(item: ApiArchivedDocumentItem): string {
  const kind = item.last_active_scope_kind;
  const ref = item.last_active_scope_ref;
  if (kind === null || kind === undefined || ref === null || ref === undefined) {
    return "—";
  }
  return `${kind}:${ref}`;
}

// ─── Unarchive confirmation modal ────────────────────────────────────────────

interface UnarchiveModalProps {
  item: ApiArchivedDocumentItem;
  onClose: () => void;
  onCompleted: () => void | Promise<void>;
}

function UnarchiveModal({
  item,
  onClose,
  onCompleted,
}: UnarchiveModalProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = useCallback(() => {
    setBusy(true);
    setError(null);
    unarchiveDocument(item.document_id)
      .then(async () => {
        await onCompleted();
        onClose();
      })
      .catch((err: unknown) => {
        setError(errorMessage(err, "Unarchive failed."));
      })
      .finally(() => setBusy(false));
  }, [item.document_id, onCompleted, onClose]);

  return (
    <ModalShell title="Restore document?" onClose={onClose}>
      <p>
        Restore <strong>{item.original_filename}</strong> to active reads?
      </p>
      <p className="muted">
        The document will reappear on the standard catalog after this
        action. Already-purged versions stay purged.
      </p>
      {error !== null ? (
        <div className="notice danger" role="alert">
          <strong>Unarchive failed</strong>
          <span>{error}</span>
        </div>
      ) : null}
      <div className="action-row">
        <button
          type="button"
          className="secondary-button"
          onClick={onClose}
          disabled={busy}
        >
          Cancel
        </button>
        <button
          type="button"
          className="primary-button"
          onClick={handleConfirm}
          disabled={busy}
          aria-busy={busy}
        >
          {busy ? "Restoring…" : "Restore"}
        </button>
      </div>
    </ModalShell>
  );
}

// ─── Purge dry-run preview + confirm modal ───────────────────────────────────

interface PurgeModalProps {
  item: ApiArchivedDocumentItem;
  onClose: () => void;
  onCompleted: () => void | Promise<void>;
}

function PurgeModal({ item, onClose, onCompleted }: PurgeModalProps) {
  // Two-phase modal: first the dry-run loads the impact preview;
  // confirming flips to the real call. The "Permanently delete" CTA
  // only renders once the dry-run resolves so the operator cannot
  // skip the preview phase by clicking faster.
  const [preview, setPreview] = useState<ApiPurgeArtifactsResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [purging, setPurging] = useState(false);
  const [purgeError, setPurgeError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPreviewLoading(true);
    setPreviewError(null);
    purgeArtifacts(item.document_id, { dryRun: true })
      .then((response) => {
        if (cancelled) return;
        setPreview(response);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setPreviewError(errorMessage(err, "Failed to preview purge impact."));
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [item.document_id]);

  const handleConfirm = useCallback(() => {
    setPurging(true);
    setPurgeError(null);
    purgeArtifacts(item.document_id, { dryRun: false })
      .then(async () => {
        await onCompleted();
        onClose();
      })
      .catch((err: unknown) => {
        setPurgeError(errorMessage(err, "Purge failed."));
      })
      .finally(() => setPurging(false));
  }, [item.document_id, onCompleted, onClose]);

  // Bytes-estimate rollup. ``bytes_estimate`` is None on a tombstone'd
  // version (already-PURGED rows) — sum the non-null values.
  const bytesTotal = useMemo(() => {
    if (preview === null) return 0;
    return preview.versions_purged.reduce(
      (sum, version) => sum + (version.bytes_estimate ?? 0),
      0,
    );
  }, [preview]);

  const versionsToPurge = useMemo(() => {
    if (preview === null) return [];
    return preview.versions_purged.filter(
      (version) => version.status_before !== "PURGED",
    );
  }, [preview]);

  return (
    <ModalShell title="Purge document artifacts?" onClose={onClose}>
      {previewLoading ? (
        <p className="muted" role="status" aria-live="polite">
          Loading impact preview…
        </p>
      ) : previewError !== null ? (
        <div className="notice danger" role="alert">
          <strong>Preview failed</strong>
          <span>{previewError}</span>
        </div>
      ) : preview !== null ? (
        <>
          <div className="notice danger" role="alert">
            <strong>Irreversible.</strong> This will permanently delete the
            bytes for {versionsToPurge.length}{" "}
            {versionsToPurge.length === 1 ? "version" : "versions"}. The
            catalog rows will be preserved as audit traces.
          </div>
          <p>
            Document: <strong>{item.original_filename}</strong>
          </p>
          <dl className="purge-preview">
            <div>
              <dt>Versions to purge</dt>
              <dd data-testid="purge-versions-count">
                {versionsToPurge.length}
              </dd>
            </div>
            <div>
              <dt>Estimated bytes freed</dt>
              <dd data-testid="purge-bytes-total">{bytesTotal}</dd>
            </div>
          </dl>
          {versionsToPurge.length > 0 ? (
            <details className="purge-tombstone-list">
              <summary>Tombstone URIs that will be created</summary>
              <ul>
                {versionsToPurge.map((version) => (
                  <li key={version.version_id}>
                    <code>{version.tombstone_uri}</code>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      ) : null}
      {purgeError !== null ? (
        <div className="notice danger" role="alert">
          <strong>Purge failed</strong>
          <span>{purgeError}</span>
        </div>
      ) : null}
      <div className="action-row">
        <button
          type="button"
          className="secondary-button"
          onClick={onClose}
          disabled={purging}
        >
          Cancel
        </button>
        {/* The destructive CTA only shows after a successful dry-run.
            That removes the "double-click to skip preview" foot-gun. */}
        {preview !== null ? (
          <button
            type="button"
            className="primary-button danger"
            onClick={handleConfirm}
            disabled={purging}
            aria-busy={purging}
          >
            {purging ? "Purging…" : "Permanently delete"}
          </button>
        ) : null}
      </div>
    </ModalShell>
  );
}

// ─── Filter / sort state ─────────────────────────────────────────────────────

/**
 * Sort modes the filter bar exposes. Applied client-side against the
 * already-fetched page (cursor pagination is preserved by the backend
 * — see TODO below).
 *
 * - ``recent``  — ``archived_at DESC`` (default; matches the route's
 *   server-side default so the no-filter view doesn't re-shuffle).
 * - ``oldest``  — ``archived_at ASC``.
 * - ``most-purged`` — ``versions_purged DESC``; surfaces the "biggest
 *   bytes-freed wins" so an operator hunting storage can scan top-down.
 *
 * TODO(#274 follow-up): the route ``GET /admin/archive/archived_documents``
 * doesn't accept ``?q=`` / ``?sort=`` yet — when it does, drop the
 * client-side filter for the matching server-side params (the catalog
 * list endpoint already uses this shape with ``?q=`` / ``?status=``).
 * Until then the search+sort applies only to the visible page; with
 * the cursor still pointing at the next page from the unfiltered set.
 */
export type ArchiveSortMode = "recent" | "oldest" | "most-purged";

const DEFAULT_SORT_MODE: ArchiveSortMode = "recent";

/** Apply the client-side filter + sort to the visible page. Pure so
 *  the test suite can pin the ordering without re-mounting the view. */
export function filterAndSortItems(
  items: readonly ApiArchivedDocumentItem[],
  query: string,
  sort: ArchiveSortMode,
): ApiArchivedDocumentItem[] {
  const q = query.trim().toLowerCase();
  const filtered =
    q === ""
      ? items.slice()
      : items.filter((it) =>
          it.original_filename.toLowerCase().includes(q),
        );
  switch (sort) {
    case "oldest":
      filtered.sort((a, b) => a.archived_at.localeCompare(b.archived_at));
      break;
    case "most-purged":
      filtered.sort((a, b) => b.versions_purged - a.versions_purged);
      break;
    case "recent":
    default:
      filtered.sort((a, b) => b.archived_at.localeCompare(a.archived_at));
      break;
  }
  return filtered;
}

// ─── Main view ───────────────────────────────────────────────────────────────

type ModalState =
  | { kind: "none" }
  | { kind: "unarchive"; item: ApiArchivedDocumentItem }
  | { kind: "purge"; item: ApiArchivedDocumentItem }
  | { kind: "relink"; item: ApiArchivedDocumentItem }
  | { kind: "bulk-purge"; documentIds: string[] };

interface ToastState {
  message: string;
  kind: "success" | "danger";
}

export function AdminArchiveView() {
  const [items, setItems] = useState<ApiArchivedDocumentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<ApiError | string | null>(null);
  const [modal, setModal] = useState<ModalState>({ kind: "none" });
  const [toast, setToast] = useState<ToastState | null>(null);
  // Bulk multi-select state. Set<document_id> of currently checked rows.
  // Cleared whenever the list reloads so a refreshed table starts clean
  // (also defends against a stale id surviving an unarchive/purge).
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  // Client-side filter bar state. The backend route doesn't accept
  // ``?q=`` / ``?sort=`` yet (#274 follow-up), so we filter the
  // already-fetched visible page in-memory.
  const [filterQuery, setFilterQuery] = useState("");
  const [sortMode, setSortMode] = useState<ArchiveSortMode>(DEFAULT_SORT_MODE);

  const loadList = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      // TODO(#274 follow-up): pass ``q`` / ``sort`` to the backend
      // once ``GET /admin/archive/archived_documents`` supports them
      // (matches the catalog list's ``?q=`` / ``?status=`` shape).
      // Until then ``filterQuery`` + ``sortMode`` are applied
      // client-side via ``filterAndSortItems``.
      const page = await listArchivedDocuments();
      setItems(page.items);
      setSelectedIds(new Set());
    } catch (err: unknown) {
      if (err instanceof ApiError) setLoadError(err);
      else if (err instanceof Error) setLoadError(err.message);
      else setLoadError("Failed to load archived documents.");
    } finally {
      setLoading(false);
    }
  }, []);

  const visibleItems = useMemo(
    () => filterAndSortItems(items, filterQuery, sortMode),
    [items, filterQuery, sortMode],
  );

  const handleResetFilters = useCallback(() => {
    setFilterQuery("");
    setSortMode(DEFAULT_SORT_MODE);
  }, []);

  const filtersActive =
    filterQuery.trim() !== "" || sortMode !== DEFAULT_SORT_MODE;

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const handleUnarchiveCompleted = useCallback(async () => {
    setToast({ message: "Document restored.", kind: "success" });
    await loadList();
  }, [loadList]);

  const handlePurgeCompleted = useCallback(async () => {
    setToast({ message: "Artifacts purged.", kind: "success" });
    await loadList();
  }, [loadList]);

  const handleRelinkCompleted = useCallback(async () => {
    setToast({ message: "Scope link reactivated.", kind: "success" });
    await loadList();
  }, [loadList]);

  const handleBulkPurgeCompleted = useCallback(
    async (toastMessage: string) => {
      setToast({ message: toastMessage, kind: "success" });
      await loadList();
    },
    [loadList],
  );

  const toggleRow = useCallback((documentId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(documentId)) next.delete(documentId);
      else next.add(documentId);
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    setSelectedIds((prev) => {
      // If every visible row is already selected, clear; otherwise
      // select all visible rows. Mirrors the semantic of a tri-state
      // header checkbox without needing an indeterminate visual.
      // "Visible" honours the client-side filter — selecting-all on
      // a filtered page only checks the filtered rows, which matches
      // the operator's mental model when they've narrowed the table.
      if (prev.size === visibleItems.length && visibleItems.length > 0) {
        return new Set();
      }
      return new Set(visibleItems.map((it) => it.document_id));
    });
  }, [visibleItems]);

  const allSelected =
    visibleItems.length > 0 && selectedIds.size === visibleItems.length;
  const selectedCount = selectedIds.size;

  // Forbidden state: the backend's 403 on a non-admin caller is the
  // sole signal we ever consult about role. We don't try to derive
  // it from the token client-side (the frontend has no token-decoder
  // and the server is the source of truth).
  if (loadError instanceof ApiError && loadError.status === 403) {
    return (
      <main className="app-shell admin-shell" aria-label="Admin archive view">
        <section className="workspace">
          <header className="workspace-header">
            <h2>Forbidden</h2>
          </header>
          <p>This view requires the <code>admin</code> role.</p>
          <p className="muted">{loadError.detail}</p>
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell admin-shell" aria-label="Admin archive view">
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Admin</p>
            <h2>Archived Documents</h2>
          </div>
          <button
            type="button"
            className="secondary-button"
            onClick={() => void loadList()}
            disabled={loading}
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
        </header>

        {toast !== null ? (
          <div
            className={`notice ${toast.kind === "success" ? "" : "danger"}`}
            role="status"
            aria-live="polite"
          >
            <span>{toast.message}</span>
            <button
              type="button"
              className="text-button"
              onClick={() => setToast(null)}
            >
              Dismiss
            </button>
          </div>
        ) : null}

        {loadError !== null && !(loadError instanceof ApiError) ? (
          <div className="notice danger" role="alert">
            <strong>Failed to load</strong>
            <span>{loadError}</span>
          </div>
        ) : loadError instanceof ApiError ? (
          <div className="notice danger" role="alert">
            <strong>Failed to load</strong>
            <span>{loadError.detail}</span>
          </div>
        ) : null}

        {/* Filter bar — client-side search + sort + reset. Renders
            even when the page is empty so the controls don't pop in
            and out. The filter is applied locally; see TODO on
            ``loadList`` for the future server-side ``?q=`` / ``?sort=``
            params. */}
        {!loading && loadError === null ? (
          <div
            className="action-row admin-archive-filter-bar"
            data-testid="admin-archive-filter-bar"
          >
            <label className="admin-archive-filter-search">
              <span className="muted">Search filename</span>
              <input
                type="search"
                value={filterQuery}
                onChange={(e) => setFilterQuery(e.target.value)}
                placeholder="filename substring"
                data-testid="admin-archive-filter-search"
              />
            </label>
            <label className="admin-archive-filter-sort">
              <span className="muted">Sort</span>
              <select
                value={sortMode}
                onChange={(e) =>
                  setSortMode(e.target.value as ArchiveSortMode)
                }
                data-testid="admin-archive-filter-sort"
              >
                <option value="recent">Recently archived</option>
                <option value="oldest">Oldest first</option>
                <option value="most-purged">Most versions purged</option>
              </select>
            </label>
            <button
              type="button"
              className="text-button"
              onClick={handleResetFilters}
              disabled={!filtersActive}
              data-testid="admin-archive-filter-reset"
            >
              Reset filters
            </button>
          </div>
        ) : null}

        {loading ? (
          <p className="muted" role="status" aria-live="polite">
            Loading…
          </p>
        ) : items.length === 0 ? (
          <p className="muted">No archived documents.</p>
        ) : visibleItems.length === 0 ? (
          // Filter bar narrowed the page to nothing. Distinct copy from
          // the truly-empty state so the operator knows it's a filter
          // result, not an empty archive.
          <p className="muted" data-testid="admin-archive-empty-filtered">
            No archived documents match the search.
          </p>
        ) : (
          <>
            {/* Bulk action bar — only renders when ≥ 1 row is selected.
                Hidden otherwise to keep the table chrome quiet for the
                common single-row workflow. */}
            {selectedCount > 0 ? (
              <div
                className="action-row admin-archive-bulk-bar"
                data-testid="admin-archive-bulk-bar"
              >
                <span className="muted">{selectedCount} selected</span>
                <button
                  type="button"
                  className="primary-button danger"
                  onClick={() =>
                    setModal({
                      kind: "bulk-purge",
                      documentIds: Array.from(selectedIds),
                    })
                  }
                >
                  Purge selected ({selectedCount})…
                </button>
                <button
                  type="button"
                  className="text-button"
                  onClick={() => setSelectedIds(new Set())}
                >
                  Clear
                </button>
              </div>
            ) : null}
            <table className="admin-archive-table">
              <thead>
                <tr>
                  <th scope="col" className="select-col">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      aria-label="Select all rows"
                      data-testid="admin-archive-select-all"
                    />
                  </th>
                  <th scope="col">Filename</th>
                  <th scope="col">Scope removed</th>
                  <th scope="col">Versions</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {visibleItems.map((item) => {
                  const totalVersions =
                    item.versions_remaining + item.versions_purged;
                  const isSelected = selectedIds.has(item.document_id);
                  return (
                    <tr key={item.document_id} data-testid="admin-archive-row">
                      <td className="select-col">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleRow(item.document_id)}
                          aria-label={`Select ${item.original_filename}`}
                          data-testid="admin-archive-row-select"
                        />
                      </td>
                      <td>
                        <div>
                          <strong>{item.original_filename}</strong>
                        </div>
                        <div className="muted" data-testid="row-archived-relative">
                          {formatRelativeArchived(item.archived_at)}
                        </div>
                      </td>
                      <td data-testid="row-scope-removed">
                        {formatScopeRemoved(item)}
                      </td>
                      <td data-testid="row-version-counts">
                        {item.versions_remaining} / {totalVersions}
                      </td>
                      <td>
                        <div className="action-row">
                          <button
                            type="button"
                            className="secondary-button"
                            onClick={() =>
                              setModal({ kind: "unarchive", item })
                            }
                          >
                            Unarchive
                          </button>
                          <button
                            type="button"
                            className="secondary-button"
                            onClick={() =>
                              setModal({ kind: "relink", item })
                            }
                          >
                            Relink scope…
                          </button>
                          <button
                            type="button"
                            className="secondary-button"
                            onClick={() => setModal({ kind: "purge", item })}
                          >
                            Purge…
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}
      </section>

      {modal.kind === "unarchive" ? (
        <UnarchiveModal
          item={modal.item}
          onClose={() => setModal({ kind: "none" })}
          onCompleted={handleUnarchiveCompleted}
        />
      ) : null}
      {modal.kind === "purge" ? (
        <PurgeModal
          item={modal.item}
          onClose={() => setModal({ kind: "none" })}
          onCompleted={handlePurgeCompleted}
        />
      ) : null}
      {modal.kind === "relink" ? (
        <RelinkModal
          item={modal.item}
          onClose={() => setModal({ kind: "none" })}
          onCompleted={handleRelinkCompleted}
        />
      ) : null}
      {modal.kind === "bulk-purge" ? (
        <BulkPurgeModal
          documentIds={modal.documentIds}
          onClose={() => setModal({ kind: "none" })}
          onCompleted={handleBulkPurgeCompleted}
        />
      ) : null}
    </main>
  );
}
