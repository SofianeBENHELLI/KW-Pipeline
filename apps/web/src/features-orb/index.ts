/**
 * Phase-1 of the Orbital redesign — entry point for the `/orb` route.
 * Components here render against the new `.orb-*` design system in
 * `src/styles/tokens.css` and stay isolated from the legacy `features/`
 * tree until phases 2-7 retire the old surfaces one by one.
 */

export { OrbCatalogView } from "./CatalogView";
export { OrbShell } from "./Shell";
export type { OrbShellProps } from "./Shell";
export { CatalogRail, viewToStatuses } from "./CatalogRail";
export type { CatalogRailProps, CatalogView } from "./CatalogRail";
export { CatalogTable } from "./CatalogTable";
export type { CatalogTableProps } from "./CatalogTable";
export { ReviewPane } from "./ReviewPane";
export type { ReviewPaneProps } from "./ReviewPane";
export { pruneSelectionAfterBatch, runBatchPipeline } from "./batch";
export type { BatchFailure, BatchProgressEntry, BatchSnapshot, BatchStage } from "./batch";
export { GraphPanel } from "./GraphPanel";
export type { GraphPanelProps } from "./GraphPanel";
export { OrbSearchPanel } from "./SearchPanel";
export type { OrbSearchPanelProps } from "./SearchPanel";
export { OrbChatPanel } from "./ChatPanel";
export type { OrbChatPanelProps } from "./ChatPanel";
export { OrbAdminHub } from "./AdminHub";
export { OrbAdminAudit } from "./AdminAudit";
