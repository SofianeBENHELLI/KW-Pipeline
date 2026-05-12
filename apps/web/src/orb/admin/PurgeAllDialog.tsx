/**
 * PurgeAllDialog — nuclear corpus-wide purge gate.
 *
 * Per design §8.2: the gate is a *rotating secret phrase* the operator
 * must type verbatim, plus a 5-second cool-off countdown on the
 * primary button after the phrase matches. Backend re-validates with
 * a constant-time compare.
 *
 * The expected phrase comes from `ORBITAL_PURGE_ALL_PHRASE` in
 * `apps/web/src/api/types.ts` (mirrored from the backend module so the
 * UI stays in sync without an extra fetch).
 */

import { useEffect, useState } from "react";
import type { ReactElement } from "react";

import { Btn, OrbI } from "../index";
import { ORBITAL_PURGE_ALL_PHRASE } from "../../api/types";
import "./admin.css";

const COOL_OFF_SECONDS = 5;

export interface PurgeAllDialogProps {
  open: boolean;
  /** The secret phrase the user must type verbatim. */
  phrase?: string;
  /** Total docs that will be permanently destroyed. Surfaced in the body. */
  documentCount?: number;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}

export function PurgeAllDialog({
  open,
  phrase = ORBITAL_PURGE_ALL_PHRASE,
  documentCount,
  onConfirm,
  onCancel,
}: PurgeAllDialogProps): ReactElement | null {
  const [typed, setTyped] = useState("");
  const [coolOff, setCoolOff] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const matched = typed === phrase;

  // Start the cool-off when the user finishes typing. The cool-off
  // only counts down while the dialog is open AND the typed value
  // matches — switching focus or editing the field resets it.
  useEffect(() => {
    if (!matched) {
      setCoolOff(0);
      return;
    }
    setCoolOff(COOL_OFF_SECONDS);
    const id = setInterval(() => {
      setCoolOff((s) => Math.max(0, s - 1));
    }, 1000);
    return () => clearInterval(id);
  }, [matched]);

  if (!open) return null;

  const ready = matched && coolOff === 0 && !busy;

  const submit = async () => {
    if (!ready) return;
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
        className="kf-modal kf-modal--danger"
        role="dialog"
        aria-modal="true"
        aria-labelledby="kf-purge-all-title"
        data-testid="kf-purge-all-dialog"
      >
        <header className="kf-modal__head kf-modal__head--danger">
          <h2 id="kf-purge-all-title" className="kf-modal__title">
            Purge entire corpus
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
            <strong>This is irreversible.</strong> Every document,
            version, extraction, semantic projection, graph node, and
            graph edge in the corpus will be deleted.
          </p>
          {typeof documentCount === "number" && (
            <p className="orb-mono kf-modal__count">
              {documentCount.toLocaleString()} documents will be destroyed.
            </p>
          )}
          <label className="kf-modal__label">
            Type the rotating phrase to enable Purge:
            <input
              type="text"
              className="kf-modal__input"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={phrase}
              disabled={busy}
              autoComplete="off"
              spellCheck={false}
              aria-label="Purge phrase"
            />
          </label>
          {matched && coolOff > 0 && (
            <p className="orb-mono kf-modal__cooloff" data-testid="kf-purge-cooloff">
              cool-off · {coolOff}s
            </p>
          )}
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
            disabled={!ready}
            onClick={submit}
            data-testid="kf-purge-all-confirm"
            icon={OrbI.trash}
          >
            {busy ? "Purging…" : matched && coolOff > 0 ? `Purge in ${coolOff}s` : "Purge corpus"}
          </Btn>
        </footer>
      </div>
    </div>
  );
}
