/**
 * Admin Archive — Bulk multi-select Purge modal (D.9 follow-up).
 *
 * Wraps ``POST /admin/archive/purge_batch`` (ADR-027 §4 / #273). Same
 * dry-run-then-real flow as the per-doc purge modal, but the preview
 * is per-document (each row in the response carries either a
 * ``PurgeArtifactsResponse`` on success or an ``error_code``/
 * ``error_message`` pair on failure). The CTA is disabled when:
 *
 * - the selection exceeds the 100-doc cap (backend would 422), or
 * - the preview returned 0 purgeable docs (real call would be a no-op).
 *
 * UX: the per-doc table is collapsed inside a ``<details>`` so the
 * default modal height is bounded; the rolled-up summary (purgeable /
 * failed / bytes) is always visible above it.
 */

import { useCallback, useMemo, useState } from "react";

import { ApiError, purgeBatch } from "../../api/client";
import type { ApiPurgeBatchResponse } from "../../api/types";
import { ModalShell } from "./ModalShell";

/** Backend cap — see PurgeBatchRequest in ADR-027 §4 / #273.
 *  Mirrored client-side so the CTA disables (with a tooltip) rather
 *  than letting the user trip the 422. */
const PURGE_BATCH_MAX = 100;

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return fallback;
}

interface BulkPurgeModalProps {
  documentIds: string[];
  onClose: () => void;
  onCompleted: (toastMessage: string) => void | Promise<void>;
}

export function BulkPurgeModal({
  documentIds,
  onClose,
  onCompleted,
}: BulkPurgeModalProps) {
  const [preview, setPreview] = useState<ApiPurgeBatchResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  const overCap = documentIds.length > PURGE_BATCH_MAX;

  // Compact id list (`doc-abc-123, doc-def-456, …`). Truncated to the
  // first 10 ids so a 100-doc selection doesn't flood the modal —
  // operators have the count + the per-doc table once preview returns.
  const compactIds = useMemo(() => {
    const head = documentIds.slice(0, 10).join(", ");
    if (documentIds.length <= 10) return head;
    return `${head}, … (+${documentIds.length - 10} more)`;
  }, [documentIds]);

  const handlePreview = useCallback(() => {
    setPreviewing(true);
    setPreviewError(null);
    setConfirmError(null);
    purgeBatch(documentIds, { dryRun: true })
      .then((response) => setPreview(response))
      .catch((err: unknown) => {
        setPreviewError(errorMessage(err, "Preview failed."));
      })
      .finally(() => setPreviewing(false));
  }, [documentIds]);

  // Bytes rollup across the successful per-doc previews. Skip failed
  // rows (their ``purge_response`` is null) — the operator will see
  // those as the "failed" count in the post-confirm toast anyway.
  const previewSummary = useMemo(() => {
    if (preview === null) return null;
    let purgeable = 0;
    let failed = 0;
    let bytesTotal = 0;
    for (const result of preview.results) {
      if (result.success && result.purge_response) {
        purgeable += 1;
        for (const v of result.purge_response.versions_purged) {
          bytesTotal += v.bytes_estimate ?? 0;
        }
      } else {
        failed += 1;
      }
    }
    return { purgeable, failed, bytesTotal };
  }, [preview]);

  const handleConfirm = useCallback(() => {
    setConfirming(true);
    setConfirmError(null);
    purgeBatch(documentIds, { dryRun: false })
      .then(async (response) => {
        let succeeded = 0;
        let failed = 0;
        for (const r of response.results) {
          if (r.success) succeeded += 1;
          else failed += 1;
        }
        const toastMessage =
          failed === 0
            ? `Purged ${succeeded} doc${succeeded === 1 ? "" : "s"}.`
            : `Purged ${succeeded} doc${succeeded === 1 ? "" : "s"}, ${failed} failed.`;
        await onCompleted(toastMessage);
        onClose();
      })
      .catch((err: unknown) => {
        setConfirmError(errorMessage(err, "Bulk purge failed."));
      })
      .finally(() => setConfirming(false));
  }, [documentIds, onCompleted, onClose]);

  const ctaDisabled =
    overCap ||
    confirming ||
    preview === null ||
    (previewSummary !== null && previewSummary.purgeable === 0);

  const ctaTooltip = overCap
    ? `Max ${PURGE_BATCH_MAX} per batch — split into multiple operations`
    : undefined;

  return (
    <ModalShell title="Purge selected documents?" onClose={onClose}>
      <div className="notice danger" role="alert">
        <strong>Irreversible.</strong> This will permanently delete the
        bytes for the selected documents. Catalog rows are preserved as
        audit traces.
      </div>
      <p>
        <strong>{documentIds.length}</strong>{" "}
        document{documentIds.length === 1 ? "" : "s"} selected
        {overCap ? (
          <>
            {" "}
            — <span className="danger-text">over the {PURGE_BATCH_MAX}-doc
            cap</span>
          </>
        ) : null}
        .
      </p>
      <p
        className="muted compact-id-list"
        data-testid="bulk-purge-id-list"
      >
        <code>{compactIds}</code>
      </p>
      {previewError !== null ? (
        <div className="notice danger" role="alert">
          <strong>Preview failed</strong>
          <span>{previewError}</span>
        </div>
      ) : null}
      {preview !== null && previewSummary !== null ? (
        <>
          <dl className="purge-preview">
            <div>
              <dt>Purgeable</dt>
              <dd data-testid="bulk-purge-purgeable">
                {previewSummary.purgeable}
              </dd>
            </div>
            <div>
              <dt>Failed in preview</dt>
              <dd data-testid="bulk-purge-failed">{previewSummary.failed}</dd>
            </div>
            <div>
              <dt>Estimated bytes freed</dt>
              <dd data-testid="bulk-purge-bytes">
                {previewSummary.bytesTotal}
              </dd>
            </div>
          </dl>
          <details className="purge-tombstone-list">
            <summary>Per-document outcome ({preview.results.length})</summary>
            <ul>
              {preview.results.map((r) => (
                <li key={r.document_id}>
                  <code>{r.document_id}</code>{" "}
                  {r.success ? (
                    <span className="muted">
                      {r.purge_response?.versions_purged.length ?? 0} version
                      {(r.purge_response?.versions_purged.length ?? 0) === 1
                        ? ""
                        : "s"}{" "}
                      ·{" "}
                      {r.purge_response?.versions_purged.reduce(
                        (sum, v) => sum + (v.bytes_estimate ?? 0),
                        0,
                      ) ?? 0}{" "}
                      bytes
                    </span>
                  ) : (
                    <span className="danger-text">
                      {r.error_code ?? "FAILED"}: {r.error_message ?? ""}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </details>
        </>
      ) : null}
      {confirmError !== null ? (
        <div className="notice danger" role="alert">
          <strong>Bulk purge failed</strong>
          <span>{confirmError}</span>
        </div>
      ) : null}
      <div className="action-row">
        <button
          type="button"
          className="secondary-button"
          onClick={onClose}
          disabled={previewing || confirming}
        >
          Cancel
        </button>
        <button
          type="button"
          className="secondary-button"
          onClick={handlePreview}
          disabled={overCap || previewing || confirming}
          aria-busy={previewing}
          title={ctaTooltip}
        >
          {previewing ? "Previewing…" : "Preview impact"}
        </button>
        <button
          type="button"
          className="primary-button danger"
          onClick={handleConfirm}
          disabled={ctaDisabled}
          aria-busy={confirming}
          title={ctaTooltip}
        >
          {confirming
            ? "Purging…"
            : `Permanently delete ${documentIds.length} document${documentIds.length === 1 ? "" : "s"}`}
        </button>
      </div>
    </ModalShell>
  );
}
