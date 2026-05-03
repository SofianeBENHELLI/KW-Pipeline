import { useRef, useState } from "react";
import type { ApiDocument, ApiUploadResponse } from "../../api/types";
import { ApiError, uploadDocument } from "../../api/client";
import { latestVersion } from "../../domain/document";
import { StatusBadge } from "../../ui/StatusBadge";

const ACCEPTED_MIME_TYPES =
  "text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

interface PipelineWidgetProps {
  documents: ApiDocument[];
  selectedDocumentId: string;
  onSelectDocument: (id: string) => void;
  /**
   * Called after a successful upload with the new document_id so the
   * parent can refresh the catalog and re-select.
   */
  onUploaded?: (documentId: string) => void | Promise<void>;
  /** Active catalog filter (#86); ``undefined`` keeps the legacy "no filter" UX. */
  filter?: { status: string[]; q: string };
  /** Filter setter — required when ``filter`` is provided. */
  onFilterChange?: (next: { status: string[]; q: string }) => void;
}

/**
 * Saved-view definitions for the segmented filter bar (#86). Each
 * entry maps a UX label to the set of statuses the server should
 * filter on. ``All`` is the implicit default and isn't rendered as
 * an explicit chip — clearing the active view restores it.
 */
const SAVED_VIEWS: ReadonlyArray<{ id: string; label: string; statuses: string[] }> = [
  { id: "review", label: "Review", statuses: ["NEEDS_REVIEW", "DUPLICATE_DETECTED"] },
  { id: "validated", label: "Validated", statuses: ["VALIDATED"] },
  { id: "failed", label: "Failed", statuses: ["FAILED", "REJECTED"] },
];

type UploadState =
  | { kind: "idle" }
  | { kind: "uploading"; filename: string }
  | { kind: "success"; version: ApiUploadResponse }
  | { kind: "error"; message: string; remediation: string | null };

export function PipelineWidget({
  documents,
  selectedDocumentId,
  onSelectDocument,
  onUploaded,
  filter,
  onFilterChange,
}: PipelineWidgetProps) {
  // Active saved-view id: matches against the configured statuses set
  // exactly so a custom-status filter (set programmatically) doesn't
  // accidentally light up an unrelated chip.
  const activeViewId = filter
    ? SAVED_VIEWS.find((view) => sameStatusSet(view.statuses, filter.status))?.id
    : null;
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [upload, setUpload] = useState<UploadState>({ kind: "idle" });
  const [copiedHashFor, setCopiedHashFor] = useState<string | null>(null);

  const pendingReview = documents.filter(
    (document) => latestVersion(document).status === "NEEDS_REVIEW",
  ).length;
  const failed = documents.filter(
    (document) => latestVersion(document).status === "FAILED",
  ).length;
  const duplicates = documents.filter(
    (document) => latestVersion(document).status === "DUPLICATE_DETECTED",
  ).length;

  const isUploading = upload.kind === "uploading";

  function openFilePicker() {
    if (isUploading) return;
    inputRef.current?.click();
  }

  async function handleFile(file: File | null | undefined) {
    if (!file) return;

    if (file.size === 0) {
      setUpload({
        kind: "error",
        message: "The selected file is empty.",
        remediation: "Pick a non-empty file and try again.",
      });
      return;
    }

    setUpload({ kind: "uploading", filename: file.name });
    try {
      const version = await uploadDocument(file);
      setUpload({ kind: "success", version });
      // Reset the file input so the same file can be re-selected later.
      if (inputRef.current) inputRef.current.value = "";
      if (onUploaded) await onUploaded(version.document_id);
    } catch (err: unknown) {
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : "Upload failed.";
      const remediation = err instanceof ApiError ? err.remediation : null;
      setUpload({ kind: "error", message, remediation });
    }
  }

  async function copyHash(value: string) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      }
      setCopiedHashFor(value);
      window.setTimeout(() => {
        setCopiedHashFor((current) => (current === value ? null : current));
      }, 1500);
    } catch {
      // Clipboard write can reject in non-secure contexts; just no-op.
    }
  }

  return (
    <section className="widget-panel" aria-labelledby="pipeline-widget-title">
      <div className="widget-header">
        <div>
          <p className="eyebrow">Orbital</p>
          <h1 id="pipeline-widget-title">KW Pipeline</h1>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label="Upload document"
          aria-busy={isUploading}
          disabled={isUploading}
          onClick={openFilePicker}
        >
          {isUploading ? <span className="spinner" aria-hidden="true" /> : "+"}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_MIME_TYPES}
          className="visually-hidden"
          aria-hidden="true"
          tabIndex={-1}
          onChange={(event) => {
            const file = event.target.files?.[0];
            void handleFile(file);
          }}
        />
      </div>

      {upload.kind === "uploading" ? (
        <p className="muted upload-status" role="status" aria-live="polite">
          Uploading {upload.filename}…
        </p>
      ) : null}

      {upload.kind === "error" ? (
        <div className="notice danger" role="alert">
          <strong>Upload failed</strong>
          <span>{upload.message}</span>
          {upload.remediation ? (
            <span className="muted">{upload.remediation}</span>
          ) : null}
        </div>
      ) : null}

      {upload.kind === "success" ? (
        <UploadSuccessSummary
          version={upload.version}
          copiedHashFor={copiedHashFor}
          onCopyHash={copyHash}
        />
      ) : null}

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
        {documents.length === 0 ? (
          <p className="muted">
            {filter && (filter.status.length > 0 || filter.q.length > 0)
              ? "No documents match this filter."
              : "No documents yet."}
          </p>
        ) : (
          documents.map((document) => {
            const version = latestVersion(document);
            const selected = document.id === selectedDocumentId;
            const isDuplicate =
              version.status === "DUPLICATE_DETECTED" ||
              version.duplicate_of_version_id !== null;

            return (
              <button
                className={selected ? "document-row selected" : "document-row"}
                type="button"
                key={document.id}
                aria-current={selected ? "page" : undefined}
                onClick={() => onSelectDocument(document.id)}
              >
                <span>
                  <strong>{document.original_filename}</strong>
                  <small>v{version.version_number}</small>
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

interface UploadSuccessSummaryProps {
  version: ApiUploadResponse;
  copiedHashFor: string | null;
  onCopyHash: (value: string) => void;
}

function UploadSuccessSummary({
  version,
  copiedHashFor,
  onCopyHash,
}: UploadSuccessSummaryProps) {
  const truncated = version.sha256.slice(0, 12);
  const isDuplicate =
    version.status === "DUPLICATE_DETECTED" ||
    version.duplicate_of_version_id !== null;
  const copied = copiedHashFor === version.sha256;

  return (
    <div className="upload-success" role="status" aria-live="polite">
      <div className="upload-success-row">
        <strong>{version.filename}</strong>
        <StatusBadge status={version.status} />
      </div>
      <div className="upload-success-row">
        <span className="muted">SHA-256</span>
        <code>{truncated}…</code>
        <button
          type="button"
          className="text-button"
          onClick={() => onCopyHash(version.sha256)}
          aria-label={`Copy SHA-256 hash ${version.sha256}`}
        >
          {copied ? "Copied" : "Copy hash"}
        </button>
      </div>
      {isDuplicate ? (
        <p className="duplicate-marker prominent" role="status">
          Duplicate detected — this file matches an earlier version.
        </p>
      ) : null}
    </div>
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
