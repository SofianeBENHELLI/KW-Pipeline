import type { ReactNode } from "react";

import { Btn, Chip, Icon, useOrbTheme } from "../ui/orb";

import "./rwA.css";

interface ShellBanner {
  id: string;
  tone: "info" | "warn" | "err";
  body: ReactNode;
  dismiss?: () => void;
}

export type OrbNavItem = "review" | "graph" | "search" | "chat" | "admin";

export interface OrbShellProps {
  rail: ReactNode;
  children: ReactNode;
  banners?: ShellBanner[];
  /** Active top-nav tab. */
  activeNav?: OrbNavItem;
  onNav?: (next: OrbNavItem) => void;
  /** Initials displayed in the upper-right avatar (e.g. "SB"). */
  avatar?: string;
  /** Optional build/version string shown as a status chip in the top-right. */
  buildVersion?: string;
  /** When true, the rail sits on the right edge (mockup `rail-right`). */
  railRight?: boolean;
}

const NAV_ITEMS: { id: OrbNavItem; label: string; icon: Parameters<typeof Icon>[0]["name"] }[] = [
  { id: "review", label: "Review", icon: "doc" },
  { id: "graph", label: "Graph", icon: "graph" },
  { id: "search", label: "Search", icon: "spark" },
  { id: "chat", label: "Chat", icon: "chat" },
  { id: "admin", label: "Admin", icon: "shield" },
];

/**
 * Variant-A shell — exact port of the mockup top bar (brand mark + nav
 * tabs + version chip + cog + avatar). The body is a two-column grid
 * (rail + main canvas) that the catalog and review surfaces fill.
 */
export function OrbShell({
  rail,
  children,
  banners = [],
  activeNav = "review",
  onNav,
  avatar = "SB",
  buildVersion,
  railRight = false,
}: OrbShellProps) {
  const { toggleTheme, theme } = useOrbTheme();
  return (
    <div className={`orb-app rwA ${railRight ? "rail-right" : ""}`.trim()}>
      {banners.length > 0 && (
        <div className="orb-shell__banners">
          {banners.map((banner) => (
            <div
              key={banner.id}
              className={`orb-banner orb-banner--${banner.tone}`}
              role={banner.tone === "err" ? "alert" : "status"}
            >
              <span className="orb-banner__body">{banner.body}</span>
              {banner.dismiss && (
                <button
                  type="button"
                  className="orb-btn orb-btn--ghost orb-btn--xs"
                  onClick={banner.dismiss}
                >
                  Dismiss
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="rwA-topbar">
        <span className="rwA-brand">
          <span className="rwA-mark" aria-hidden="true" />
          <span className="rwA-brandname">orbital</span>
          <span className="rwA-brandtag orb-mono">reviewer · kw-pipeline</span>
        </span>
        <nav className="rwA-nav" aria-label="Primary">
          {NAV_ITEMS.map((item) => {
            const active = item.id === activeNav;
            return (
              <button
                key={item.id}
                type="button"
                className={`rwA-navbtn ${active ? "is-active" : ""}`.trim()}
                aria-current={active ? "page" : undefined}
                onClick={() => onNav?.(item.id)}
              >
                <Icon name={item.icon} />
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="rwA-topright">
          {buildVersion && (
            <Chip dot color="var(--orb-ok)">
              <span className="orb-mono">{buildVersion}</span>
            </Chip>
          )}
          <Btn
            kind="ghost"
            size="sm"
            iconOnly
            icon={<Icon name="spark" />}
            onClick={toggleTheme}
            title={`Theme: ${theme}`}
            aria-label="Toggle theme"
          />
          <Btn
            kind="ghost"
            size="sm"
            iconOnly
            icon={<Icon name="cog" />}
            title="Settings"
            aria-label="Open settings"
          />
          <span className="rwA-avatar" aria-label={`Signed in as ${avatar}`}>
            {avatar}
          </span>
        </div>
      </div>

      <div className="rwA-fab">
        <aside className="rwA-rail">{rail}</aside>
        <main className="rwA-main orb-scroll">{children}</main>
      </div>
    </div>
  );
}
