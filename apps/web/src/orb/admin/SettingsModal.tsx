/**
 * SettingsModal — Knowledge Forge settings surface.
 *
 * Per design §10: tabbed (Account / Pipeline / Phase 3 /
 * Diagnostics). PR 8 ships the chrome — read-only views of each tab —
 * so a follow-up can swap in real editors without changing the modal
 * surface.
 */

import { useState } from "react";
import type { ReactElement } from "react";

import { Btn, Kbd, MetaRow, OrbI, SectionH } from "../index";
import "./admin.css";

export type SettingsTab = "account" | "pipeline" | "phase3" | "diag";

export interface SettingsModalProps {
  open: boolean;
  onClose: () => void;
  /** Initial tab. Defaults to "account". */
  initialTab?: SettingsTab;
  /** Optional pre-loaded admin config snapshot for the Pipeline + Phase-3 tabs. */
  config?: SettingsConfigSummary | null;
}

export interface SettingsConfigSummary {
  pipelineName?: string;
  autoValidateThreshold?: number;
  forceAutoCorpus?: boolean;
  voyageKeyConfigured?: boolean;
  llmProvider?: string;
  llmModel?: string;
  knowledgeLayerEnabled?: boolean;
}

const TABS: Array<{ id: SettingsTab; label: string }> = [
  { id: "account", label: "Account" },
  { id: "pipeline", label: "Pipeline" },
  { id: "phase3", label: "Phase 3" },
  { id: "diag", label: "Diagnostics" },
];

export function SettingsModal({
  open,
  onClose,
  initialTab = "account",
  config = null,
}: SettingsModalProps): ReactElement | null {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  if (!open) return null;
  return (
    <div
      className="kf-modal-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div
        className="kf-modal kf-modal--wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="kf-settings-title"
        data-testid="kf-settings-modal"
      >
        <header className="kf-modal__head">
          <h2 id="kf-settings-title" className="kf-modal__title">
            Settings
          </h2>
          <button
            type="button"
            className="kf-modal__close"
            aria-label="Close"
            onClick={onClose}
          >
            {OrbI.x}
          </button>
        </header>
        <div className="kf-settings__body">
          <nav className="kf-settings__nav" aria-label="Settings tabs">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`kf-settings__nav-item ${tab === t.id ? "is-on" : ""}`}
                onClick={() => setTab(t.id)}
                aria-current={tab === t.id ? "page" : undefined}
              >
                {t.label}
              </button>
            ))}
          </nav>
          <div className="kf-settings__pane">
            {tab === "account" && <AccountTab />}
            {tab === "pipeline" && <PipelineTab config={config} />}
            {tab === "phase3" && <Phase3Tab config={config} />}
            {tab === "diag" && <DiagTab />}
          </div>
        </div>
        <footer className="kf-modal__foot">
          <Btn kind="ghost" onClick={onClose}>
            Close
          </Btn>
        </footer>
      </div>
    </div>
  );
}

function AccountTab(): ReactElement {
  return (
    <>
      <SectionH>Identity</SectionH>
      <p className="kf-settings__note">
        Your name and email come from the auth provider. To change them
        update your provider profile and re-sign in.
      </p>
      <SectionH>Theme + density</SectionH>
      <p className="kf-settings__note">
        Light, dark, and system theme; compact / cozy / dense density —
        editable in a follow-up. The Knowledge Forge chrome reads{" "}
        <code className="orb-mono">[data-theme]</code> +{" "}
        <code className="orb-mono">[data-density]</code> on the root
        already.
      </p>
      <SectionH>Reduced motion</SectionH>
      <p className="kf-settings__note">
        The shell honours <code className="orb-mono">prefers-reduced-motion</code>{" "}
        out of the box — no toggle required.
      </p>
    </>
  );
}

function PipelineTab({
  config,
}: {
  config: SettingsConfigSummary | null;
}): ReactElement {
  return (
    <>
      <SectionH>Pipeline configuration</SectionH>
      <div className="kf-settings__metas">
        <MetaRow k="Pipeline">{config?.pipelineName ?? "kw-pipeline"}</MetaRow>
        <MetaRow k="Auto-validate ≥">
          {config?.autoValidateThreshold?.toFixed?.(2) ?? "0.85"}
        </MetaRow>
        <MetaRow k="Force-auto corpus">
          {config?.forceAutoCorpus ? "ON" : "off"}
        </MetaRow>
      </div>
      <p className="kf-settings__note">
        Editing these surfaces ships in a follow-up; the values shown
        are pulled from <code className="orb-mono">/admin/config</code>{" "}
        when the modal mounts.
      </p>
    </>
  );
}

function Phase3Tab({
  config,
}: {
  config: SettingsConfigSummary | null;
}): ReactElement {
  return (
    <>
      <SectionH>Phase-3 readiness</SectionH>
      <div className="kf-settings__metas">
        <MetaRow k="Knowledge layer">
          {config?.knowledgeLayerEnabled ? "enabled" : "disabled"}
        </MetaRow>
        <MetaRow k="VOYAGE_API_KEY">
          {config?.voyageKeyConfigured ? "present" : "missing"}
        </MetaRow>
        <MetaRow k="LLM provider">{config?.llmProvider ?? "—"}</MetaRow>
        <MetaRow k="LLM model">{config?.llmModel ?? "—"}</MetaRow>
      </div>
      <p className="kf-settings__note">
        Search + Chat surface a 503 with the operator-facing remediation
        when these gates are off — no need to babysit them from here
        for routine operation.
      </p>
    </>
  );
}

function DiagTab(): ReactElement {
  return (
    <>
      <SectionH>Diagnostics</SectionH>
      <p className="kf-settings__note">
        Backend health pings, build hash, projection latency p50/p95
        ship in a follow-up. The legacy Settings modal under{" "}
        <code className="orb-mono">/?settings=open</code> already
        exposes these — use it for now.
      </p>
      <p className="kf-settings__note">
        Keyboard reference:
      </p>
      <p>
        <Kbd>/</Kbd>
        <span style={{ marginLeft: 6 }}>focus rail search</span>
        <span style={{ marginLeft: 12 }} />
        <Kbd>v</Kbd>
        <span style={{ marginLeft: 6 }}>validate</span>
        <span style={{ marginLeft: 12 }} />
        <Kbd>r</Kbd>
        <span style={{ marginLeft: 6 }}>reject</span>
      </p>
    </>
  );
}
