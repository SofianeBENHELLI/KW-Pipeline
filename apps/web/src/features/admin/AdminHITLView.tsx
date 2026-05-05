/**
 * Admin UI — HITL routing dashboard (#215, EPIC-A close-out, ADR-023 §6).
 *
 * Read-only operator surface for the HITL routing layer:
 *
 * 1. **Config snapshot** — four metric cards at the top show the
 *    deployment posture (scorer enabled, force-auto override,
 *    auto-validate threshold, pending auto-promotion queue depth).
 *    The pending-count card carries the "Run pass" trigger so the
 *    operator can drain the queue without leaving the page.
 *
 * 2. **Per-bucket table** — one row per
 *    ``(content_type, topic_cluster)`` SPC bucket the router has
 *    touched. Sorted by ``drift_ratio`` DESC server-side so the
 *    noisiest buckets surface at the top. Drift ratio + effective
 *    sample rate are color-coded so the operator can scan for
 *    hot-spots without reading numbers.
 *
 * 3. **Auto-refresh** — the snapshot polls every 30 s by default with
 *    a toggle so an operator parking on the page sees fresh state
 *    without a manual reload. Off by default for the test
 *    environment — auto-refresh fires only when explicitly enabled
 *    so component-mount in unit tests doesn't double-fetch.
 *
 * No client-side role check: the API surface is gated server-side
 * (``require_admin``) and a 403 envelope from any of the API calls
 * collapses the page to a "Forbidden" state. Same pattern
 * AdminArchiveView (#274) established.
 *
 * UX decisions worth flagging:
 *
 * - The "Run pass" trigger is enabled even when ``pending_auto_promotions === 0``
 *   so an admin can sanity-check the worker is wired without faking
 *   a pending row. The result panel renders the empty
 *   ``scanned=0`` envelope which doubles as a smoke test.
 * - The 503 ``KW_HITL_DISABLED`` envelope (scorer disabled) renders
 *   a dedicated state card rather than the generic "Failed to load"
 *   banner. The remediation copy comes from the envelope verbatim
 *   so an operator who flipped ``KW_HITL_DISABLE_SCORER=true`` for
 *   a customer demo sees the exact unset hint.
 * - Auto-refresh is *not* a websocket — the dashboard is operator-
 *   facing and the read is cheap (one Cypher / SQLite scan). Polling
 *   keeps the wire shape simple and avoids a long-lived connection
 *   on the auth-gated route.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  getAdminHITLState,
  runAutoPromotePass,
} from "../../api/client";
import type {
  ApiAdminHITLStateResponse,
  ApiAutoPromoteResult,
  ApiBucketState,
} from "../../api/types";

// ─── Constants ──────────────────────────────────────────────────────────────

/** Default auto-refresh cadence (ms). Operators can flip the toggle
 *  off if they're investigating a single hot-spot and don't want the
 *  table re-sorting underneath them. Kept as a const so future
 *  cadence tuning lands in one place. */
const AUTO_REFRESH_INTERVAL_MS = 30_000;

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Format ``last_decision_at`` as a relative phrase. ``"never"`` for
 *  null timestamps (defensive — happens when the dashboard surfaces
 *  a bucket that exists only because of a stale ``record_drift_event``). */
export function formatRelativeDecision(
  isoString: string | null,
  now: Date = new Date(),
): string {
  if (isoString === null) return "never";
  const then = new Date(isoString);
  const ms = now.getTime() - then.getTime();
  if (Number.isNaN(ms) || ms < 0) return isoString;
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months === 1 ? "" : "s"} ago`;
  return then.toISOString().slice(0, 10);
}

/** Render a drift ratio with the threshold-coded class so the table
 *  cell carries the visual signal directly. */
export function driftRatioClass(
  ratio: number,
  threshold: number,
): "drift-ok" | "drift-elevated" {
  return ratio >= threshold ? "drift-elevated" : "drift-ok";
}

/** Render an effective sampling rate against the baseline so a
 *  ramped bucket shows up yellow. ``rate > baseline`` is the
 *  detector's "this bucket is escalating" signal. */
export function rateElevationClass(
  rate: number,
  baseline: number,
): "rate-baseline" | "rate-elevated" {
  // Tolerate float rounding so a baseline that round-trips through
  // JSON doesn't accidentally render as elevated.
  return rate > baseline + 1e-9 ? "rate-elevated" : "rate-baseline";
}

// ─── Run-pass result panel ──────────────────────────────────────────────────

interface RunPassResultProps {
  result: ApiAutoPromoteResult;
  onDismiss: () => void;
}

function RunPassResultPanel({ result, onDismiss }: RunPassResultProps) {
  return (
    <div
      className="notice"
      role="status"
      aria-live="polite"
      data-testid="run-pass-result"
    >
      <div>
        <strong>Auto-promotion pass complete.</strong>
        <span>
          {" "}
          Scanned {result.scanned}, promoted {result.promoted.length}, skipped{" "}
          {result.skipped.length}, failed {result.failed.length}.
        </span>
      </div>
      <button
        type="button"
        className="text-button"
        onClick={onDismiss}
        aria-label="Dismiss run-pass result"
      >
        Dismiss
      </button>
    </div>
  );
}

// ─── Main view ──────────────────────────────────────────────────────────────

interface MetricCardProps {
  label: string;
  value: React.ReactNode;
  badge?: "ok" | "warning" | "danger";
  testId?: string;
  children?: React.ReactNode;
}

function MetricCard({ label, value, badge, testId, children }: MetricCardProps) {
  const badgeClass =
    badge === "ok"
      ? "badge-ok"
      : badge === "warning"
        ? "badge-warning"
        : badge === "danger"
          ? "badge-danger"
          : "";
  return (
    <div className="metric-card" data-testid={testId}>
      <p className="metric-label">{label}</p>
      <p className={`metric-value ${badgeClass}`}>{value}</p>
      {children}
    </div>
  );
}

export function AdminHITLView() {
  const [state, setState] = useState<ApiAdminHITLStateResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<ApiError | string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [runPassBusy, setRunPassBusy] = useState(false);
  const [runPassError, setRunPassError] = useState<string | null>(null);
  const [runPassResult, setRunPassResult] =
    useState<ApiAutoPromoteResult | null>(null);

  // Hold the latest state in a ref so the auto-refresh interval can
  // see the freshest snapshot without re-binding the timer on every
  // re-render.
  const latestStateRef = useRef<ApiAdminHITLStateResponse | null>(null);
  latestStateRef.current = state;

  const loadState = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const snapshot = await getAdminHITLState();
      setState(snapshot);
    } catch (err: unknown) {
      if (err instanceof ApiError) setLoadError(err);
      else if (err instanceof Error) setLoadError(err.message);
      else setLoadError("Failed to load HITL state.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadState();
  }, [loadState]);

  // Auto-refresh loop. Off by default — the page mounts once and
  // re-fetches only on the user toggling the checkbox. Operators
  // who want a live view flip it on and the snapshot refreshes
  // every 30 s without a websocket.
  useEffect(() => {
    if (!autoRefresh) return;
    const id = window.setInterval(() => {
      void loadState();
    }, AUTO_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [autoRefresh, loadState]);

  const handleRunPass = useCallback(async () => {
    setRunPassBusy(true);
    setRunPassError(null);
    try {
      const result = await runAutoPromotePass();
      setRunPassResult(result);
      // Refresh state so the pending count + bucket counters reflect
      // the post-pass shape immediately, without waiting for the
      // 30 s auto-refresh cadence.
      await loadState();
    } catch (err: unknown) {
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : "Run pass failed.";
      setRunPassError(message);
    } finally {
      setRunPassBusy(false);
    }
  }, [loadState]);

  // Forbidden state. Same pattern AdminArchiveView (#274) — the
  // server's 403 is the only role probe we ever consult.
  if (loadError instanceof ApiError && loadError.status === 403) {
    return (
      <main className="app-shell admin-shell" aria-label="Admin HITL dashboard">
        <section className="workspace">
          <header className="workspace-header">
            <h2>Forbidden</h2>
          </header>
          <p>
            This view requires the <code>admin</code> role.
          </p>
          <p className="muted">{loadError.detail}</p>
        </section>
      </main>
    );
  }

  // 503 KW_HITL_DISABLED → dedicated card with the envelope's
  // remediation hint. A scorer-disabled deployment is a deliberate
  // operator action, not a failure to load, so the empty state
  // copy is friendlier than the generic banner.
  if (loadError instanceof ApiError && loadError.status === 503) {
    return (
      <main className="app-shell admin-shell" aria-label="Admin HITL dashboard">
        <section className="workspace">
          <header className="workspace-header">
            <div>
              <p className="eyebrow">Admin</p>
              <h2>HITL Routing State</h2>
            </div>
          </header>
          <div
            className="notice danger"
            role="alert"
            data-testid="hitl-disabled-state"
          >
            <strong>HITL disabled.</strong>
            <span>{loadError.detail}</span>
            {loadError.remediation !== null ? (
              <span className="muted">{loadError.remediation}</span>
            ) : null}
          </div>
        </section>
      </main>
    );
  }

  const buckets: ApiBucketState[] = state?.buckets ?? [];

  return (
    <main className="app-shell admin-shell" aria-label="Admin HITL dashboard">
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Admin</p>
            <h2>HITL Routing State</h2>
          </div>
          <div className="action-row">
            <label className="auto-refresh-toggle">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                data-testid="auto-refresh-toggle"
              />
              <span>Auto-refresh ({AUTO_REFRESH_INTERVAL_MS / 1000}s)</span>
            </label>
            <button
              type="button"
              className="secondary-button"
              onClick={() => void loadState()}
              disabled={loading}
            >
              {loading ? "Loading…" : "Refresh"}
            </button>
          </div>
        </header>

        {loadError !== null && !(loadError instanceof ApiError) ? (
          <div className="notice danger" role="alert">
            <strong>Failed to load</strong>
            <span>{loadError}</span>
          </div>
        ) : loadError instanceof ApiError ? (
          <div className="notice danger" role="alert">
            <strong>Failed to load</strong>
            <span>{loadError.detail}</span>
          </div>
        ) : null}

        {state !== null ? (
          <>
            {/* Top bar: 4 config metric cards. */}
            <div className="metric-grid" data-testid="hitl-config-cards">
              <MetricCard
                label="Status"
                value={state.enabled ? "Enabled" : "Disabled"}
                badge={state.enabled ? "ok" : "danger"}
                testId="card-status"
              />
              <MetricCard
                label="Force-auto corpus"
                value={state.force_auto_corpus ? "ON" : "OFF"}
                badge={state.force_auto_corpus ? "warning" : "ok"}
                testId="card-force-auto"
              />
              <MetricCard
                label="Auto-validate threshold"
                value={state.threshold.toFixed(2)}
                testId="card-threshold"
              />
              <MetricCard
                label="Pending auto-promotions"
                value={state.pending_auto_promotions}
                testId="card-pending"
              >
                <button
                  type="button"
                  className="primary-button"
                  onClick={() => void handleRunPass()}
                  disabled={runPassBusy}
                  aria-busy={runPassBusy}
                  data-testid="run-pass-button"
                >
                  {runPassBusy ? "Running…" : "Run pass"}
                </button>
              </MetricCard>
            </div>

            {runPassError !== null ? (
              <div
                className="notice danger"
                role="alert"
                data-testid="run-pass-error"
              >
                <strong>Run pass failed</strong>
                <span>{runPassError}</span>
              </div>
            ) : null}
            {runPassResult !== null ? (
              <RunPassResultPanel
                result={runPassResult}
                onDismiss={() => setRunPassResult(null)}
              />
            ) : null}

            {/* Per-bucket table. */}
            {buckets.length === 0 ? (
              <p className="muted" data-testid="empty-buckets">
                No buckets recorded yet. The HITL router populates buckets
                as it makes routing decisions.
              </p>
            ) : (
              <table
                className="admin-hitl-table"
                aria-label="HITL sampling buckets"
              >
                <thead>
                  <tr>
                    <th scope="col">Content type</th>
                    <th scope="col">Topic cluster</th>
                    <th scope="col">Samples</th>
                    <th scope="col">Auto / Human</th>
                    <th scope="col">Drift ratio</th>
                    <th scope="col">Effective rate</th>
                    <th scope="col">Last decision</th>
                  </tr>
                </thead>
                <tbody>
                  {buckets.map((bucket) => (
                    <BucketRow
                      key={`${bucket.content_type}::${bucket.topic_cluster}`}
                      bucket={bucket}
                      driftThreshold={state.drift_threshold}
                      baselineRate={state.baseline_sample_rate}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </>
        ) : loading ? (
          <p className="muted" role="status" aria-live="polite">
            Loading…
          </p>
        ) : null}
      </section>
    </main>
  );
}

interface BucketRowProps {
  bucket: ApiBucketState;
  driftThreshold: number;
  baselineRate: number;
}

function BucketRow({ bucket, driftThreshold, baselineRate }: BucketRowProps) {
  // Memoise the formatted strings so a busy table doesn't recompute
  // them on every parent re-render — the auto-refresh cadence
  // re-renders the whole table every 30 s.
  const driftCls = useMemo(
    () => driftRatioClass(bucket.drift_ratio, driftThreshold),
    [bucket.drift_ratio, driftThreshold],
  );
  const rateCls = useMemo(
    () => rateElevationClass(bucket.effective_sample_rate, baselineRate),
    [bucket.effective_sample_rate, baselineRate],
  );
  return (
    <tr data-testid="hitl-bucket-row">
      <td>
        <code>{bucket.content_type}</code>
      </td>
      <td>
        <code>{bucket.topic_cluster}</code>
      </td>
      <td data-testid="row-samples-taken">{bucket.samples_taken}</td>
      <td data-testid="row-auto-human">
        {bucket.samples_auto} / {bucket.samples_human}
      </td>
      <td className={driftCls} data-testid="row-drift-ratio">
        {bucket.drift_ratio.toFixed(2)}
      </td>
      <td className={rateCls} data-testid="row-effective-rate">
        {bucket.effective_sample_rate.toFixed(3)}
      </td>
      <td className="muted" data-testid="row-last-decision">
        {formatRelativeDecision(bucket.last_decision_at)}
      </td>
    </tr>
  );
}
