/**
 * Widget header (brand bar) — replaces the previous title + cog
 * combo with the design-handoff treatment: logo placeholder + product
 * name + breadcrumb stub on the left, live status pill + refresh /
 * cog / overflow on the right.
 *
 * The placeholder square ("mark") stands in for the official 3DS
 * compass SVG — swap when the brand asset lands in `apps/widget/src/
 * assets/`. The breadcrumb is a static stub for v1; real workspace
 * wiring lands with #91 (workspace scoping) and #83 (auth).
 */

import React from "react";

import { Icon } from "./icons";

interface HealthSnapshot {
  ok: boolean;
  word: string;
  version?: string;
}

interface Props {
  /** Current health state — drives the live pill. */
  health: HealthSnapshot;
  settingsOpen: boolean;
  orbitalUrl: string;
  onToggleSettings: () => void;
  onRefresh: () => void;
}

export const Header: React.FC<Props> = ({
  health,
  settingsOpen,
  orbitalUrl,
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
        <span className="kw-hdr__product">3DX KnowledgeForge</span>
        <span className="kw-hdr__crumb-sep" aria-hidden="true">
          ›
        </span>
        <span className="kw-hdr__crumb">workspace · alpha</span>
      </div>
      <div className="kw-hdr__actions">
        <span className={pillClass}>{pillText}</span>
        <a
          className="kw-iconbtn"
          href={orbitalUrl}
          target="_blank"
          rel="noreferrer"
          aria-label="Open Orbital review workspace"
          title="Open Orbital"
        >
          <Icon name="external-link" />
        </a>
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
