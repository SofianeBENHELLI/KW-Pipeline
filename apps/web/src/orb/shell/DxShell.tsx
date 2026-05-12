/**
 * DxShell — outermost wrapper for every Knowledge Forge surface.
 *
 * Owns the 44px top bar + 48px icon rail chrome and exposes a slot for
 * the workspace content. Also owns the `.orb-app` class + `data-theme`
 * + `data-density` attributes that scope all Knowledge Forge tokens
 * away from the legacy `apps/web/src/styles/tokens.css`.
 *
 * Light theme + compact density are PR-1 defaults; the Settings modal
 * (PR 8) flips them at runtime.
 */
import type { ReactElement, ReactNode } from "react";

import { IconRail, type RailTileId } from "./IconRail";
import { TopBar, type TopBarProps, type TopNavTab } from "./TopBar";

export type OrbTheme = "light" | "dark";
export type OrbDensity = "compact" | "cozy" | "dense";

export interface DxShellProps {
  /** Theme to scope under `data-theme=…`. Default `"light"`. */
  theme?: OrbTheme;
  /** Density to scope under `data-density=…`. Default `"compact"`. */
  density?: OrbDensity;
  /** Currently-active top nav tab. */
  activeTab?: TopNavTab;
  /** Currently-active rail tile. */
  activeRail?: RailTileId;
  /** Top-bar slot props (brand name, crumb, status, etc.). */
  topBar?: Omit<TopBarProps, "activeTab">;
  /** Rail click handler. Inert in PR 1. */
  onRailSelect?: (id: RailTileId) => void;
  /** Top-tab click handler. Inert in PR 1. */
  onTabSelect?: (tab: TopNavTab) => void;
  /** Whether to render the icon rail. Defaults to `true`. */
  showRail?: boolean;
  /** Workspace content. */
  children: ReactNode;
}

export function DxShell({
  theme = "light",
  density = "compact",
  activeTab,
  activeRail = "review",
  topBar,
  onRailSelect,
  onTabSelect,
  showRail = true,
  children,
}: DxShellProps): ReactElement {
  return (
    <div className="orb-app" data-theme={theme} data-density={density}>
      <div className="dx-shell">
        <TopBar {...topBar} activeTab={activeTab} onTabSelect={onTabSelect} />
        <div className="dx-body">
          {showRail && <IconRail active={activeRail} onSelect={onRailSelect} />}
          <main className="dx-content" role="main">
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
