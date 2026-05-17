/**
 * IconRail — 48px-wide left rail with icon-only navigation tiles.
 *
 * Pure presentation; click handling lives in the parent. Active tile gets
 * the cyan tint specified in the design handoff §1.1 (`--dx-cyan-soft`
 * background, `--dx-cyan` foreground). The bottom-anchored "Settings"
 * tile is rendered separately so a flex spacer can push it down.
 *
 * The set of tiles intentionally lags the top-bar nav: the rail is for
 * activity-style verbs (Activity / Upload / Review / Search / Document
 * / Settings). Top-bar nav is for routes (Review / Search / Chat /
 * Admin) per §2.2. There is no Graph tile because Knowledge Forge has
 * no corpus-wide graph surface — graph lives as a per-document tab in
 * the Review Workspace; corpus-wide exploration is the scope of the
 * Knowledge Explorer app (`apps/explorer`).
 */
import {
  Activity,
  CircleHelp,
  Compass,
  FileText,
  Settings,
  Upload,
} from "lucide-react";
import type { ReactElement, ReactNode } from "react";

import { OrbI } from "../atoms/icons";

export type RailTileId =
  | "activity"
  | "upload"
  | "review"
  | "search"
  | "info"
  | "explore"
  | "settings";

interface RailTile {
  id: RailTileId;
  label: string;
  icon: ReactNode;
}

const SZ = 16 as const;
const SW = 1.4 as const;

const TOP_TILES: RailTile[] = [
  { id: "activity", label: "Activity",     icon: <Activity   size={SZ} strokeWidth={SW} /> },
  { id: "upload",   label: "Upload",       icon: <Upload     size={SZ} strokeWidth={SW} /> },
];

const MID_TILES: RailTile[] = [
  { id: "review",   label: "Review",       icon: <FileText   size={SZ} strokeWidth={SW} /> },
  { id: "search",   label: "Search",       icon: OrbI.search },
  { id: "explore",  label: "Knowledge Explorer", icon: <Compass size={SZ} strokeWidth={SW} /> },
  { id: "info",     label: "Document",     icon: <CircleHelp size={SZ} strokeWidth={SW} /> },
];

const BOTTOM_TILE: RailTile = {
  id: "settings",
  label: "Settings",
  icon: <Settings size={SZ} strokeWidth={SW} />,
};

export interface IconRailProps {
  /** Currently-active tile id. Defaults to "review". */
  active?: RailTileId;
  /** Optional click handler. Inert when omitted (PR 1 default). */
  onSelect?: (id: RailTileId) => void;
}

export function IconRail({
  active = "review",
  onSelect,
}: IconRailProps): ReactElement {
  const renderTile = (t: RailTile) => (
    <button
      key={t.id}
      type="button"
      className={`dx-rail-btn ${active === t.id ? "is-active" : ""}`}
      title={t.label}
      aria-label={t.label}
      aria-current={active === t.id ? "page" : undefined}
      onClick={onSelect ? () => onSelect(t.id) : undefined}
    >
      {t.icon}
    </button>
  );

  return (
    <nav className="dx-rail" aria-label="Primary navigation">
      {TOP_TILES.map(renderTile)}
      <div className="dx-rail-divider" aria-hidden="true" />
      {MID_TILES.map(renderTile)}
      <div className="dx-rail-spacer" />
      {renderTile(BOTTOM_TILE)}
    </nav>
  );
}
