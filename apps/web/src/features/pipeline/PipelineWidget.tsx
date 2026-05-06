import { useMemo } from "react";
import type { ApiDocument } from "../../api/types";
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
}

/**
 * Saved-view definitions for the segmented filter bar (#86 / #292).
 *
 * Stored (#292) groups every "raw bytes are present, processing in
 * progress or paused" status so an operator can pick out documents
 * still moving through the pipeline. Review/Validated/Failed remain
 * the canonical terminal lenses.
 */
const SAVED_VIEWS: ReadonlyArray<{ id: string; label: string; statuses: string[] }> = [
  {
    id: "stored",
    label: "Stored",
    statuses: [
      "STORED",
      "QUEUED_FOR_EXTRACTION",
      "EXTRACTING",
      "EXTRACTED",
      "ENRICHED",
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
}: PipelineWidgetProps) {
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

  return (
    <section className="widget-panel" aria-labelledby="pipeline-widget-title">
      <div className="widget-header">
        <div>
          <p className="eyebrow">Orbital</p>
          <h1 id="pipeline-widget-title">KW Pipeline</h1>
        </div>
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
            const isDuplicate =
              version.status === "DUPLICATE_DETECTED" ||
              version.duplicate_of_version_id !== null;

            const totalVersions = document.versions.length;
            return (
              <button
                className={selected ? "document-row selected" : "document-row"}
                type="button"
                key={document.id}
                aria-current={selected ? "page" : undefined}
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
                  <StatusBadge status={version.status} />
                </span>
              </button>
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
      <div className="catalog-filter-views" role="tablist" aria-label="Saved views">
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
