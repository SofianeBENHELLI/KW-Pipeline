/**
 * TopBar — 44px chrome row at the top of the Knowledge Forge shell.
 *
 * Layout (left → right):
 *   [brand-logo] [brand-name "Knowledge Forge"] [crumb] | [nav tabs] | [status pill] [icon buttons] [avatar]
 *
 * The brand name is mandated by the user: internal codename is "Orbital"
 * but the user-visible product is **"Knowledge Forge"** — every shell
 * surface must show it. The crumb (e.g. "kw-pipeline · alpha") is pure
 * trailing metadata and may be omitted.
 *
 * Top-bar nav tabs (Review / Graph / Search / Chat / Admin) match the
 * route family at `/kf/{review,graph,search,chat,admin}`. In PR 1 the
 * tabs are inert links — PR 2+ wires them through `<NavLink>`.
 */
import type { ReactElement, ReactNode } from "react";

import { OrbI } from "../atoms/icons";

export type TopNavTab = "review" | "graph" | "search" | "chat" | "admin";

interface NavTabDef {
  id: TopNavTab;
  label: string;
  icon: ReactNode;
}

const NAV_TABS: NavTabDef[] = [
  { id: "review", label: "Review", icon: OrbI.doc },
  { id: "graph",  label: "Graph",  icon: OrbI.graph },
  { id: "search", label: "Search", icon: OrbI.search },
  { id: "chat",   label: "Chat",   icon: OrbI.chat },
  { id: "admin",  label: "Admin",  icon: OrbI.shield },
];

export interface TopBarProps {
  /** User-visible product name. Defaults to "Knowledge Forge". */
  product?: string;
  /** Optional metadata crumb after the product name. */
  crumb?: string;
  /** Pill text on the right side of the chrome. */
  status?: string;
  /** Currently-active top nav tab. */
  activeTab?: TopNavTab;
  /** Optional click handler for nav tabs. Inert when omitted. */
  onTabSelect?: (tab: TopNavTab) => void;
  /** User initials for the avatar. Falls back to "—". */
  initials?: string;
  /** Optional click handler for the settings cog. */
  onOpenSettings?: () => void;
}

export function TopBar({
  product = "Knowledge Forge",
  crumb,
  status = "online",
  activeTab,
  onTabSelect,
  initials = "—",
  onOpenSettings,
}: TopBarProps): ReactElement {
  return (
    <header className="dx-topbar" role="banner">
      <div className="dx-brand">
        <span className="dx-brand-logo" aria-hidden="true">
          {/* Stylized compass mark — abstract, not the actual 3DX glyph */}
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
            <path d="M8 1.5L13.5 6 11 14 5 14 2.5 6z" />
            <circle cx="8" cy="7.5" r="1.6" fill="currentColor" stroke="none" />
          </svg>
        </span>
        <span className="dx-brand-name">{product}</span>
        {crumb && (
          <>
            <span className="dx-brand-sep" aria-hidden="true" />
            <span className="dx-brand-crumb orb-mono">{crumb}</span>
          </>
        )}
      </div>

      <nav className="dx-nav" aria-label="Workspace sections">
        {NAV_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`dx-navbtn ${activeTab === t.id ? "is-active" : ""}`}
            aria-current={activeTab === t.id ? "page" : undefined}
            onClick={onTabSelect ? () => onTabSelect(t.id) : undefined}
          >
            <span aria-hidden="true">{t.icon}</span>
            {t.label}
          </button>
        ))}
      </nav>

      <div className="dx-topbar-right">
        <span className="dx-status-pill" role="status" aria-label={`Status: ${status}`}>
          <span className="dot" aria-hidden="true" />
          {status}
        </span>
        <button
          type="button"
          className="dx-icon-btn"
          title="Settings"
          aria-label="Open settings"
          onClick={onOpenSettings}
        >
          {OrbI.cog}
        </button>
        <div className="dx-avatar" aria-label={`Signed in as ${initials}`}>
          {initials}
        </div>
      </div>
    </header>
  );
}
