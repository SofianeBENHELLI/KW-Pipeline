import { useEffect, useRef, useState } from "react";

import { ApiError, orbitalPurgeAll } from "../../api/client";
import {
  type ApiOrbitalPurgeAllResponse,
  ORBITAL_PURGE_ALL_PHRASE,
} from "../../api/types";

interface PurgeAllDialogProps {
  /** ``true`` opens the modal; ``false`` keeps it hidden. */
  open: boolean;
  /** Number of active documents currently in the catalog (header copy). */
  documentCount: number;
  onCancel: () => void;
  onPurged: (response: ApiOrbitalPurgeAllResponse) => void;
}

/**
 * Bulk hard-delete confirmation modal (#292 §5 — operator override).
 *
 * The user picked option 3 in #292 (Orbital is the sanctioned
 * hard-delete surface) and asked for a single button that nukes the
 * whole knowledge space. Two independent gates protect this path:
 *
 * - The operator types :data:`ORBITAL_PURGE_ALL_PHRASE` verbatim;
 *   the confirm button stays disabled otherwise.
 * - The backend re-checks the same phrase on the wire (422 on
 *   mismatch) and demands ``?confirm=true`` separately.
 */
export function PurgeAllDialog({
  open,
  documentCount,
  onCancel,
  onPurged,
}: PurgeAllDialogProps) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setTyped("");
    setError(null);
    inputRef.current?.focus();
  }, [open]);

  if (!open) return null;

  const phraseMatches = typed === ORBITAL_PURGE_ALL_PHRASE;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const result = await orbitalPurgeAll(typed);
      onPurged(result);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : "Bulk purge failed.";
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="purge-all-dialog-title"
      data-testid="purge-all-dialog"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div className="modal-card purge-dialog">
        <h2 id="purge-all-dialog-title">Purge the entire knowledge space?</h2>
        <p className="muted">
          This will <strong>archive</strong> every active document,{" "}
          <strong>delete</strong> their stored bytes / extractions / semantic
          JSON / Markdown, and <strong>drop</strong> their knowledge-graph
          subgraphs. The cascade is irreversible from this surface.
        </p>
        <ul className="purge-dialog-summary">
          <li>
            Documents impacted: <strong>{documentCount}</strong>
          </li>
          <li>
            Audit events: <code>orbital.knowledge_space.purge</code> +{" "}
            <code>orbital.document.purge</code> per row
          </li>
        </ul>
        <p>
          Type <code>{ORBITAL_PURGE_ALL_PHRASE}</code> to confirm.
        </p>
        <input
          ref={inputRef}
          type="text"
          className="purge-dialog-input"
          aria-label="Type confirmation phrase"
          value={typed}
          disabled={busy}
          onChange={(event) => setTyped(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && phraseMatches && !busy) submit();
            if (event.key === "Escape" && !busy) onCancel();
          }}
          data-testid="purge-all-dialog-input"
        />
        {error && (
          <div className="notice danger" role="alert">
            <strong>Couldn&apos;t purge</strong>
            <span>{error}</span>
          </div>
        )}
        <div className="purge-dialog-actions">
          <button
            type="button"
            className="button"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            className="button button-danger"
            onClick={submit}
            disabled={!phraseMatches || busy}
            data-testid="purge-all-dialog-confirm"
          >
            {busy ? "Purging…" : `Purge ${documentCount} documents permanently`}
          </button>
        </div>
      </div>
    </div>
  );
}
