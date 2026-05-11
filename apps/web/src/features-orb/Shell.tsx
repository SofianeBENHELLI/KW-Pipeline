import type { ReactNode } from "react";

import { Btn, Icon, ThemeToggle } from "../ui/orb";

import "./shell.css";

interface ShellBanner {
  id: string;
  tone: "info" | "warn" | "err";
  body: ReactNode;
  dismiss?: () => void;
}

export type ShellAside = "search" | "chat" | null;

export interface OrbShellProps {
  rail: ReactNode;
  children: ReactNode;
  banners?: ShellBanner[];
  /** Currently-open right-edge slide-out panel, if any. */
  aside?: ShellAside;
  onAsideChange?: (next: ShellAside) => void;
  /** Slot for the slide-out panel content (one of <SearchPanel/> | <ChatPanel/>). */
  asideContent?: ReactNode;
}

/**
 * Phase-1+ shell. Fixed-position grid: banner stack → topbar →
 * rail+canvas split. Phase 5 added the right-edge `aside` slot toggled
 * by the topbar's search and chat icons.
 */
export function OrbShell({ rail, children, banners = [], aside = null, onAsideChange, asideContent }: OrbShellProps) {
  const toggleAside = (next: ShellAside) => {
    onAsideChange?.(aside === next ? null : next);
  };
  return (
    <div className={`orb-app orb-shell ${aside ? "orb-shell--with-aside" : ""}`.trim()}>
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
        {onAsideChange && (
          <>
            <Btn
              kind={aside === "search" ? "primary" : "ghost"}
              size="sm"
              iconOnly
              icon={<Icon name="search" />}
              onClick={() => toggleAside("search")}
              aria-label={aside === "search" ? "Close search panel" : "Open vector search"}
              title="Vector search"
            />
            <Btn
              kind={aside === "chat" ? "primary" : "ghost"}
              size="sm"
              iconOnly
              icon={<Icon name="chat" />}
              onClick={() => toggleAside("chat")}
              aria-label={aside === "chat" ? "Close chat panel" : "Open grounded chat"}
              title="Grounded chat"
            />
          </>
        )}
        <ThemeToggle />
      </div>

      <div className="orb-shell__main">
        <aside className="orb-shell__rail orb-scroll">{rail}</aside>
        <main className="orb-shell__canvas orb-scroll">{children}</main>
        {aside && asideContent && (
          <aside className="orb-shell__aside orb-scroll" aria-label={aside === "search" ? "Vector search" : "Chat"}>
            {asideContent}
          </aside>
        )}
      </div>
    </div>
  );
}
