/**
 * Knowledge Forge Settings — web surface.
 *
 * Same data layer as the widget settings tile (the shared
 * ``apps/_shared/settings-hub`` package), rendered as a modal overlay
 * that the user opens via a gear button in the app shell.
 *
 * The web app does not let users edit the API base URL — that's a
 * build-time constant (``VITE_API_BASE_URL``). The widget tile is the
 * surface where end users override it on a per-tile basis.
 */

import { useEffect, useState } from "react";

import {
  ApiError,
  buildDiagnosticTiles,
  buildSettingsSections,
  fetchAdminConfig,
  type AdminConfigResponse,
  type DiagnosticTile,
  type SettingRow,
} from "../../../../_shared/settings-hub";
import { DemoToggle } from "../../../../_shared/demo-toggle";
import { getApiBaseUrl } from "../../api/client";

type State =
  | { kind: "loading" }
  | { kind: "ok"; config: AdminConfigResponse }
  | { kind: "err"; message: string };

const STATE_GLYPH: Record<DiagnosticTile["state"], string> = {
  ok: "✓",
  off: "✗",
  warn: "⚠",
};

const STATE_COLOR: Record<DiagnosticTile["state"], string> = {
  ok: "#3F8E60",
  off: "#A1A8B0",
  warn: "#C77B22",
};

interface Props {
  open: boolean;
  onClose: () => void;
  /**
   * Re-fetch the document catalog after the transitional Demo-toggle
   * mutates it (load finishes, or dataset is reset). The reviewer
   * shell wires this to ``useDocumentCatalog().refreshAll`` so the
   * pipeline widget, review workspace, and search panel all reflect
   * the new corpus on the next render.
   *
   * Optional so existing call sites (the SettingsLauncher's modal in
   * tests) keep type-checking without forcing a refresh wiring;
   * production mounts always supply it.
   */
  onCorpusRefreshNeeded?: () => void;
}

function InfoTip({ text }: { text: string }) {
  if (!text) return null;
  return (
    <span
      role="img"
      aria-label="More info"
      title={text}
      data-testid="settings-info-tip"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 14,
        height: 14,
        marginLeft: 6,
        borderRadius: "50%",
        border: "1px solid #C8CDD4",
        color: "#5C6770",
        fontSize: 9,
        cursor: "help",
        userSelect: "none",
      }}
    >
      i
    </span>
  );
}

function TileCard({ tile }: { tile: DiagnosticTile }) {
  return (
    <div
      data-testid={`settings-tile-${tile.id}`}
      data-state={tile.state}
      style={{
        flex: "1 1 0",
        minWidth: 110,
        padding: "10px 12px",
        border: "1px solid #E1E5EA",
        borderRadius: 6,
        background: tile.state === "off" ? "#FAFBFC" : "#FFFFFF",
        opacity: tile.state === "off" ? 0.7 : 1,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 18,
          color: STATE_COLOR[tile.state],
        }}
      >
        <span aria-hidden="true">{STATE_GLYPH[tile.state]}</span>
        <span style={{ fontSize: 12, fontWeight: 600, color: "#2A3138" }}>
          {tile.label}
        </span>
      </div>
      <div
        style={{
          marginTop: 4,
          fontSize: 11,
          color: "#5C6770",
          fontFamily: "ui-monospace, monospace",
        }}
      >
        {tile.sublabel}
      </div>
    </div>
  );
}

function SettingRowItem({ row }: { row: SettingRow }) {
  const inactive = row.status === "inactive";
  return (
    <div
      data-testid={`settings-row-${row.key}`}
      data-status={row.status}
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
        gap: 12,
        alignItems: "baseline",
        padding: "6px 0",
        borderBottom: "1px solid #F0F2F5",
        opacity: inactive ? 0.55 : 1,
      }}
    >
      <div style={{ fontSize: 13, color: "#2A3138" }}>
        {row.label}
        <InfoTip text={row.help} />
      </div>
      <div
        style={{
          fontSize: 13,
          color: inactive ? "#7A828A" : "#1A1F25",
          fontFamily: "ui-monospace, monospace",
          fontFeatureSettings: '"tnum"',
          wordBreak: "break-word",
        }}
      >
        {row.value === null || row.value === "" ? "—" : String(row.value)}
      </div>
    </div>
  );
}

export function SettingsModal({ open, onClose, onCorpusRefreshNeeded }: Props) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const apiBaseUrl = getApiBaseUrl();

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let cancelled = false;
    setState({ kind: "loading" });

    fetchAdminConfig(apiBaseUrl, controller.signal)
      .then((config) => {
        if (!cancelled) setState({ kind: "ok", config });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if ((err as { name?: string })?.name === "AbortError") return;
        const message =
          err instanceof ApiError
            ? `${err.code}: ${err.detail}`
            : err instanceof Error
              ? err.message
              : "Failed to load configuration";
        setState({ kind: "err", message });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [open, apiBaseUrl]);

  if (!open) return null;

  return (
    <div
      data-testid="settings-modal"
      role="dialog"
      aria-label="Knowledge Forge settings"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(15, 22, 30, 0.45)",
        zIndex: 1000,
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        padding: "5vh 16px",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          background: "white",
          width: "min(720px, 100%)",
          maxHeight: "90vh",
          overflow: "auto",
          borderRadius: 8,
          padding: 20,
          boxShadow: "0 12px 40px rgba(0,0,0,0.18)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 12,
          }}
        >
          <h2 style={{ margin: 0, fontSize: 16 }}>Knowledge Forge — Settings</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close settings"
            data-testid="settings-modal-close"
            style={{
              border: "1px solid #C8CDD4",
              borderRadius: 4,
              background: "white",
              fontSize: 13,
              padding: "4px 10px",
              cursor: "pointer",
            }}
          >
            Close
          </button>
        </div>

        {state.kind === "loading" && (
          <div data-testid="settings-modal-loading" style={{ color: "#5C6770" }}>
            Loading configuration…
          </div>
        )}

        {state.kind === "err" && (
          <div
            role="alert"
            data-testid="settings-modal-error"
            style={{
              padding: 10,
              border: "1px solid #E5B3B3",
              background: "#FFF5F5",
              borderRadius: 6,
              color: "#9C2A2A",
            }}
          >
            Failed to load configuration: {state.message}
          </div>
        )}

        {state.kind === "ok" && (
          <>
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
              Pipeline status
            </h3>
            <div
              data-testid="settings-modal-tiles"
              style={{
                display: "flex",
                gap: 8,
                flexWrap: "wrap",
                marginBottom: 16,
              }}
            >
              {buildDiagnosticTiles(state.config).map((tile) => (
                <TileCard key={tile.id} tile={tile} />
              ))}
            </div>

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
              Backend configuration
            </h3>
            {buildSettingsSections(state.config).map((section) => (
              <div
                key={section.id}
                data-testid={`settings-modal-block-${section.id}`}
                style={{ marginBottom: 14 }}
              >
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "#3D4751",
                    marginBottom: 4,
                    paddingBottom: 4,
                    borderBottom: "1px solid #E1E5EA",
                  }}
                >
                  {section.title}
                </div>
                {section.rows.map((row) => (
                  <SettingRowItem key={row.key} row={row} />
                ))}
              </div>
            ))}

            {/*
              Transitional Demo-dataset toggle (apps/_shared/demo-toggle).
              Lives between Backend configuration and the schema_version
              footer so it inherits the modal's narrow column. Designed
              to be ripped out as a single ``git rm`` once the permanent
              demo workflow lands. ``onCorpusRefreshNeeded`` re-fetches
              the document catalog so the pipeline widget reflects the
              new corpus immediately.
            */}
            <DemoToggle
              apiBaseUrl={apiBaseUrl}
              onCorpusRefreshNeeded={
                onCorpusRefreshNeeded ?? (() => undefined)
              }
            />

            <div
              style={{
                marginTop: 16,
                fontSize: 11,
                color: "#9CA4AC",
                fontFamily: "ui-monospace, monospace",
              }}
            >
              schema_version: {state.config.schema_version} · API: {apiBaseUrl}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ``SettingsLauncher`` lives in ./SettingsLauncher.tsx so the host
// can import the (tiny) launcher eagerly while loading this modal —
// which pulls the shared DemoToggle, the admin-config fetch path,
// and a non-trivial render tree — only on first open. Re-exported
// here for a one-line backwards-compatible import path.
export { SettingsLauncher } from "./SettingsLauncher";
