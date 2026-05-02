import React, { useEffect, useState } from "react";

import { ApiError, getHealthWithLatency } from "../api/client";
import { SectionHeader } from "../components/SectionHeader";

const POLL_INTERVAL_MS = 30_000;

interface Props {
  apiBaseUrl: string;
  refreshTick: number;
}

type State =
  | { kind: "loading" }
  | { kind: "ok"; status: string; version?: string; latencyMs: number }
  | { kind: "err"; message: string };

export const HealthCard: React.FC<Props> = ({ apiBaseUrl, refreshTick }) => {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    const fetchOnce = async () => {
      try {
        const { health, latencyMs } = await getHealthWithLatency({
          baseUrl: apiBaseUrl,
          signal: controller.signal,
        });
        if (!cancelled) {
          setState({
            kind: "ok",
            status: health.status,
            version: health.version,
            latencyMs,
          });
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
    <section className="kw-section" aria-label="Backend health">
      <SectionHeader icon="pulse" title="Backend health" meta="auto · 30s" />

      {state.kind === "loading" && (
        <div className="kw-statline">
          <span className="kw-status">Checking…</span>
        </div>
      )}

      {state.kind === "ok" && (
        <div className="kw-statline">
          <span className="kw-statline__word kw-statline__word--ok">{state.status}</span>
          <span className="kw-statline__sep">·</span>
          <span className="kw-mono">{state.version ?? "—"}</span>
          <span className="kw-statline__sep">·</span>
          <span className="kw-mono kw-mono--ok">{state.latencyMs} ms</span>
        </div>
      )}

      {state.kind === "err" && (
        <div className="kw-statline">
          <span className="kw-statline__word kw-statline__word--err">unreachable</span>
        </div>
      )}

      {state.kind === "err" && (
        <div className="kw-error" style={{ marginBottom: 8 }}>
          {state.message}
        </div>
      )}

      <div className="kw-url-chip">
        <span className="kw-url-chip__label">API</span>
        <span className="kw-url-chip__value">{apiBaseUrl}</span>
      </div>
    </section>
  );
};
