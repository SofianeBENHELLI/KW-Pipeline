/**
 * Knowledge Explorer header (brand bar). Mirrors apps/widget/src/components/
 * Header.tsx visually so both tiles read as siblings on the dashboard,
 * but swaps the product name + breadcrumb stub for the Explorer's own copy.
 */

import React from "react";

import { Icon } from "./icons";

interface HealthSnapshot {
  ok: boolean;
  word: string;
  version?: string;
}

interface Props {
  health: HealthSnapshot;
  settingsOpen: boolean;
  onToggleSettings: () => void;
  onRefresh: () => void;
}

export const Header: React.FC<Props> = ({
  health,
  settingsOpen,
  onToggleSettings,
  onRefresh,
}) => {
  const pillClass = health.ok ? "kw-pill kw-pill--live" : "kw-pill kw-pill--down";
  const pillText = health.ok
    ? `${health.version ?? "unknown"} · ${health.word}`
    : `unreachable · ${health.word}`;
  return (
    <header className="kw-hdr">
      <div className="kw-hdr__brand">
        <span className="kw-hdr__mark" aria-hidden="true" />
        <span className="kw-hdr__product">3DX Knowledge Explorer</span>
        <span className="kw-hdr__crumb-sep" aria-hidden="true">
          ›
        </span>
        <span className="kw-hdr__crumb">explore · alpha</span>
      </div>
      <div className="kw-hdr__actions">
        <span className={pillClass}>{pillText}</span>
        <button
          type="button"
          className="kw-iconbtn"
          onClick={onRefresh}
          aria-label="Refresh"
          title="Refresh"
        >
          <Icon name="refresh" />
        </button>
        <button
          type="button"
          className={settingsOpen ? "kw-iconbtn kw-iconbtn--active" : "kw-iconbtn"}
          onClick={onToggleSettings}
          aria-label="Settings"
          aria-expanded={settingsOpen}
          title="Settings"
        >
          <Icon name="cog" />
        </button>
        <button
          type="button"
          className="kw-iconbtn"
          aria-label="More options"
          title="More"
        >
          <Icon name="more" />
        </button>
      </div>
    </header>
  );
};
