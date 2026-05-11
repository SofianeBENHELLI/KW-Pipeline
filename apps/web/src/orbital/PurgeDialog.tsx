import { useEffect, useState } from "react";

import { ApiError, orbitalPurgeAll, orbitalPurgeDocument } from "../api/client";

import { Btn, Icon } from "./atoms";

const PHRASE = "PURGE ENTIRE ORBITAL CORPUS — IRREVERSIBLE";

interface BaseProps {
  open: boolean;
  onClose: () => void;
  onConfirmed: () => void;
}

export interface PurgeDialogProps extends BaseProps {
  documentId: string;
  filename: string;
  versionCount: number;
}

/**
 * Single-document purge — typed-filename confirmation per the mockup.
 * Backend re-verifies the filename on the wire.
 */
export function PurgeDialog({ open, onClose, onConfirmed, documentId, filename, versionCount }: PurgeDialogProps) {
  const [v, setV] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setV("");
      setError(null);
    }
  }, [open]);

  if (!open) return null;
  const ok = v === filename;

  const purge = async () => {
    if (!ok || busy) return;
    setBusy(true);
    setError(null);
    try {
      await orbitalPurgeDocument(documentId, v);
      onConfirmed();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="orb-app pd-shell" style={{ position: "fixed", inset: 0, zIndex: 110 }}>
      <div className="pd-bg" onClick={onClose}></div>
      <div className="pd-modal">
        <header className="pd-h">
          <span style={{ color: "var(--orb-err)" }}>
            <Icon name="trash" />
          </span>
          <span style={{ fontWeight: 600 }}>Purge document</span>
          <span style={{ flex: 1 }}></span>
          <button className="sp-x" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </header>
        <div className="pd-body">
          <div className="pd-warn">
            <b>This is permanent.</b> Bytes, extractions, semantic JSON, generated Markdown, the KG
            subgraph, and every prior version will be removed. An audit event{" "}
            <code className="orb-mono">admin.purge_document</code> is written and cannot be reversed.
          </div>
          <div className="pd-summary">
            <div className="pd-sumtitle orb-section-h">Will be deleted</div>
            <div className="pd-sumrow">
              <span className="orb-mono">{documentId.slice(0, 12)}</span>
              <span>{filename}</span>
            </div>
            <div className="pd-sumrow">
              <span>Versions</span>
              <span>{versionCount}</span>
            </div>
          </div>
          <div className="pd-confirm">
            <label className="orb-section-h" htmlFor="orb-purge-confirm">
              Type the filename to confirm
            </label>
            <div className="pd-targ orb-mono">{filename}</div>
            <input
              id="orb-purge-confirm"
              className="orb-input"
              value={v}
              onChange={(e) => setV(e.target.value)}
              placeholder="…"
            />
            <div className="pd-confhint orb-mono">
              {ok ? (
                <span style={{ color: "var(--orb-ok)" }}>✓ match — confirm enabled</span>
              ) : (
                <span style={{ color: "var(--orb-fg-dim)" }}>case-sensitive · whitespace-sensitive</span>
              )}
            </div>
            {error && <div style={{ marginTop: 6, color: "var(--orb-err-fg)", fontSize: 11 }}>{error}</div>}
          </div>
        </div>
        <footer className="pd-foot">
          <Btn onClick={onClose} disabled={busy}>Cancel</Btn>
          <span style={{ flex: 1 }}></span>
          <span className="orb-mono pd-foot-m">POST /admin/orbital/purge_document</span>
          <Btn kind="danger" disabled={!ok || busy} icon={<Icon name="trash" />} onClick={() => void purge()}>
            {busy ? "Purging…" : "Purge permanently"}
          </Btn>
        </footer>
      </div>
    </div>
  );
}

export function PurgeAllDialog({ open, onClose, onConfirmed }: BaseProps) {
  const [v, setV] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setV("");
      setError(null);
    }
  }, [open]);

  if (!open) return null;
  const ok = v === PHRASE;

  const purge = async () => {
    if (!ok || busy) return;
    setBusy(true);
    setError(null);
    try {
      await orbitalPurgeAll(v);
      onConfirmed();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="orb-app pd-shell pd-shell--all" style={{ position: "fixed", inset: 0, zIndex: 110 }}>
      <div className="pd-bg" onClick={onClose}></div>
      <div className="pd-modal pd-modal--all">
        <header className="pd-h pd-h--all">
          <Icon name="alert" />
          <span style={{ fontWeight: 700 }}>Purge entire corpus</span>
          <span className="pd-tag-all">NUCLEAR · IRREVERSIBLE</span>
          <span style={{ flex: 1 }}></span>
          <button className="sp-x" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </header>
        <div className="pd-body">
          <div className="pd-warn pd-warn--all">
            Backend re-checks the phrase on the wire and returns <code className="orb-mono">422</code> if it
            does not match exactly. This is <b>irreversible</b>.
          </div>
          <div className="pd-confirm">
            <label className="orb-section-h" htmlFor="orb-purge-all-confirm">
              Type the secret phrase verbatim
            </label>
            <div className="pd-targ orb-mono pd-targ--all">{PHRASE}</div>
            <input
              id="orb-purge-all-confirm"
              className="orb-input pd-input--all"
              value={v}
              onChange={(e) => setV(e.target.value)}
              placeholder="type to enable…"
            />
            <div className="pd-confhint orb-mono">
              {ok ? (
                <span style={{ color: "var(--orb-err)" }}>
                  ✓ match — confirm enabled · backend will re-check
                </span>
              ) : (
                <span style={{ color: "var(--orb-fg-dim)" }}>
                  {v.length}/{PHRASE.length} chars · case-sensitive
                </span>
              )}
            </div>
            {error && <div style={{ marginTop: 6, color: "var(--orb-err-fg)", fontSize: 11 }}>{error}</div>}
          </div>
        </div>
        <footer className="pd-foot">
          <Btn onClick={onClose} disabled={busy}>Cancel</Btn>
          <span style={{ flex: 1 }}></span>
          <span className="orb-mono pd-foot-m">POST /admin/orbital/purge_all?confirm=true</span>
          <Btn kind="danger" disabled={!ok || busy} onClick={() => void purge()}>
            {busy ? "Purging…" : "Purge everything"}
          </Btn>
        </footer>
      </div>
    </div>
  );
}
