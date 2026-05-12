/**
 * Knowledge Forge (internal: Orbital) public surface.
 *
 * Everything under `apps/web/src/orb/` is exported here so consumers
 * (today: App.tsx; tomorrow: PR 2-8 surfaces) import from a single
 * stable path. Side-effect import of `tokens.css` lives inside
 * `KnowledgeForgeApp.tsx` so the styles ship with the lazy chunk.
 */

export { Btn } from "./atoms/Btn";
export type { BtnKind, BtnProps } from "./atoms/Btn";
export { Card, CardHead } from "./atoms/Card";
export type { CardHeadProps, CardProps } from "./atoms/Card";
export { Kbd } from "./atoms/Kbd";
export type { KbdProps } from "./atoms/Kbd";
export { MetaRow } from "./atoms/MetaRow";
export type { MetaRowProps } from "./atoms/MetaRow";
export { ScopeChip } from "./atoms/ScopeChip";
export type { DocScope, ScopeChipProps } from "./atoms/ScopeChip";
export { SectionH } from "./atoms/SectionH";
export type { SectionHProps } from "./atoms/SectionH";
export { StatusBadge } from "./atoms/StatusBadge";
export type { DocStatus, StatusBadgeProps } from "./atoms/StatusBadge";
export { OrbI } from "./atoms/icons";
export type { OrbIconName } from "./atoms/icons";

export { DxShell } from "./shell/DxShell";
export type {
  DxShellProps,
  OrbDensity,
  OrbTheme,
} from "./shell/DxShell";
export { IconRail } from "./shell/IconRail";
export type { IconRailProps, RailTileId } from "./shell/IconRail";
export { TopBar } from "./shell/TopBar";
export type { TopBarProps, TopNavTab } from "./shell/TopBar";

export { KnowledgeForgeApp } from "./KnowledgeForgeApp";
export type { KnowledgeForgeAppProps } from "./KnowledgeForgeApp";
