/**
 * Admin Archive — Relink scope modal (D.9 follow-up).
 *
 * Modal for ``POST /admin/archive/relink_scope`` (ADR-027 §1.2 / #269).
 * Pre-fills from the archived row's ``last_active_scope_*`` so the most
 * common workflow (reverse the most-recent removal) is one click +
 * Preview + Confirm. Same dry-run-then-real gate as the per-doc purge
 * modal — the destructive-CTA only renders after the dry-run resolves.
 *
 * Editing the form invalidates the cached preview: otherwise an
 * operator could preview ``personal:bob``, edit to ``personal:alice``,
 * and click the CTA which would submit alice without ever previewing
 * it. The state owner clears ``preview`` on every form change.
 */

import { useCallback, useState } from "react";

import { ApiError, relinkScope } from "../../api/client";
import type {
  ApiArchivedDocumentItem,
  ApiRelinkScopeResponse,
  ApiScopeKind,
} from "../../api/types";
import { ModalShell } from "./ModalShell";

/** Three-flavor scope kind list mirrors ``ApiScopeKind`` (Pydantic
 *  ``Literal``). Hard-coded because openapi-typescript inlines literal
 *  unions on the request shape rather than emitting a value enum. */
const SCOPE_KIND_OPTIONS: readonly ApiScopeKind[] = [
  "personal",
  "swym_community",
  "project",
];

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return fallback;
}

interface RelinkModalProps {
  item: ApiArchivedDocumentItem;
  onClose: () => void;
  onCompleted: () => void | Promise<void>;
}

export function RelinkModal({
  item,
  onClose,
  onCompleted,
}: RelinkModalProps) {
  // Pre-fill from the archived row's last_active_scope_*. Either may be
  // null on a doc that was never in any scope before archiving — the
  // form just opens with empty fields and the operator types them.
  // Default to "personal" when the row has no scope_kind hint, since
  // it's the most common flavor and the picker still lets them switch.
  const initialKind: ApiScopeKind =
    item.last_active_scope_kind &&
    SCOPE_KIND_OPTIONS.includes(item.last_active_scope_kind as ApiScopeKind)
      ? (item.last_active_scope_kind as ApiScopeKind)
      : "personal";
  const initialRef = item.last_active_scope_ref ?? "";

  const [scopeKind, setScopeKind] = useState<ApiScopeKind>(initialKind);
  const [scopeRef, setScopeRef] = useState<string>(initialRef);
  const [preview, setPreview] = useState<ApiRelinkScopeResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  const handleKindChange = useCallback((next: ApiScopeKind) => {
    setScopeKind(next);
    setPreview(null);
  }, []);
  const handleRefChange = useCallback((next: string) => {
    setScopeRef(next);
    setPreview(null);
  }, []);

  const canSubmit = scopeRef.trim().length > 0;

  const handlePreview = useCallback(() => {
    setPreviewing(true);
    setPreviewError(null);
    setConfirmError(null);
    relinkScope(
      {
        document_id: item.document_id,
        scope_kind: scopeKind,
        scope_ref: scopeRef.trim(),
      },
      { dryRun: true },
    )
      .then((response) => setPreview(response))
      .catch((err: unknown) => {
        setPreviewError(errorMessage(err, "Preview failed."));
      })
      .finally(() => setPreviewing(false));
  }, [item.document_id, scopeKind, scopeRef]);

  const handleConfirm = useCallback(() => {
    setConfirming(true);
    setConfirmError(null);
    relinkScope(
      {
        document_id: item.document_id,
        scope_kind: scopeKind,
        scope_ref: scopeRef.trim(),
      },
      { dryRun: false },
    )
      .then(async () => {
        await onCompleted();
        onClose();
      })
      .catch((err: unknown) => {
        setConfirmError(errorMessage(err, "Relink failed."));
      })
      .finally(() => setConfirming(false));
  }, [item.document_id, scopeKind, scopeRef, onCompleted, onClose]);

  return (
    <ModalShell title="Relink scope" onClose={onClose}>
      <p>
        Reactivate a soft-removed scope link for{" "}
        <strong>{item.original_filename}</strong>.
      </p>
      <div className="form-grid">
        <label>
          <span className="muted">Scope kind</span>
          <select
            value={scopeKind}
            onChange={(e) => handleKindChange(e.target.value as ApiScopeKind)}
            disabled={previewing || confirming}
            data-testid="relink-scope-kind"
          >
            {SCOPE_KIND_OPTIONS.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="muted">Scope ref</span>
          <input
            type="text"
            value={scopeRef}
            onChange={(e) => handleRefChange(e.target.value)}
            disabled={previewing || confirming}
            placeholder="e.g. user-id, community-id, project-id"
            data-testid="relink-scope-ref"
          />
        </label>
      </div>
      {previewError !== null ? (
        <div className="notice danger" role="alert">
          <strong>Preview failed</strong>
          <span>{previewError}</span>
        </div>
      ) : null}
      {preview !== null ? (
        <dl className="purge-preview" data-testid="relink-preview">
          <div>
            <dt>Scope link</dt>
            <dd>
              <code>
                {preview.scope_kind}:{preview.scope_ref}
              </code>
            </dd>
          </div>
          <div>
            <dt>Removed at (before)</dt>
            <dd data-testid="relink-removed-at">
              {preview.removed_at_before ?? "active (no-op)"}
            </dd>
          </div>
        </dl>
      ) : null}
      {confirmError !== null ? (
        <div className="notice danger" role="alert">
          <strong>Relink failed</strong>
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
          disabled={!canSubmit || previewing || confirming}
          aria-busy={previewing}
        >
          {previewing ? "Previewing…" : "Preview"}
        </button>
        {/* CTA only renders after the dry-run resolves — same load-bearing
            gate as the per-doc purge modal (ADR-027 dry-run-then-real). */}
        {preview !== null ? (
          <button
            type="button"
            className="primary-button"
            onClick={handleConfirm}
            disabled={!canSubmit || confirming}
            aria-busy={confirming}
          >
            {confirming ? "Reactivating…" : "Reactivate scope link"}
          </button>
        ) : null}
      </div>
    </ModalShell>
  );
}
