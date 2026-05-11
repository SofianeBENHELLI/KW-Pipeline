import type { ReactNode } from "react";

import { Icon } from "./atoms";
import { useOrbTheme } from "./useTheme";

export type OrbNavId = "review" | "graph" | "search" | "chat" | "admin";

const NAV_ITEMS: { id: OrbNavId; label: string; icon: Parameters<typeof Icon>[0]["name"] }[] = [
  { id: "review", label: "Review", icon: "doc" },
  { id: "graph", label: "Graph", icon: "graph" },
  { id: "search", label: "Search", icon: "spark" },
  { id: "chat", label: "Chat", icon: "chat" },
  { id: "admin", label: "Admin", icon: "shield" },
];

export interface TopBarProps {
  activeNav: OrbNavId;
  onNav: (next: OrbNavId) => void;
  onOpenSettings: () => void;
  onClickBrand: () => void;
  buildVersion?: string;
  avatar?: string;
  rightExtras?: ReactNode;
}

/**
 * The 44px top bar from the mockup (`rwA-topbar`). Hoisted out of the
 * workspace so it can sit above every Orbital screen (catalog, graph,
 * search, chat, admin), giving navigation a single coherent surface.
 */
export function TopBar({
  activeNav,
  onNav,
  onOpenSettings,
  onClickBrand,
  buildVersion = "v0.1.0-preview.5",
  avatar = "SB",
  rightExtras,
}: TopBarProps) {
  const { theme, toggle } = useOrbTheme();
  return (
    <div className="rwA-topbar">
      <div className="rwA-brand">
        <span className="rwA-mark"></span>
        <button
          type="button"
          className="rwA-brandname"
          onClick={onClickBrand}
          style={{
            background: "transparent",
            border: 0,
            padding: 0,
            cursor: "pointer",
            color: "inherit",
            font: "inherit",
            fontWeight: 700,
            fontSize: 14,
            letterSpacing: "-0.02em",
          }}
        >
          orbital
        </button>
        <span className="rwA-brandtag orb-mono">reviewer · kw-pipeline</span>
      </div>
      <nav className="rwA-nav" aria-label="Primary">
        {NAV_ITEMS.map((item) => {
          const active = item.id === activeNav;
          return (
            <button
              key={item.id}
              type="button"
              className={`rwA-navbtn ${active ? "is-active" : ""}`}
              aria-current={active ? "page" : undefined}
              onClick={() => onNav(item.id)}
            >
              <Icon name={item.icon} /> {item.label}
            </button>
          );
        })}
      </nav>
      <div className="rwA-topright">
        {rightExtras}
        <span className="orb-chip orb-mono">
          <span className="dot" style={{ background: "var(--orb-ok)" }}></span>
          {buildVersion}
        </span>
        <button
          className="orb-btn orb-btn--ghost orb-btn--icon"
          title={`Theme: ${theme} — click to toggle`}
          onClick={toggle}
          aria-label="Toggle theme"
        >
          <Icon name="spark" />
        </button>
        <button
          className="orb-btn orb-btn--ghost orb-btn--icon"
          title="Settings"
          onClick={onOpenSettings}
          aria-label="Open settings"
        >
          <Icon name="cog" />
        </button>
        <span className="rwA-avatar" aria-label={`Signed in as ${avatar}`}>
          {avatar}
        </span>
      </div>
    </div>
  );
}
