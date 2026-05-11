import type { ReactNode } from "react";

import { ThemeToggle } from "../ui/orb";

import "./shell.css";

interface ShellBanner {
  id: string;
  tone: "info" | "warn" | "err";
  body: ReactNode;
  dismiss?: () => void;
}

export interface OrbShellProps {
  rail: ReactNode;
  children: ReactNode;
  banners?: ShellBanner[];
}

/**
 * Phase-1 shell. A fixed-position 3-row grid (banner stack → topbar →
 * rail+canvas split). Everything below this component is rendered inside
 * the `.orb-app` scope so tokens.css selectors apply. Banner slots,
 * topbar, and the left rail are explicit props so the Phase-2 review
 * workspace and Phase-4 graph view can mount the same shell with their
 * own rail content without forking the layout.
 */
export function OrbShell({ rail, children, banners = [] }: OrbShellProps) {
  return (
    <div className="orb-app orb-shell">
      <div className="orb-shell__banners">
        {banners.map((banner) => (
          <div key={banner.id} className={`orb-banner orb-banner--${banner.tone}`} role={banner.tone === "err" ? "alert" : "status"}>
            <span className="orb-banner__body">{banner.body}</span>
            {banner.dismiss && (
              <button
                type="button"
                className="orb-btn orb-btn--ghost orb-btn--xs orb-banner__dismiss"
                onClick={banner.dismiss}
              >
                Dismiss
              </button>
            )}
          </div>
        ))}
      </div>

      <div className="orb-shell__topbar">
        <span className="orb-shell__brand">
          <span className="orb-shell__brand-mark">O</span>
          <span className="orb-shell__brand-name">Orbital</span>
          <span className="orb-shell__brand-tagline orb-mono">reviewer workbench</span>
        </span>
        <span className="orb-shell__topbar-spacer" />
        <ThemeToggle />
      </div>

      <div className="orb-shell__main">
        <aside className="orb-shell__rail orb-scroll">{rail}</aside>
        <main className="orb-shell__canvas orb-scroll">{children}</main>
      </div>
    </div>
  );
}
