import React, { useEffect, useState } from "react";

import { getHealthWithLatency, setApiBaseUrl } from "../api/client";

interface Props {
  initialValue: string;
  onSave: (next: string) => void;
  onCancel: () => void;
}

type ProbeState =
  | { kind: "checking" }
  | { kind: "ok"; version?: string; latencyMs: number }
  | { kind: "err"; message: string };

export const SettingsPanel: React.FC<Props> = ({ initialValue, onSave, onCancel }) => {
  const [value, setValue] = useState(initialValue);
  const [probe, setProbe] = useState<ProbeState>({ kind: "checking" });

  // Reachability check against the *current* persisted value (not the
  // edit-buffer) so the panel shows the user what they're connected
  // to right now. They can save a new URL and the parent re-mounts
  // with the next initialValue.
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setProbe({ kind: "checking" });
    void (async () => {
      try {
        const { health, latencyMs } = await getHealthWithLatency({
          baseUrl: initialValue,
          signal: controller.signal,
        });
        if (!cancelled) {
          setProbe({ kind: "ok", version: health.version, latencyMs });
        }
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "unreachable";
        setProbe({ kind: "err", message });
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [initialValue]);

  const handleSave = () => {
    const trimmed = value.trim();
    if (trimmed.length === 0) return;
    setApiBaseUrl(trimmed);
    onSave(trimmed);
  };

  return (
    <div className="kw-settings" role="dialog" aria-label="Widget settings">
      <label className="kw-settings__label" htmlFor="kw-settings-url">
        API base URL
      </label>
      <input
        id="kw-settings-url"
        type="url"
        className="kw-input kw-input--mono"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="https://kw-pipeline.example.com"
        autoFocus
      />
      <div className="kw-settings__row">
        <span
          className={
            probe.kind === "ok"
              ? "kw-settings__meta kw-settings__meta--ok"
              : probe.kind === "err"
                ? "kw-settings__meta kw-settings__meta--err"
                : "kw-settings__meta"
          }
        >
          {probe.kind === "checking" && "Checking reachability…"}
          {probe.kind === "ok" &&
            `Currently reachable · ${probe.version ?? "unknown"} · ${probe.latencyMs} ms`}
          {probe.kind === "err" && `Unreachable · ${probe.message}`}
        </span>
        <button type="button" className="kw-btn kw-btn--sm" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="kw-btn kw-btn--sm kw-btn--primary"
          onClick={handleSave}
          disabled={value.trim().length === 0}
        >
          Save
        </button>
      </div>
    </div>
  );
};
