/**
 * Lineage modal — version history for a single ExplorerDocument.
 *
 * Triggered from:
 *   * The ``v{N}`` badge in the cluster rail / catalog (only when
 *     ``versionCount > 1``).
 *   * The "View history" link button in the DetailPanel "VERSIONS"
 *     section header.
 *
 * Rendering rules (derived purely from ``document.versions`` — no API
 * call):
 *   * Sort DESC by ``versionNumber`` (latest at top).
 *   * Each row: v{N} badge + filename + status chip + sha256[:8] +
 *     ingested_at.
 *   * The latest row gets a "Latest" chip + accent border-left.
 *   * SUPERSEDED rows get muted styling + a "→ replaced by v{X}"
 *     caption pointing to the next-higher VALIDATED sibling
 *     (per ADR-025 only ``VALIDATED → SUPERSEDED`` is legal, so the
 *     replacement is always the next ``VALIDATED`` version_number).
 *   * DUPLICATE_DETECTED rows get a "duplicate of v{X}" caption when
 *     ``duplicateOfVersionId`` resolves to a sibling in the family.
 *
 * Closing semantics:
 *   * ESC, click on backdrop, or click on the Close button.
 *   * Focus is trapped within the modal while it's open and restored
 *     to the trigger (or document.body) on close — same shape as the
 *     SettingsModal but with a tighter footprint.
 *
 * When the parallel ``GET /documents/{id}/lineage`` endpoint lands, a
 * follow-up swap point is the ``buildRows`` helper — feed it the
 * server projection instead of ``document.versions`` and the JSX is
 * unchanged.
 */

import React, { useCallback, useEffect, useMemo, useRef } from "react";

import { Icon } from "./icons";
import type { ExplorerDocument } from "../state/explorer-data";

interface LineageModalProps {
  document: ExplorerDocument;
  onClose: () => void;
}

type LineageVersion = NonNullable<ExplorerDocument["versions"]>[number];

interface LineageRow {
  version: LineageVersion;
  isLatest: boolean;
  isSuperseded: boolean;
  replacedByVersion: number | null;
  duplicateOfVersion: number | null;
  statusLabel: string;
  statusVariant: "good" | "warn" | "bad" | "info" | "muted";
}

const STATUS_LABEL: Record<string, string> = {
  UPLOADED: "Uploaded",
  HASHED: "Hashed",
  DUPLICATE_DETECTED: "Duplicate",
  STORED: "Stored",
  EXTRACTING: "Extracting",
  EXTRACTED: "Extracted",
  SEMANTIC_READY: "Semantic ready",
  NEEDS_REVIEW: "Needs review",
  VALIDATED: "Validated",
  REJECTED: "Rejected",
  FAILED: "Failed",
  SUPERSEDED: "Superseded",
  // ADR-027 §3: terminal status assigned by the purge_artifacts
  // admin route. Lineage modal still surfaces the row so audit
  // consumers can see the version was tombstoned (matches the
  // route layer's "show purged versions in mixed-status families"
  // posture per ADR-027 §3).
  PURGED: "Purged",
};

function statusVariant(status: string): LineageRow["statusVariant"] {
  switch (status) {
    case "VALIDATED":
      return "good";
    case "NEEDS_REVIEW":
    case "DUPLICATE_DETECTED":
      return "warn";
    case "REJECTED":
    case "FAILED":
      return "bad";
    case "SUPERSEDED":
    case "PURGED":
      return "muted";
    default:
      return "info";
  }
}

/**
 * Project the per-version metadata onto display rows. Sorted DESC by
 * version_number — latest first. Each row is enriched with derived
 * "replaced by v{X}" / "duplicate of v{X}" pointers based purely on
 * the supplied versions array (no API call).
 */
export function buildRows(versions: LineageVersion[]): LineageRow[] {
  if (versions.length === 0) return [];
  const sorted = [...versions].sort((a, b) => b.versionNumber - a.versionNumber);
  const latest = sorted[0]?.versionNumber;
  // Index by id for the duplicate_of_version_id pointer lookup.
  const byId = new Map<string, LineageVersion>();
  for (const v of versions) byId.set(v.id, v);
  return sorted.map((version) => {
    const isSuperseded = version.status === "SUPERSEDED";
    // Per ADR-025, only ``VALIDATED → SUPERSEDED`` is legal, so the
    // replacement is the next-higher VALIDATED version_number.
    let replacedByVersion: number | null = null;
    if (isSuperseded) {
      const candidates = versions
        .filter((v) => v.versionNumber > version.versionNumber && v.status === "VALIDATED")
        .sort((a, b) => a.versionNumber - b.versionNumber);
      replacedByVersion = candidates[0]?.versionNumber ?? null;
    }
    let duplicateOfVersion: number | null = null;
    if (version.status === "DUPLICATE_DETECTED" && version.duplicateOfVersionId) {
      const sibling = byId.get(version.duplicateOfVersionId);
      duplicateOfVersion = sibling?.versionNumber ?? null;
    }
    return {
      version,
      isLatest: version.versionNumber === latest,
      isSuperseded,
      replacedByVersion,
      duplicateOfVersion,
      statusLabel: STATUS_LABEL[version.status] ?? version.status,
      statusVariant: statusVariant(version.status),
    };
  });
}

function shortSha(sha?: string): string | null {
  if (!sha) return null;
  return sha.slice(0, 8);
}

function formatDate(iso: string): string {
  // The version createdAt comes from the API as an ISO 8601 string.
  // Show YYYY-MM-DD — relative dates would need a timestamp baseline
  // we don't currently carry through (per the spec, skip relative
  // formatting rather than fabricate it). Fall back to the raw string
  // when parsing fails so we never blow up the modal on malformed
  // input.
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return iso.slice(0, 10);
}

export const LineageModal: React.FC<LineageModalProps> = ({ document: doc, onClose }) => {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const previousActiveRef = useRef<Element | null>(null);

  const versions = doc.versions ?? [];
  const rows = useMemo(() => buildRows(versions), [versions]);
  const onlyOne = rows.length <= 1;

  // ESC + focus restore + initial focus on the close button. Mirrors
  // the SettingsModal pattern but with a small focus trap so Tab
  // doesn't escape the dialog.
  useEffect(() => {
    previousActiveRef.current = window.document.activeElement;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === "Tab") {
        const root = dialogRef.current;
        if (!root) return;
        const focusables = root.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = window.document.activeElement as HTMLElement | null;
        if (e.shiftKey && active === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      const prev = previousActiveRef.current;
      if (prev && prev instanceof HTMLElement) prev.focus();
    };
  }, [onClose]);

  const onBackdropClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  return (
    <div
      className="kx-lineage-modal-backdrop"
      data-testid="kx-lineage-backdrop"
      onClick={onBackdropClick}
    >
      <div
        ref={dialogRef}
        className="kx-lineage-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Version history"
        data-testid="kx-lineage-modal"
      >
        <header className="kx-lineage-head">
          <div className="kx-lineage-head-t">
            <div className="kx-kind">VERSION HISTORY</div>
            <div
              className="kx-lineage-filename"
              title={doc.title}
              data-testid="kx-lineage-doc-title"
            >
              {doc.title}
            </div>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="kx-icon-btn"
            onClick={onClose}
            aria-label="Close version history"
            data-testid="kx-lineage-close"
          >
            <Icon name="x" size={12} />
          </button>
        </header>

        <div className="kx-lineage-body">
          {onlyOne ? (
            <div className="kx-lineage-empty" data-testid="kx-lineage-empty">
              Only one version uploaded — no history yet.
            </div>
          ) : (
            <ol className="kx-lineage-list" data-testid="kx-lineage-list">
              {rows.map((row) => {
                const sha = shortSha(row.version.sha256);
                const className =
                  "kx-lineage-row" +
                  (row.isLatest ? " kx-lineage-row--latest" : "") +
                  (row.isSuperseded ? " kx-lineage-row--superseded" : "");
                return (
                  <li
                    key={row.version.id}
                    className={className}
                    data-version-number={row.version.versionNumber}
                    data-status={row.version.status}
                  >
                    <div className="kx-lineage-row-head">
                      <span className="kx-ver-badge kx-mono">v{row.version.versionNumber}</span>
                      <span
                        className="kx-lineage-fname"
                        title={row.version.filename}
                      >
                        {row.version.filename}
                      </span>
                      <span
                        className={
                          "kx-cat-badge kx-lineage-status kx-stat-" + row.statusVariant
                        }
                      >
                        {row.statusLabel}
                      </span>
                      {row.isLatest && (
                        <span
                          className="kx-cat-badge kx-stat-good kx-lineage-latest"
                          data-testid="kx-lineage-latest-chip"
                        >
                          Latest
                        </span>
                      )}
                    </div>
                    <div className="kx-lineage-row-meta">
                      {sha && (
                        <span
                          className="kx-mono kx-mute kx-lineage-sha"
                          title={row.version.sha256}
                        >
                          sha {sha}
                        </span>
                      )}
                      {row.version.createdAt && (
                        <span className="kx-mono kx-mute kx-lineage-date">
                          {formatDate(row.version.createdAt)}
                        </span>
                      )}
                      {row.replacedByVersion !== null && (
                        <span
                          className="kx-lineage-arrow"
                          data-testid={`kx-lineage-replaced-by-${row.version.versionNumber}`}
                        >
                          → replaced by v{row.replacedByVersion}
                        </span>
                      )}
                      {row.duplicateOfVersion !== null && (
                        <span
                          className="kx-lineage-arrow"
                          data-testid={`kx-lineage-duplicate-of-${row.version.versionNumber}`}
                        >
                          duplicate of v{row.duplicateOfVersion}
                        </span>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>

        <footer className="kx-lineage-foot">
          <span className="kx-mute">Older versions are read-only.</span>
          <button
            type="button"
            className="kx-btn"
            onClick={onClose}
            data-testid="kx-lineage-close-foot"
          >
            Close
          </button>
        </footer>
      </div>
    </div>
  );
};

export default LineageModal;
