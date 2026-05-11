import { useEffect, useState } from "react";

import {
  ApiError,
  orbitalPurgeAll,
  orbitalPurgeDocument,
} from "../api/client";
import { ORBITAL_PURGE_ALL_PHRASE } from "../api/types";
import { Btn, Card, Mono } from "../ui/orb";
import { Input } from "../ui/orb/atoms";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  onConfirmed: () => void;
}

/**
 * Phase-7 typed-confirmation purge dialog for a single document. The
 * operator must type the document's exact filename to unlock the
 * Purge button — same contract the backend re-verifies on the wire.
 */
export interface OrbPurgeDialogProps extends DialogProps {
  documentId: string;
  filename: string;
  versionCount: number;
}

export function OrbPurgeDialog({ open, onClose, onConfirmed, documentId, filename, versionCount }: OrbPurgeDialogProps) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setTyped("");
      setError(null);
    }
  }, [open]);

  if (!open) return null;
  const ok = typed === filename;

  const purge = async () => {
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      await orbitalPurgeDocument(documentId, typed);
      onConfirmed();
      onClose();
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalShell title="Purge document" onClose={onClose}>
      <Card className="orb-modal__body">
        <p>
          This will <strong>permanently delete</strong> {versionCount} version(s) of{" "}
          <Mono>{filename}</Mono>: stored bytes, raw extractions, semantic JSON, generated
          Markdown, and the projected knowledge-graph subgraph. The audit event is preserved.
        </p>
        <p>
          To confirm, type the document's exact filename: <br />
          <Mono>{filename}</Mono>
        </p>
        <Input
          aria-label="Confirm filename"
          placeholder="filename"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
        />
        {error && <p style={{ color: "var(--orb-err-fg)", marginTop: 8 }}>{error}</p>}
      </Card>
      <div className="orb-modal__actions">
        <Btn kind="ghost" onClick={onClose} disabled={busy}>Cancel</Btn>
        <span style={{ flex: 1 }} />
        <Btn kind="danger" onClick={() => void purge()} disabled={!ok || busy}>
          {busy ? "Purging…" : "Purge document"}
        </Btn>
      </div>
    </ModalShell>
  );
}

/**
 * Phase-7 nuclear-option dialog — operator must type the secret phrase
 * verbatim. Backend re-verifies on the wire (422 on mismatch).
 */
export type OrbPurgeAllDialogProps = DialogProps;

export function OrbPurgeAllDialog({ open, onClose, onConfirmed }: OrbPurgeAllDialogProps) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setTyped("");
      setError(null);
    }
  }, [open]);

  if (!open) return null;
  const ok = typed === ORBITAL_PURGE_ALL_PHRASE;

  const purge = async () => {
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      await orbitalPurgeAll(typed);
      onConfirmed();
      onClose();
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalShell title="Purge ALL documents" onClose={onClose}>
      <Card className="orb-modal__body orb-modal__body--danger">
        <p>
          <strong>Irreversible.</strong> This deletes every document in the corpus —
          stored bytes, extractions, semantic JSON, Markdown, knowledge graph, embeddings.
          The audit trail is preserved.
        </p>
        <p>
          Type <Mono>{ORBITAL_PURGE_ALL_PHRASE}</Mono> to confirm.
        </p>
        <Input
          aria-label="Confirm purge phrase"
          placeholder={ORBITAL_PURGE_ALL_PHRASE}
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
        />
        {error && <p style={{ color: "var(--orb-err-fg)", marginTop: 8 }}>{error}</p>}
      </Card>
      <div className="orb-modal__actions">
        <Btn kind="ghost" onClick={onClose} disabled={busy}>Cancel</Btn>
        <span style={{ flex: 1 }} />
        <Btn kind="danger" onClick={() => void purge()} disabled={!ok || busy}>
          {busy ? "Purging…" : "Purge everything"}
        </Btn>
      </div>
    </ModalShell>
  );
}

/* -------- shared modal shell -------- */

function ModalShell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="orb-modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="orb-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="orb-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="orb-modal__head">
          <h2 id="orb-modal-title">{title}</h2>
          <button type="button" className="orb-btn orb-btn--ghost orb-btn--icon" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
