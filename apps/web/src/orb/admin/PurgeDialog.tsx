/**
 * PurgeDialog — typed-confirm gate for permanent document deletion.
 *
 * Per design §8.1: the user must type the *exact filename* before the
 * Purge button enables. Server re-validates server-side and returns
 * 412 if the filename has changed — we surface the message verbatim.
 *
 * Backend wiring stays decoupled: the parent passes `onConfirm`,
 * which typically calls `orbitalPurgeDocument()` from the existing
 * api/client.ts.
 */

import { useState } from "react";
import type { ReactElement } from "react";

import { Btn, OrbI } from "../index";
import "./admin.css";

export interface PurgeDialogProps {
  open: boolean;
  documentId: string;
  filename: string;
  /** Pretty-printed scope summary shown in the body. */
  scopeBlurb?: string;
  /** Async confirm. The dialog stays open while in-flight. */
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}

export function PurgeDialog({
  open,
  documentId,
  filename,
  scopeBlurb,
  onConfirm,
  onCancel,
}: PurgeDialogProps): ReactElement | null {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;
  const matched = typed === filename;

  const submit = async () => {
    if (!matched) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="kf-modal-backdrop" role="presentation">
      <div
        className="kf-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="kf-purge-title"
        data-testid="kf-purge-dialog"
      >
        <header className="kf-modal__head">
          <h2 id="kf-purge-title" className="kf-modal__title">
            Purge document
          </h2>
          <button
            type="button"
            className="kf-modal__close"
            aria-label="Cancel"
            onClick={onCancel}
            disabled={busy}
          >
            {OrbI.x}
          </button>
        </header>
        <div className="kf-modal__body">
          <p>
            This will <strong>permanently delete</strong> the file blob,
            all version metadata, all extracted spans, all semantic
            versions, and all graph projections for{" "}
            <code className="orb-mono">{documentId}</code>.
          </p>
          {scopeBlurb && <p className="kf-modal__scope">{scopeBlurb}</p>}
          <label className="kf-modal__label">
            Type the exact filename to enable Purge:
            <input
              type="text"
              className="kf-modal__input"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={filename}
              disabled={busy}
              aria-label="Filename confirmation"
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          {error && (
            <p className="kf-modal__error" role="alert">
              {error}
            </p>
          )}
        </div>
        <footer className="kf-modal__foot">
          <Btn kind="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Btn>
          <Btn
            kind="danger"
            disabled={!matched || busy}
            onClick={submit}
            data-testid="kf-purge-confirm"
            icon={OrbI.trash}
          >
            {busy ? "Purging…" : "Purge"}
          </Btn>
        </footer>
      </div>
    </div>
  );
}
