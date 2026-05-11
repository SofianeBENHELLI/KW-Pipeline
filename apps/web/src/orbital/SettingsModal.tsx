import { useState } from "react";

import type { AdminConfigResponse } from "../../../_shared/settings-hub";
import { getApiBaseUrl } from "../api/client";
import { useAdminConfig } from "../api/useAdminConfig";

import { Icon } from "./atoms";

export interface SettingsModalProps {
  open: boolean;
  onClose: () => void;
}

const PANELS = ["General", "Health", "Phase-3", "Audit", "Demo", "Account", "Shortcuts"] as const;
type Panel = (typeof PANELS)[number];

export function SettingsModal({ open, onClose }: SettingsModalProps) {
  const [panel, setPanel] = useState<Panel>("Health");
  const baseUrl = getApiBaseUrl();
  const admin = useAdminConfig(baseUrl);

  if (!open) return null;
  return (
    <div className="orb-app pd-shell pd-shell--settings" style={{ position: "fixed", inset: 0, zIndex: 100 }}>
      <div className="pd-bg" onClick={onClose}></div>
      <div className="pd-modal pd-modal--settings">
        <header className="pd-h">
          <Icon name="cog" />
          <span style={{ fontWeight: 600 }}>Settings</span>
          <span className="orb-mono" style={{ fontSize: 10, color: "var(--orb-fg-dim)" }}>
            schema {admin.config?.schema_version ?? "?"} · {admin.status}
          </span>
          <span style={{ flex: 1 }}></span>
          <button className="sp-x" onClick={onClose} aria-label="Close settings">
            <Icon name="x" />
          </button>
        </header>
        <div className="pd-body" style={{ padding: 0 }}>
          <div className="set-cols">
            <nav className="set-nav">
              {PANELS.map((p) => (
                <button key={p} className={panel === p ? "is-on" : ""} onClick={() => setPanel(p)}>
                  {p}
                </button>
              ))}
            </nav>
            <div className="set-main orb-scroll">
              {panel === "Health" && <HealthPanel config={admin.config} status={admin.status} />}
              {panel === "Phase-3" && <PhasePanel config={admin.config} />}
              {panel === "Demo" && (
                <>
                  <h3>Presenter tools</h3>
                  <p className="set-sub">Load fixtures or reset the corpus. Both require admin auth on the backend.</p>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button className="orb-btn">
                      <Icon name="bolt" /> Load demo dataset
                    </button>
                    <button className="orb-btn orb-btn--danger">
                      <Icon name="refresh" /> Reset corpus
                    </button>
                  </div>
                </>
              )}
              {!(["Health", "Phase-3", "Demo"] as readonly Panel[]).includes(panel) && (
                <>
                  <h3>{panel}</h3>
                  <p className="set-sub">Pane stub — port from the mockup as needed.</p>
                </>
              )}

              <h3 style={{ marginTop: 20 }}>API base URL</h3>
              <div className="set-readonly orb-mono">
                {baseUrl}{" "}
                <span style={{ color: "var(--orb-fg-dim)" }}>· read-only · baked via VITE_API_BASE_URL</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function HealthPanel({ config, status }: { config: AdminConfigResponse | null; status: string }) {
  const tiles = config
    ? [
        { l: "Backend",        v: config.schema_version,                 s: "ok",                                                  d: `schema · status ${status}` },
        { l: "Neo4j",          v: config.knowledge_layer.neo4j_database, s: config.knowledge_layer.neo4j_configured ? "ok" : "off", d: config.knowledge_layer.enabled ? "knowledge layer enabled" : "knowledge layer disabled" },
        { l: "LLM",            v: config.llm.model || "—",               s: config.llm.configured ? "ok" : "off",                  d: `provider ${config.llm.provider_setting}` },
        { l: "Embeddings",     v: config.embeddings.model || "—",        s: config.embeddings.configured ? "ok" : "off",           d: config.embeddings.configured ? "active" : "VOYAGE_API_KEY unset" },
        { l: "HITL force-auto",v: config.hitl.force_auto_corpus ? "ON" : "off", s: config.hitl.force_auto_corpus ? "warn" : "off", d: "ADR-023 §6 override" },
        { l: "Audit",          v: config.audit.enabled ? "on" : "off",   s: config.audit.enabled ? "ok" : "off",                   d: config.audit.enabled ? "events stored" : "KW_AUDIT_ENABLED=false" },
        { l: "Persistence",    v: config.persistence.persistent ? "sqlite" : "memory", s: "ok",                                  d: config.persistence.persistent ? config.persistence.data_dir : "in-memory" },
        { l: "Logging",        v: `${config.logging.level}/${config.logging.format}`, s: "ok",                                   d: "" },
      ]
    : [];
  return (
    <>
      <h3>Backend health</h3>
      <p className="set-sub">Snapshot from <code className="orb-mono">GET /admin/config</code> at boot.</p>
      {tiles.length === 0 && (
        <div className="set-readonly">No config loaded — backend may be unreachable or /admin/config is 403.</div>
      )}
      <div className="set-tiles">
        {tiles.map((t) => (
          <div key={t.l} className={`set-tile set-tile--${t.s}`}>
            <div className="set-tile-t">{t.l}</div>
            <div className="set-tile-v">{t.v}</div>
            <div className="set-tile-d">{t.d}</div>
          </div>
        ))}
      </div>
    </>
  );
}

function PhasePanel({ config }: { config: AdminConfigResponse | null }) {
  if (!config) {
    return (
      <>
        <h3>Phase-3 features</h3>
        <div className="set-readonly">No config loaded.</div>
      </>
    );
  }
  const flags = [
    { ok: config.knowledge_layer.enabled,      label: "Knowledge layer",   env: "KW_KNOWLEDGE_LAYER_ENABLED" },
    { ok: config.embeddings.configured,        label: "Vector search",     env: "VOYAGE_API_KEY" },
    { ok: config.llm.configured,               label: "Chat (LLM)",        env: "ANTHROPIC_API_KEY / GEMINI_API_KEY" },
    { ok: config.hitl.force_auto_corpus,       label: "Force-auto corpus", env: "KW_HITL_FORCE_AUTO_CORPUS", warn: true },
  ];
  return (
    <>
      <h3>Phase-3 features</h3>
      <div className="set-feat">
        {flags.map((f) => (
          <div key={f.env}>
            <span style={{ color: f.ok ? (f.warn ? "var(--orb-warn)" : "var(--orb-ok)") : "var(--orb-fg-dim)" }}>●</span>
            {f.label}
            <span className="orb-mono">{f.env}</span>
          </div>
        ))}
      </div>
    </>
  );
}
