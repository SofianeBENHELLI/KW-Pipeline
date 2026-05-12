/**
 * StatusBadge — FSM-state pill (mono 10px, dot + uppercase label).
 *
 * Renders the Knowledge Forge document lifecycle states. Color is never
 * the only signal — every badge has a dot AND a text label. The 8 known
 * statuses are listed below; unknown statuses fall through to "STORED"
 * styling so the surface keeps rendering.
 *
 * See `apps/web/src/orb/tokens.css` for `.orb-status--{slug}` palette.
 */
import type { ReactElement } from "react";

export type DocStatus =
  | "STORED"
  | "EXTRACTING"
  | "EXTRACTED"
  | "SEMANTIC_READY"
  | "NEEDS_REVIEW"
  | "VALIDATED"
  | "REJECTED"
  | "FAILED"
  | "DUPLICATE_DETECTED";

interface StatusEntry {
  label: string;
  cls: string;
}

const STATUSES: Record<DocStatus, StatusEntry> = {
  STORED:             { label: "STORED",         cls: "orb-status--stored" },
  EXTRACTING:         { label: "EXTRACTING",     cls: "orb-status--extracting" },
  EXTRACTED:          { label: "EXTRACTED",      cls: "orb-status--extracted" },
  SEMANTIC_READY:     { label: "SEMANTIC_READY", cls: "orb-status--semantic" },
  NEEDS_REVIEW:       { label: "NEEDS_REVIEW",   cls: "orb-status--review" },
  VALIDATED:          { label: "VALIDATED",      cls: "orb-status--validated" },
  REJECTED:           { label: "REJECTED",       cls: "orb-status--rejected" },
  FAILED:             { label: "FAILED",         cls: "orb-status--failed" },
  DUPLICATE_DETECTED: { label: "DUPLICATE",      cls: "orb-status--duplicate" },
};

export interface StatusBadgeProps {
  status: DocStatus | string;
}

export function StatusBadge({ status }: StatusBadgeProps): ReactElement {
  const s = STATUSES[status as DocStatus] ?? STATUSES.STORED;
  return (
    <span className={`orb-status ${s.cls}`} role="status" aria-label={s.label}>
      <span className="dot" aria-hidden="true" />
      {s.label}
    </span>
  );
}
