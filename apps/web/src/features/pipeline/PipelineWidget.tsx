import { useEffect, useMemo, useRef } from "react";
import type { ApiDocument } from "../../api/types";
import type { BatchFailure, BatchItemState } from "../../App";
import { documentScopes, latestVersion } from "../../domain/document";
import { ScopeChip } from "../../ui/ScopeChip";
import { StatusBadge } from "../../ui/StatusBadge";

interface PipelineWidgetProps {
  documents: ApiDocument[];
  selectedDocumentId: string;
  onSelectDocument: (id: string) => void;
  /** Active catalog filter (#86); ``undefined`` keeps the legacy "no filter" UX. */
  filter?: { status: string[]; q: string };
  /** Filter setter — required when ``filter`` is provided. */
  onFilterChange?: (next: { status: string[]; q: string }) => void;
  /** #292 §5 — when set, each row shows a Purge button that opens the modal. */
  onPurgeRequest?: (document: ApiDocument) => void;
  /** #292 §5 — when set, a "Purge all" button appears in the header. */
  onPurgeAllRequest?: () => void;
  /** Selected documents for the batch semantic pipeline. */
  selectedBatchIds?: ReadonlySet<string>;
  batchBusy?: boolean;
  batchMessage?: string | null;
  /**
   * Per-document batch progress map keyed by ``document_id`` (#292 §3
   * follow-up). Each entry's ``status`` drives the per-row pill so the
   * operator sees the loop's progress instead of just a global busy
   * spinner. ``undefined`` (or a doc not in the map) means the row is
   * inactive and the pill is hidden.
   */
  batchProgress?: ReadonlyMap<string, BatchItemState>;
  /**
   * Structured per-document failure list rendered after a batch run.
   * Replaces the previous joined-string ``batchError`` so the operator
   * sees one bullet per failed doc with its filename and reason.
   */
  batchFailures?: ReadonlyArray<BatchFailure>;
  onToggleBatchDocument?: (id: string, checked: boolean) => void;
  onRunBatchPipeline?: () => void;
  onClearBatchSelection?: () => void;
  /**
   * One-shot trigger from the deep-link mount path: when this token
   * changes, the widget scrolls the currently-selected row into view.
   * Kept as a number (not a boolean) so the parent can fire the same
   * intent twice without having to flip-and-reset a flag.
   */
  scrollSelectedToken?: number;
}

/**
 * Saved-view definitions for the segmented filter bar (#86 / #292).
 *
 * Recent (#292) groups newly-imported and in-progress statuses so an
 * operator lands on the documents that are ready to split, extract, or
 * semantically generate. Review/Validated/Failed remain the canonical
 * terminal lenses.
 */
const SAVED_VIEWS: ReadonlyArray<{ id: string; label: string; statuses: string[] }> = [
  {
    id: "stored",
    label: "Recent",
    statuses: [
      "STORED",
      "EXTRACTING",
      "EXTRACTED",
      "SEMANTIC_READY",
      "NEEDS_REVIEW",
    ],
  },
  { id: "review", label: "Review", statuses: ["NEEDS_REVIEW", "DUPLICATE_DETECTED"] },
  { id: "validated", label: "Validated", statuses: ["VALIDATED"] },
  { id: "failed", label: "Failed", statuses: ["FAILED", "REJECTED"] },
];

export function PipelineWidget({
  documents,
  selectedDocumentId,
  onSelectDocument,
  filter,
  onFilterChange,
  onPurgeRequest,
  onPurgeAllRequest,
  selectedBatchIds,
  batchBusy = false,
  batchMessage = null,
  batchProgress,
  batchFailures,
  onToggleBatchDocument,
  onRunBatchPipeline,
  onClearBatchSelection,
  scrollSelectedToken,
}: PipelineWidgetProps) {
  const selectedRowRef = useRef<HTMLDivElement | null>(null);
  const lastFiredScrollTokenRef = useRef(0);

  // Deep-link scroll trigger (#292 §4 follow-up). The parent bumps
  // ``scrollSelectedToken`` once on the deep-link mount, BUT in the
  // real flow the token + ``selectedDocumentId`` are set before the
  // async catalog list resolves — so the matching row's DOM node
  // doesn't exist yet on first run. We re-fire the effect every time
  // ``documents`` changes (so the row eventually renders and the ref
  // populates) and use a ref to remember which token we already
  // scrolled for, so a later catalog refresh doesn't yank the viewport
  // around a second time.
  useEffect(() => {
    if (scrollSelectedToken === undefined || scrollSelectedToken === 0) return;
    if (lastFiredScrollTokenRef.current === scrollSelectedToken) return;
    if (!selectedDocumentId) return;
    const node = selectedRowRef.current;
    if (!node) return; // try again on the next render once the row exists
    if (typeof node.scrollIntoView !== "function") return;
    node.scrollIntoView({ block: "center", behavior: "smooth" });
    lastFiredScrollTokenRef.current = scrollSelectedToken;
  }, [scrollSelectedToken, selectedDocumentId, documents]);
  const activeViewId = filter
    ? SAVED_VIEWS.find((view) => sameStatusSet(view.statuses, filter.status))?.id
    : null;

  // #292 — newest imports on top. ``created_at`` is the catalog's
  // wall-clock when the family row was first inserted; ties broken by
  // ``id`` for stable order across renders.
  const sortedDocuments = useMemo(
    () =>
      [...documents].sort((a, b) => {
        const cmp = b.created_at.localeCompare(a.created_at);
        return cmp !== 0 ? cmp : b.id.localeCompare(a.id);
      }),
    [documents],
  );

  const pendingReview = sortedDocuments.filter(
    (document) => latestVersion(document).status === "NEEDS_REVIEW",
  ).length;
  const failed = sortedDocuments.filter(
    (document) => latestVersion(document).status === "FAILED",
  ).length;
  const duplicates = sortedDocuments.filter(
    (document) => latestVersion(document).status === "DUPLICATE_DETECTED",
  ).length;
  const batchSelectionCount = selectedBatchIds?.size ?? 0;
  const canShowBatchBar =
    selectedBatchIds !== undefined &&
    onToggleBatchDocument !== undefined &&
    onRunBatchPipeline !== undefined;

  return (
    <section className="widget-panel" aria-labelledby="pipeline-widget-title">
      <div className="widget-header">
        <div>
          <p className="eyebrow">Orbital</p>
          <h1 id="pipeline-widget-title">KW Pipeline</h1>
        </div>
        {onPurgeAllRequest && sortedDocuments.length > 0 && (
          <button
            type="button"
            className="button button-danger purge-all-button"
            onClick={onPurgeAllRequest}
            aria-label="Purge all documents"
            data-testid="purge-all-button"
          >
            Purge all
          </button>
        )}
      </div>

      <p className="muted forge-hint" data-testid="forge-import-hint">
        Import documents from the Forge widget — Orbital is read-only for ingestion.
      </p>

      <div className="metric-grid" aria-label="Pipeline status summary">
        <Metric label="Review" value={pendingReview} tone="warning" />
        <Metric label="Failed" value={failed} tone="danger" />
        <Metric label="Duplicate" value={duplicates} tone="neutral" />
      </div>

      <div className="section-heading">
        <h2>Recent documents</h2>
      </div>

      {filter && onFilterChange ? (
        <CatalogFilterBar
          filter={filter}
          activeViewId={activeViewId ?? null}
          onFilterChange={onFilterChange}
        />
      ) : null}

      {canShowBatchBar ? (
        <div className="batch-pipeline-bar" aria-label="Batch semantic pipeline">
          <div>
            <strong>{batchSelectionCount}</strong>{" "}
            <span className="muted">selected for extraction + semantic generation</span>
          </div>
          <div className="batch-pipeline-actions">
            {batchSelectionCount > 0 && onClearBatchSelection ? (
              <button
                type="button"
                className="secondary-button"
                onClick={onClearBatchSelection}
                disabled={batchBusy}
              >
                Clear
              </button>
            ) : null}
            <button
              type="button"
              className="primary-button"
              onClick={onRunBatchPipeline}
              disabled={batchSelectionCount === 0 || batchBusy}
              aria-busy={batchBusy}
            >
              {batchBusy ? "Running…" : "Run selected pipeline"}
            </button>
          </div>
          {batchMessage ? (
            <div className="notice success" role="status">
              <span>{batchMessage}</span>
            </div>
          ) : null}
          {batchFailures && batchFailures.length > 0 ? (
            <div className="notice danger" role="alert">
              <strong>
                {batchFailures.length === 1
                  ? "1 document failed"
                  : `${batchFailures.length} documents failed`}
              </strong>
              <ul
                className="batch-failure-list"
                data-testid="batch-failure-list"
              >
                {batchFailures.map((failure) => (
                  <li key={failure.document_id}>
                    <strong>{failure.filename}</strong>
                    <span className="muted"> — {failure.reason}</span>
                  </li>
                ))}
              </ul>
              <p className="muted batch-failure-hint">
                Failed documents stay selected so you can retry in one click.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="document-list">
        {sortedDocuments.length === 0 ? (
          <p className="muted">
            {filter && (filter.status.length > 0 || filter.q.length > 0)
              ? "No documents match this filter."
              : "No documents yet."}
          </p>
        ) : (
          sortedDocuments.map((document) => {
            const version = latestVersion(document);
            const selected = document.id === selectedDocumentId;
            const checked = selectedBatchIds?.has(document.id) ?? false;
            const isDuplicate =
              version.status === "DUPLICATE_DETECTED" ||
              version.duplicate_of_version_id !== null;

            const totalVersions = document.versions.length;
            const batchState = batchProgress?.get(document.id);
            return (
              <div
                className={selected ? "document-row selected" : "document-row"}
                key={document.id}
                aria-current={selected ? "page" : undefined}
                ref={selected ? selectedRowRef : undefined}
              >
                {canShowBatchBar ? (
                  <label
                    className="document-row-check"
                    title="Select for batch pipeline"
                  >
                    <input
                      type="checkbox"
                      aria-label={`Select ${document.original_filename} for batch pipeline`}
                      checked={checked}
                      disabled={isDuplicate || batchBusy}
                      onChange={(event) =>
                        onToggleBatchDocument?.(document.id, event.target.checked)
                      }
                    />
                  </label>
                ) : null}
                <button
                  type="button"
                  className="document-row-main"
                  onClick={() => onSelectDocument(document.id)}
                >
                  <span>
                    <strong>
                      {document.original_filename}
                      {totalVersions > 1 ? (
                        <span
                          className="version-count muted"
                          data-testid="version-count"
                        >
                          {" "}
                          ({totalVersions} versions)
                        </span>
                      ) : null}
                    </strong>
                    <small>
                      <span
                        className="version-badge"
                        data-testid="latest-version-badge"
                        aria-label={`Latest version v${version.version_number}`}
                        title={`Latest version v${version.version_number}`}
                      >
                        v{version.version_number}
                      </span>
                      <ScopeChip scopes={documentScopes(document)} />
                    </small>
                  </span>
                  <span className="document-row-meta">
                    {isDuplicate ? (
                      <span className="duplicate-marker" aria-label="Duplicate of an earlier version">
                        Duplicate
                      </span>
                    ) : null}
                    {batchState ? (
                      <BatchStatusPill state={batchState} />
                    ) : null}
                    <StatusBadge status={version.status} />
                  </span>
                </button>
                {onPurgeRequest && (
                  <button
                    type="button"
                    className="document-row-purge"
                    aria-label={`Purge ${document.original_filename}`}
                    title="Purge document permanently"
                    onClick={() => onPurgeRequest(document)}
                    data-testid={`purge-${document.id}`}
                  >
                    Purge
                  </button>
                )}
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

interface MetricProps {
  label: string;
  value: number;
  tone: "neutral" | "warning" | "danger";
}

function Metric({ label, value, tone }: MetricProps) {
  return (
    <div className={`metric-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

interface CatalogFilterBarProps {
  filter: { status: string[]; q: string };
  activeViewId: string | null;
  onFilterChange: (next: { status: string[]; q: string }) => void;
}

/**
 * Search + saved-view chips for the catalog (#86).
 *
 * Saved views are mutually exclusive — clicking the active view chip
 * clears it back to the implicit "All" filter. Search and saved view
 * compose: the server applies both filters and ANDs them together.
 */
function CatalogFilterBar({ filter, activeViewId, onFilterChange }: CatalogFilterBarProps) {
  return (
    <div className="catalog-filter-bar" aria-label="Filter documents">
      <input
        type="search"
        className="catalog-filter-search"
        placeholder="Search filenames…"
        aria-label="Search by filename"
        value={filter.q}
        onChange={(event) => onFilterChange({ ...filter, q: event.target.value })}
      />
      <div className="catalog-filter-views">
        <div role="tablist" aria-label="Saved views">
          {SAVED_VIEWS.map((view) => {
            const isActive = activeViewId === view.id;
            return (
              <button
                key={view.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                className={isActive ? "catalog-filter-chip active" : "catalog-filter-chip"}
                onClick={() =>
                  onFilterChange({
                    ...filter,
                    status: isActive ? [] : [...view.statuses],
                  })
                }
              >
                {view.label}
              </button>
            );
          })}
        </div>
        {(activeViewId !== null || filter.q.length > 0) && (
          <button
            type="button"
            className="catalog-filter-chip catalog-filter-chip-clear"
            onClick={() => onFilterChange({ status: [], q: "" })}
            aria-label="Clear all filters"
          >
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

function sameStatusSet(a: ReadonlyArray<string>, b: ReadonlyArray<string>): boolean {
  if (a.length !== b.length) return false;
  const setB = new Set(b);
  return a.every((value) => setB.has(value));
}

const BATCH_PILL_LABEL: Record<BatchItemState["status"], string> = {
  queued: "Queued",
  extracting: "Extracting…",
  semantic: "Generating semantic…",
  done: "Done",
  failed: "Failed",
};

interface BatchStatusPillProps {
  state: BatchItemState;
}

function BatchStatusPill({ state }: BatchStatusPillProps) {
  const label = BATCH_PILL_LABEL[state.status];
  const title =
    state.status === "failed" && state.reason ? state.reason : undefined;
  return (
    <span
      className={`batch-row-pill batch-row-pill-${state.status}`}
      data-testid="batch-row-pill"
      data-status={state.status}
      title={title}
      aria-live="polite"
    >
      {label}
    </span>
  );
}
