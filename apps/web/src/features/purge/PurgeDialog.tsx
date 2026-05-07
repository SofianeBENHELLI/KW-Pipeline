import { useEffect, useRef, useState } from "react";

import { ApiError, orbitalPurgeDocument } from "../../api/client";
import type { ApiOrbitalPurgeDocumentResponse } from "../../api/types";

interface PurgeDialogProps {
  /** Target document — closes when ``null``. */
  document: { id: string; original_filename: string; version_count: number } | null;
  onCancel: () => void;
  /** Called once the cascade lands so the parent can refresh its catalog. */
  onPurged: (response: ApiOrbitalPurgeDocumentResponse) => void;
}

/**
 * Hard-delete confirmation modal (#292 §5).
 *
 * Orbital is the sanctioned hard-delete surface per the deletion-rules
 * feedback. The modal forces the operator to type the document's
 * filename verbatim so a misclick can't take down the wrong family;
 * the backend re-validates the same string on the wire.
 *
 * Cascade order (server-side): archive → purge artifacts → drop KG
 * subgraph → emit ``orbital.document.purge`` audit row.
 */
export function PurgeDialog({ document, onCancel, onPurged }: PurgeDialogProps) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Reset the typed buffer + error whenever the target switches.
  useEffect(() => {
    setTyped("");
    setError(null);
    if (document !== null) inputRef.current?.focus();
  }, [document]);

  if (document === null) return null;

  const filenameMatches = typed === document.original_filename;

  async function submit() {
    if (!document) return;
    setBusy(true);
    setError(null);
    try {
      const result = await orbitalPurgeDocument(document.id, typed);
      onPurged(result);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : "Purge failed.";
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
      aria-labelledby="purge-dialog-title"
      data-testid="purge-dialog"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div className="modal-card purge-dialog">
        <h2 id="purge-dialog-title">Purge document permanently?</h2>
        <p className="muted">
          This will <strong>archive</strong> the document, <strong>delete</strong> its
          stored bytes / extractions / semantic JSON / Markdown, and <strong>drop</strong>{" "}
          its knowledge-graph subgraph. The catalog row is preserved as an audit trace.
        </p>
        <ul className="purge-dialog-summary">
          <li>
            Document: <code>{document.original_filename}</code>
          </li>
          <li>
            Versions impacted: <strong>{document.version_count}</strong>
          </li>
          <li>
            Audit event: <code>orbital.document.purge</code>
          </li>
        </ul>
        <p>
          Type the filename <code>{document.original_filename}</code> to confirm.
        </p>
        <input
          ref={inputRef}
          type="text"
          className="purge-dialog-input"
          aria-label="Type filename to confirm"
          value={typed}
          disabled={busy}
          onChange={(event) => setTyped(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && filenameMatches && !busy) submit();
            if (event.key === "Escape" && !busy) onCancel();
          }}
          data-testid="purge-dialog-input"
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
            disabled={!filenameMatches || busy}
            data-testid="purge-dialog-confirm"
          >
            {busy ? "Purging…" : "Purge permanently"}
          </button>
        </div>
      </div>
    </div>
  );
}
