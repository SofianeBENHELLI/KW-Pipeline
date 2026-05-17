/**
 * Admin UI — Reconcile extraction queue at ``/admin/reconcile``.
 *
 * Single-action operator surface around ``POST /admin/reconcile``
 * (#40, ADR-006 §5). Drains the stuck-extraction queue: every version
 * stuck in ``QUEUED_FOR_EXTRACTION`` or ``EXTRACTING`` is flipped to
 * ``FAILED`` with the canonical "extraction interrupted by process
 * restart" reason, so the operator can recover it via the per-version
 * Retry-extraction button on the review tab.
 *
 * The page mirrors the AdminHITLView pattern — one big action button
 * + an inline result panel + an error banner — because the wire shape
 * (``recovered_count`` + ``skipped_inline``) is just two scalars and
 * doesn't warrant the metric-grid surface.
 *
 * Out of scope (per slice brief):
 * - Auto-refresh polling — the operator clicks the button deliberately.
 * - Confirm dialog — reviewers wield the trigger intentionally.
 *
 * 503 envelope (e.g. ``KW_HITL_DISABLED`` shape from a disabled admin
 * surface) renders as a danger banner with the envelope's remediation
 * hint, same shape AdminHITLView uses.
 */

import { useCallback, useState } from "react";
import { ApiError, runReconcile } from "../../api/client";
import type { ApiReconcileResult } from "../../api/types";

// ─── Result panel ───────────────────────────────────────────────────────────

interface ReconcileResultPanelProps {
  result: ApiReconcileResult;
  onDismiss: () => void;
}

function ReconcileResultPanel({ result, onDismiss }: ReconcileResultPanelProps) {
  return (
    <div
      className="notice"
      role="status"
      aria-live="polite"
      data-testid="reconcile-result"
    >
      <div>
        <strong>Reconcile complete.</strong>
        <span data-testid="reconcile-result-counts">
          {" "}
          Recovered <b>{result.recovered_count}</b> stuck version
          {result.recovered_count === 1 ? "" : "s"}.
          {result.skipped_inline
            ? " Inline extraction mode is on — the reconcile pass was a no-op by design."
            : ""}
        </span>
      </div>
      <button
        type="button"
        className="text-button"
        onClick={onDismiss}
        aria-label="Dismiss reconcile result"
      >
        Dismiss
      </button>
    </div>
  );
}

// ─── Main view ──────────────────────────────────────────────────────────────

export function AdminReconcileView() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ApiReconcileResult | null>(null);
  const [error, setError] = useState<ApiError | string | null>(null);

  const handleRun = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const fresh = await runReconcile();
      setResult(fresh);
    } catch (err: unknown) {
      if (err instanceof ApiError) setError(err);
      else if (err instanceof Error) setError(err.message);
      else setError("Reconcile failed.");
    } finally {
      setBusy(false);
    }
  }, []);

  // Forbidden state. Mirrors AdminHITLView — the 403 envelope from the
  // backend is the only role probe we ever consult.
  if (error instanceof ApiError && error.status === 403) {
    return (
      <main
        className="app-shell admin-shell"
        aria-label="Admin reconcile extraction queue"
      >
        <section className="workspace">
          <header className="workspace-header">
            <h2>Forbidden</h2>
          </header>
          <p>
            This view requires the <code>admin</code> role.
          </p>
          <p className="muted">{error.detail}</p>
        </section>
      </main>
    );
  }

  return (
    <main
      className="app-shell admin-shell"
      aria-label="Admin reconcile extraction queue"
    >
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Admin</p>
            <h2>Reconcile extraction queue</h2>
            <p className="muted">
              Drain the stuck-extraction queue. Every version still in{" "}
              <code>QUEUED_FOR_EXTRACTION</code> or <code>EXTRACTING</code>{" "}
              is flipped to <code>FAILED</code> so operators can recover it
              via the per-version Retry-extraction button.
            </p>
          </div>
        </header>

        <div className="action-row">
          <button
            type="button"
            className="primary-button"
            onClick={() => void handleRun()}
            disabled={busy}
            aria-busy={busy}
            data-testid="reconcile-run-button"
          >
            {busy ? "Running…" : "Run reconcile"}
          </button>
        </div>

        {error !== null && !(error instanceof ApiError) ? (
          <div
            className="notice danger"
            role="alert"
            data-testid="reconcile-error"
          >
            <strong>Reconcile failed</strong>
            <span>{error}</span>
          </div>
        ) : null}
        {error instanceof ApiError ? (
          <div
            className="notice danger"
            role="alert"
            data-testid="reconcile-error"
          >
            <strong>Reconcile failed</strong>
            <span>{error.detail}</span>
            {error.remediation !== null ? (
              <span className="muted">{error.remediation}</span>
            ) : null}
          </div>
        ) : null}

        {result !== null ? (
          <ReconcileResultPanel
            result={result}
            onDismiss={() => setResult(null)}
          />
        ) : null}
      </section>
    </main>
  );
}
