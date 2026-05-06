/**
 * Transitional Demo-dataset toggle — shared React component.
 *
 * Drops into both front-ends (Explorer + Web) inside their existing
 * Settings modals. The whole feature is intentionally isolated under
 * ``apps/_shared/demo-toggle/`` so the rip-out, when the permanent
 * demo workflow lands, is a single ``git rm`` plus a few lines off
 * each modal — same posture as the backend's ``app/services/demo_dataset.py``.
 *
 * Behaviour summary (matches the backend contract in
 * ``apps/api/app/routes/demo.py``):
 *
 *   1. On mount: ``GET /admin/demo/status``. The toggle defaults to
 *      OFF before the first response lands so the UI never flashes a
 *      stale "Loading…" state.
 *   2. Operator toggles ON: ``POST /admin/demo/load`` with
 *      ``force=false``. On 409 with ``non_demo_doc_count > 0`` we
 *      surface an inline confirmation panel; on Force the same call
 *      goes out with ``force=true``. Other errors revert the toggle
 *      to OFF and render an error line.
 *   3. While ``in_progress=true``: poll ``GET /admin/demo/status``
 *      every 2 s. Each poll updates the badge ("Loading X / 47
 *      documents…") so the operator sees progress. When the flag flips
 *      back to false we stop the interval, refresh the corpus through
 *      ``onCorpusRefreshNeeded``, and surface ``last_error`` if the
 *      load aborted.
 *   4. Reset: ``window.confirm`` gate then ``POST /admin/demo/reset``
 *      and ``onCorpusRefreshNeeded`` so the parent's catalog re-fetch
 *      reflects the archive.
 *
 * Polling is owned by a ``setInterval`` ref + an ``AbortController`` ref
 * so an unmount mid-load tears both down cleanly. We also short-circuit
 * with a ``cancelled`` flag inside async resolutions in case unmount
 * lands between the network round-trip and the ``setState`` callback.
 *
 * Style: matches the existing settings UI exactly (section title at
 * ``fontSize: 11, fontWeight: 600, color: "#5C6770"`` with the same
 * ``letterSpacing`` + ``textTransform`` so the new section blends with
 * "Pipeline status" and "Backend configuration" above it).
 */

import React, { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api-core";
import {
  DemoConflictError,
  fetchDemoStatus,
  postDemoLoad,
  postDemoReset,
  type DemoStatusResponse,
} from "./api";

/** Capacity hard-coded in :mod:`apps.api.app.services.demo_dataset`. */
const DEMO_DOC_TOTAL = 47;
/** Poll interval matches the brief — keeps the UI badge live. */
const POLL_INTERVAL_MS = 2000;

interface Props {
  /**
   * Base URL of the KW-Pipeline backend, resolved by the host app from
   * its own settings module. The component never reads this from
   * storage on its own — the host owns base-URL resolution.
   */
  apiBaseUrl: string;
  /**
   * Called once after a load run finishes successfully (polling
   * detected ``in_progress`` flipping back to false) and once after a
   * successful reset. The parent uses this to bump its document /
   * graph fetch and reflect the new corpus.
   */
  onCorpusRefreshNeeded: () => void;
}

/**
 * Pretty-print an error for the inline error line. We pass through
 * :class:`ApiError`'s envelope code/detail rather than the raw
 * ``Error.message`` so the operator sees the backend's structured
 * remediation text when present.
 */
function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    return err.remediation
      ? `${err.code}: ${err.detail} — ${err.remediation}`
      : `${err.code}: ${err.detail}`;
  }
  if (err instanceof Error) return err.message;
  return "Unknown error";
}

export const DemoToggle: React.FC<Props> = ({ apiBaseUrl, onCorpusRefreshNeeded }) => {
  // ``status`` is ``null`` until the very first ``fetchDemoStatus``
  // resolves. The toggle still renders OFF in that window — the brief
  // calls for "OFF by default" rather than a loading skeleton.
  const [status, setStatus] = useState<DemoStatusResponse | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ count: number } | null>(null);

  // Polling primitives — refs so callbacks don't re-create with every
  // status update. ``stopPolling`` is intentionally idempotent.
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollAbortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current !== null) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    if (pollAbortRef.current !== null) {
      pollAbortRef.current.abort();
      pollAbortRef.current = null;
    }
  }, []);

  /**
   * Apply a fresh status snapshot. Drives the polling lifecycle:
   * ``in_progress=false`` after a previous ``true`` triggers a parent
   * corpus refresh and stops the interval.
   */
  const applyStatus = useCallback(
    (next: DemoStatusResponse, opts: { fromPolling?: boolean } = {}) => {
      if (!mountedRef.current) return;
      setStatus((prev) => {
        // Polling's "in_progress flipped to false" edge — fire the
        // parent's refresh exactly once, then halt the interval.
        const wasInProgress = prev?.in_progress === true;
        if (wasInProgress && !next.in_progress) {
          stopPolling();
          onCorpusRefreshNeeded();
        }
        return next;
      });
      setEnabled(next.loaded || next.in_progress);
      // A polling tick that surfaces ``last_error`` (loader aborted
      // mid-run) should also bubble it to the inline error line.
      if (opts.fromPolling && next.last_error) {
        setError(next.last_error);
      }
    },
    [onCorpusRefreshNeeded, stopPolling],
  );

  /** Start the 2 s polling interval. Idempotent. */
  const startPolling = useCallback(() => {
    if (pollIntervalRef.current !== null) return;
    pollIntervalRef.current = setInterval(() => {
      // Each tick gets its own AbortController so unmount or a
      // subsequent stopPolling() cleanly tears the in-flight request
      // down without trying to call setState on an unmounted component.
      const ctrl = new AbortController();
      pollAbortRef.current = ctrl;
      fetchDemoStatus(apiBaseUrl, ctrl.signal)
        .then((next) => applyStatus(next, { fromPolling: true }))
        .catch((err: unknown) => {
          if ((err as { name?: string })?.name === "AbortError") return;
          if (!mountedRef.current) return;
          // Don't tear the polling down on a single failed poll — the
          // backend may be momentarily unreachable. We do surface the
          // message so the operator knows polling has degraded.
          setError(describeError(err));
        });
    }, POLL_INTERVAL_MS);
  }, [apiBaseUrl, applyStatus]);

  // Mount-time status fetch + polling kickoff if the backend reports
  // an already-in-flight load (e.g. operator refreshed the page during
  // a previous run).
  useEffect(() => {
    mountedRef.current = true;
    const ctrl = new AbortController();
    fetchDemoStatus(apiBaseUrl, ctrl.signal)
      .then((next) => {
        applyStatus(next);
        if (next.in_progress) startPolling();
      })
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === "AbortError") return;
        if (!mountedRef.current) return;
        setError(describeError(err));
      });
    return () => {
      mountedRef.current = false;
      ctrl.abort();
      stopPolling();
    };
    // ``apiBaseUrl`` should never change while the modal is mounted
    // (host-owned), but list it so a future re-mount under a different
    // backend re-syncs cleanly.
  }, [apiBaseUrl, applyStatus, startPolling, stopPolling]);

  /**
   * Issue ``POST /admin/demo/load`` with the given ``force`` flag.
   * Used by both the initial toggle-on path (force=false) and the
   * "Force load" button on the conflict panel (force=true).
   */
  const issueLoad = useCallback(
    async (force: boolean) => {
      setBusy(true);
      setError(null);
      try {
        const next = await postDemoLoad(apiBaseUrl, force);
        setConflict(null);
        applyStatus(next);
        if (next.in_progress) startPolling();
      } catch (err: unknown) {
        if (err instanceof DemoConflictError) {
          // 409: surface the conflict panel so the operator can confirm
          // the destructive override. Toggle stays "ON" visually so the
          // intent is clear; the panel sits beneath it.
          setConflict({ count: err.nonDemoDocCount });
          setEnabled(true);
        } else {
          // Any other failure: revert the toggle to OFF and render the
          // inline error so the operator can retry.
          setEnabled(false);
          setError(describeError(err));
        }
      } finally {
        if (mountedRef.current) setBusy(false);
      }
    },
    [apiBaseUrl, applyStatus, startPolling],
  );

  const onToggle = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const next = e.target.checked;
      // Optimistically flip the visual state so the operator sees a
      // response before the network round-trip. ``issueLoad``'s catch
      // branch reverts on failure.
      setEnabled(next);
      if (next) {
        void issueLoad(false);
      } else {
        // Toggling OFF without an explicit Reset doesn't have a
        // backend semantics — surface the affordance via the Reset
        // button instead. We just leave the toggle visually off.
        setConflict(null);
      }
    },
    [issueLoad],
  );

  const onCancelConflict = useCallback(() => {
    setConflict(null);
    setEnabled(false);
  }, []);

  const onForce = useCallback(() => {
    void issueLoad(true);
  }, [issueLoad]);

  const onReset = useCallback(async () => {
    if (typeof window !== "undefined") {
      const ok = window.confirm(
        "Archive all demo documents? This cannot be undone via this UI.",
      );
      if (!ok) return;
    }
    setBusy(true);
    setError(null);
    try {
      const next = await postDemoReset(apiBaseUrl);
      applyStatus(next);
      onCorpusRefreshNeeded();
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  }, [apiBaseUrl, applyStatus, onCorpusRefreshNeeded]);

  // ── Derived rendering bits ─────────────────────────────────────────
  const inProgress = status?.in_progress === true;
  const loaded = status?.loaded === true;
  const docCount = status?.demo_doc_count ?? 0;
  const disabledControls = busy || inProgress;

  let statusLine: string;
  if (inProgress) {
    statusLine = `Loading ${docCount} / ${DEMO_DOC_TOTAL} documents…`;
  } else if (loaded) {
    statusLine = `Loaded · ${docCount} demo document${docCount === 1 ? "" : "s"} in catalog`;
  } else if (status === null) {
    statusLine = "Off";
  } else {
    statusLine = "Off · no demo documents in catalog";
  }

  return (
    <div data-testid="demo-toggle" style={{ marginBottom: 14, opacity: busy ? 0.6 : 1 }}>
      <h3
        style={{
          margin: "0 0 8px 0",
          fontSize: 11,
          fontWeight: 600,
          color: "#5C6770",
          letterSpacing: "0.08em",
          textTransform: "uppercase",
        }}
      >
        Transitional features
      </h3>
      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: "#3D4751",
          marginBottom: 6,
          paddingBottom: 4,
          borderBottom: "1px solid #E1E5EA",
        }}
      >
        Demo dataset
      </div>

      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 13,
          color: "#2A3138",
          padding: "6px 0",
        }}
      >
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabledControls}
          onChange={onToggle}
          data-testid="demo-toggle-checkbox"
          aria-label="Load automotive demo corpus"
        />
        <span>Load automotive demo corpus</span>
      </label>

      <div
        data-testid="demo-toggle-status"
        style={{
          fontSize: 11,
          color: "#5C6770",
          fontFamily: "ui-monospace, monospace",
          marginTop: 2,
        }}
      >
        {statusLine}
      </div>

      {loaded && !inProgress && (
        <button
          type="button"
          onClick={onReset}
          disabled={disabledControls}
          data-testid="demo-toggle-reset"
          style={{
            marginTop: 8,
            border: "1px solid #C8CDD4",
            borderRadius: 4,
            background: "white",
            fontSize: 12,
            padding: "4px 10px",
            cursor: disabledControls ? "not-allowed" : "pointer",
          }}
        >
          Reset demo dataset
        </button>
      )}

      {conflict !== null && (
        <div
          role="alertdialog"
          data-testid="demo-toggle-conflict-panel"
          style={{
            marginTop: 10,
            padding: 10,
            border: "1px solid #E5C792",
            background: "#FFF8E5",
            borderRadius: 6,
            color: "#5A4317",
            fontSize: 12,
          }}
        >
          <div style={{ marginBottom: 6 }}>
            <strong>{conflict.count}</strong> non-demo document
            {conflict.count === 1 ? " is" : "s are"} already present. Load anyway?
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <button
              type="button"
              onClick={onForce}
              disabled={disabledControls}
              data-testid="demo-toggle-force"
              style={{
                border: "1px solid #C77B22",
                borderRadius: 4,
                background: "#C77B22",
                color: "white",
                fontSize: 12,
                padding: "4px 10px",
                cursor: disabledControls ? "not-allowed" : "pointer",
              }}
            >
              Force load
            </button>
            <button
              type="button"
              onClick={onCancelConflict}
              disabled={disabledControls}
              style={{
                border: "1px solid #C8CDD4",
                borderRadius: 4,
                background: "white",
                fontSize: 12,
                padding: "4px 10px",
                cursor: disabledControls ? "not-allowed" : "pointer",
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error !== null && (
        <div
          role="alert"
          data-testid="demo-toggle-error"
          style={{
            marginTop: 8,
            padding: 8,
            border: "1px solid #E5B3B3",
            background: "#FFF5F5",
            borderRadius: 6,
            color: "#9C2A2A",
            fontSize: 12,
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
};
