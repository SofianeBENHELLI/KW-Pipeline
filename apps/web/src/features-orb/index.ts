/**
 * Variant-A Orbital redesign (`/orb` route). All components paint
 * against the `.rwA-*` class set in `rwA.css`, ported verbatim from
 * the hi-fi mockup (`Orbital Knowledge.zip / orbital-review-a.jsx`).
 */

export { OrbCatalogView } from "./CatalogView";
export { OrbShell } from "./Shell";
export type { OrbShellProps, OrbNavItem } from "./Shell";
export { CatalogRail, viewToStatuses } from "./CatalogRail";
export type { CatalogRailProps, CatalogView } from "./CatalogRail";
export { DocPage } from "./DocPage";
export type { DocPageProps } from "./DocPage";
export { LinkedView } from "./LinkedView";
export type { LinkedViewProps } from "./LinkedView";
export { PipelineTab } from "./PipelineTab";
export type { PipelineTabProps, FsmAction } from "./PipelineTab";
export { OrbChatPanel } from "./ChatPanel";
export type { OrbChatPanelProps } from "./ChatPanel";
export { OrbSearchPanel } from "./SearchPanel";
export type { OrbSearchPanelProps } from "./SearchPanel";
export { OrbAdminHub } from "./AdminHub";
export { OrbAdminAudit } from "./AdminAudit";
export { OrbPurgeDialog, OrbPurgeAllDialog } from "./PurgeDialogs";
export type { OrbPurgeDialogProps, OrbPurgeAllDialogProps } from "./PurgeDialogs";
export { pruneSelectionAfterBatch, runBatchPipeline } from "./batch";
export type { BatchFailure, BatchProgressEntry, BatchSnapshot, BatchStage } from "./batch";
