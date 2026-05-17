/**
 * Admin UI — Extraction-queue reconciliation (#40, ADR-006 §5).
 *
 * Single-button surface that triggers ``POST /admin/reconcile``. The
 * route flips every version stuck in ``QUEUED_FOR_EXTRACTION`` /
 * ``EXTRACTING`` to ``FAILED`` so the operator can recover via the
 * per-version retry-extraction route (slice 6 §1).
 *
 * Tiny page rather than a 5th hub card so the result envelope
 * (``recovered_count``, ``skipped_inline``) has a proper home and the
 * inline-mode no-op message gets the room it deserves.
 *
 * 403 collapses to the Forbidden state — same pattern as every other
 * admin view.
 */

import { useCallback, useState } from "react";
import { ApiError, runReconcilePass } from "../../api/client";
import type { ApiReconcileResult } from "../../api/types";

export function AdminReconcileView() {
  const [result, setResult] = useState<ApiReconcileResult | null>(null);
  const [error, setError] = useState<ApiError | string | null>(null);
  const [busy, setBusy] = useState(false);

  const handleRun = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const response = await runReconcilePass();
      setResult(response);
    } catch (err: unknown) {
      if (err instanceof ApiError) setError(err);
      else if (err instanceof Error) setError(err.message);
      else setError("Reconcile pass failed.");
    } finally {
      setBusy(false);
    }
  }, []);

  if (error instanceof ApiError && error.status === 403) {
    return (
      <main className="app-shell admin-shell" aria-label="Admin reconcile">
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
    <main className="app-shell admin-shell" aria-label="Admin reconcile">
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Admin</p>
            <h2>Extraction-queue reconciliation</h2>
            <p className="muted">
              Re-runs the stuck-extraction scan (ADR-006 §5). Every
              version still in <code>QUEUED_FOR_EXTRACTION</code> or
              <code> EXTRACTING</code> is flipped to <code>FAILED</code>{" "}
              with the canonical recovery reason; operators recover
              individually via the per-version retry button.
            </p>
          </div>
          <div className="action-row">
            <button
              type="button"
              className="primary-button"
              onClick={() => void handleRun()}
              disabled={busy}
              data-testid="admin-reconcile-run"
            >
              {busy ? "Running…" : "Run reconcile pass"}
            </button>
          </div>
        </header>

        {error instanceof ApiError && error.status !== 403 && (
          <div className="notice danger" role="alert">
            <strong>Reconcile pass failed.</strong>
            <span>{error.detail}</span>
            {error.remediation && (
              <span className="muted">{error.remediation}</span>
            )}
          </div>
        )}
        {typeof error === "string" && (
          <div className="notice danger" role="alert">
            <strong>Reconcile pass failed.</strong>
            <span>{error}</span>
          </div>
        )}

        {result !== null && (
          <div
            className="notice"
            role="status"
            aria-live="polite"
            data-testid="admin-reconcile-result"
          >
            {result.skipped_inline ? (
              <>
                <strong>No-op.</strong>
                <span>
                  {" "}
                  Inline mode (<code>KW_EXTRACTION_INLINE=true</code>) never
                  enqueues, so there is nothing to reconcile.
                </span>
              </>
            ) : (
              <>
                <strong>Recovered {result.recovered_count} version{result.recovered_count === 1 ? "" : "s"}.</strong>
                <span> They are now in FAILED — recover each via the per-version retry button.</span>
              </>
            )}
          </div>
        )}
      </section>
    </main>
  );
}
