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

// PR 2 — Review Workspace
export { ReviewWorkspace, sortDocs } from "./review/ReviewWorkspace";
export type { ReviewWorkspaceProps } from "./review/ReviewWorkspace";
export { DocRail, DEFAULT_VIEWS } from "./review/DocRail";
export type {
  DocRailProps,
  RailSort,
  RailSortColumn,
  RailSortDir,
  RailViewDef,
} from "./review/DocRail";
export { DocHeader } from "./review/DocHeader";
export type { DocHeaderProps } from "./review/DocHeader";
export { DocTabs } from "./review/DocTabs";
export type { DocTab, DocTabsProps } from "./review/DocTabs";

// PR 2 — hooks
export { useDocuments, viewToStatuses } from "./hooks/useDocuments";
export type {
  RailView,
  UseDocumentsOptions,
  UseDocumentsResult,
  UseDocumentsStatus,
} from "./hooks/useDocuments";
export { useDocumentDetail } from "./hooks/useDocumentDetail";
export type {
  DocumentDetailStatus,
  UseDocumentDetailResult,
} from "./hooks/useDocumentDetail";

// PR 2 — formatters
export {
  distinctScopeKinds,
  formatBytes,
  latestStatus,
  latestVersion,
  scopeKindToChipScope,
  splitIsoTimestamp,
} from "./review/format";

// PR 3 — Linked View
export { LinkedView } from "./review/LinkedView";
export type { LinkedViewProps, ObjKind } from "./review/LinkedView";
export { useLinkedObjects, projectGraph } from "./hooks/useLinkedObjects";
export type {
  LinkedChunk,
  LinkedEntity,
  LinkedObjects,
  LinkedObjectsStatus,
  LinkedTopic,
  UseLinkedObjectsResult,
} from "./hooks/useLinkedObjects";
