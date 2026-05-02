import React, { useEffect, useState } from "react";

import { ApiError, getHealth } from "../api/client";

const POLL_INTERVAL_MS = 30_000;

interface Props {
  apiBaseUrl: string;
  refreshTick: number;
}

type State =
  | { kind: "loading" }
  | { kind: "ok"; status: string; version?: string }
  | { kind: "err"; message: string };

export const HealthCard: React.FC<Props> = ({ apiBaseUrl, refreshTick }) => {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    const fetchOnce = async () => {
      try {
        const health = await getHealth({
          baseUrl: apiBaseUrl,
          signal: controller.signal,
        });
        if (!cancelled) {
          setState({ kind: "ok", status: health.status, version: health.version });
        }
      } catch (error) {
        if (cancelled) return;
        const message =
          error instanceof ApiError
            ? `${error.code}: ${error.detail}`
            : error instanceof Error
              ? error.message
              : "Unreachable";
        setState({ kind: "err", message });
      }
    };

    void fetchOnce();
    const interval = window.setInterval(fetchOnce, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(interval);
    };
  }, [apiBaseUrl, refreshTick]);

  return (
    <section className="kw-card" aria-label="Backend health">
      <h3 className="kw-card__title">Backend</h3>
      <div className="kw-row">
        <span
          className={
            state.kind === "ok"
              ? "kw-dot kw-dot--ok"
              : state.kind === "err"
                ? "kw-dot kw-dot--err"
                : "kw-dot"
          }
          aria-hidden="true"
        />
        {state.kind === "loading" && <span className="kw-status">Checking…</span>}
        {state.kind === "ok" && (
          <span className="kw-status">
            {state.status}
            {state.version ? ` · ${state.version}` : ""}
          </span>
        )}
        {state.kind === "err" && <span className="kw-error">{state.message}</span>}
      </div>
      <div className="kw-status" style={{ marginTop: 4 }}>
        {apiBaseUrl}
      </div>
    </section>
  );
};
