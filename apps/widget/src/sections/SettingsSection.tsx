/**
 * Knowledge Forge Settings — widget surface.
 *
 * Renders three blocks on top of the shared ``settings-hub`` data
 * layer (``apps/_shared/settings-hub``):
 *
 *   1. Pipeline status tiles (Phase 1/2/3, NER, audit, ITEROP).
 *   2. Backend configuration table — every Settings field, with a
 *      hover ``i`` tooltip per row (English help copy from the shared
 *      package), greyed out when the feature is inactive.
 *   3. Per-tile preferences (currently the API base URL editor).
 *
 * Everything visible-but-greyed comes straight from the user's
 * "show all settings, grey out the ones that are not active"
 * directive — there's no hidden field anywhere in the response.
 */

import React, { useEffect, useState } from "react";

import {
  ApiError,
  buildDiagnosticTiles,
  buildSettingsSections,
  fetchAdminConfig,
  type AdminConfigResponse,
  type DiagnosticTile,
  type SettingRow,
} from "../../../_shared/settings-hub";
import { Icon } from "../components/icons";
import { SectionHeader } from "../components/SectionHeader";

interface Props {
  apiBaseUrl: string;
  refreshTick: number;
  onApiBaseUrlChange: (next: string) => void;
}

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

function InfoTip({ text }: { text: string }): React.ReactElement | null {
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

function TileCard({ tile }: { tile: DiagnosticTile }): React.ReactElement {
  return (
    <div
      data-testid={`settings-tile-${tile.id}`}
      data-state={tile.state}
      style={{
        flex: "1 1 0",
        minWidth: 100,
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
        <span style={{ fontSize: 11, fontWeight: 600, color: "#2A3138" }}>
          {tile.label}
        </span>
      </div>
      <div
        style={{
          marginTop: 4,
          fontSize: 11,
          color: "#5C6770",
          fontFamily: "var(--ds-mono, ui-monospace, monospace)",
        }}
      >
        {tile.sublabel}
      </div>
    </div>
  );
}

function SettingRowItem({ row }: { row: SettingRow }): React.ReactElement {
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
      <div style={{ fontSize: 12, color: "#2A3138" }}>
        {row.label}
        <InfoTip text={row.help} />
      </div>
      <div
        style={{
          fontSize: 12,
          color: inactive ? "#7A828A" : "#1A1F25",
          fontFamily: "var(--ds-mono, ui-monospace, monospace)",
          fontFeatureSettings: '"tnum"',
          wordBreak: "break-word",
        }}
      >
        {row.value === null || row.value === "" ? "—" : String(row.value)}
      </div>
    </div>
  );
}

const ApiBaseUrlEditor: React.FC<{
  value: string;
  onSave: (next: string) => void;
}> = ({ value, onSave }) => {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const dirty = draft.trim() !== value && draft.trim().length > 0;
  return (
    <div
      data-testid="settings-api-base-url"
      style={{
        display: "flex",
        gap: 8,
        alignItems: "center",
        padding: "6px 0",
      }}
    >
      <label style={{ fontSize: 12, color: "#3D4751", minWidth: 110 }}>
        API base URL
      </label>
      <input
        type="url"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        aria-label="API base URL"
        style={{
          flex: 1,
          padding: "4px 6px",
          fontSize: 12,
          fontFamily: "var(--ds-mono, ui-monospace, monospace)",
          border: "1px solid #C8CDD4",
          borderRadius: 4,
        }}
      />
      <button
        type="button"
        onClick={() => onSave(draft.trim())}
        disabled={!dirty}
        style={{
          padding: "4px 10px",
          fontSize: 12,
          border: "1px solid #C8CDD4",
          borderRadius: 4,
          background: dirty ? "#1E5DAB" : "#F0F2F5",
          color: dirty ? "white" : "#7A828A",
          cursor: dirty ? "pointer" : "not-allowed",
        }}
      >
        Save
      </button>
    </div>
  );
};

export const SettingsSection: React.FC<Props> = ({
  apiBaseUrl,
  refreshTick,
  onApiBaseUrlChange,
}) => {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
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
  }, [apiBaseUrl, refreshTick]);

  return (
    <section
      className="kw-section"
      aria-label="Knowledge Forge settings"
      data-testid="settings-section"
    >
      <SectionHeader icon="cog" title="Knowledge Forge — Settings" />

      {state.kind === "loading" && (
        <div className="kw-status" data-testid="settings-section-loading">
          Loading configuration…
        </div>
      )}

      {state.kind === "err" && (
        <div
          className="kw-error"
          role="alert"
          data-testid="settings-section-error"
        >
          {state.message}
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
            data-testid="settings-tiles"
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
              data-testid={`settings-section-block-${section.id}`}
              style={{ marginBottom: 14 }}
            >
              <div
                style={{
                  fontSize: 11,
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

          <h3
            style={{
              margin: "12px 0 8px 0",
              fontSize: 11,
              fontWeight: 600,
              color: "#5C6770",
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >
            Your preferences (this widget tile only)
          </h3>
          <ApiBaseUrlEditor value={apiBaseUrl} onSave={onApiBaseUrlChange} />

          <div
            style={{
              marginTop: 16,
              fontSize: 10,
              color: "#9CA4AC",
              fontFamily: "var(--ds-mono, ui-monospace, monospace)",
            }}
          >
            <Icon name="info" size={10} /> schema_version:{" "}
            {state.config.schema_version} · API: {apiBaseUrl}
          </div>
        </>
      )}
    </section>
  );
};
