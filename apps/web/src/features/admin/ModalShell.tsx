/**
 * Inline modal primitive for the admin tool.
 *
 * The web app has no shared modal component (yet). This is the same
 * shell the AdminArchiveView used inline since #274 — extracted into
 * its own file so the new D.9-followup modals (RelinkModal, BulkPurgeModal)
 * can import it without re-implementing the backdrop / header markup.
 */

import type { ReactNode } from "react";

interface ModalShellProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
}

export function ModalShell({ title, onClose, children }: ModalShellProps) {
  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="modal-card">
        <header className="modal-header">
          <h3>{title}</h3>
          <button
            type="button"
            className="text-button"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
