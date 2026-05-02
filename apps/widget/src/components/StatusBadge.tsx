/**
 * Single source of truth for `DocumentVersionStatus` → (variant, icon, label)
 * mapping. Replaces the previous inline `statusBadgeClass()` helper in
 * `DocumentsList.tsx`. Status communication is no longer colour-only —
 * each badge carries an icon (check / cross / warn / clock / info)
 * matching the design handoff.
 */

import React from "react";

import type { DocumentVersionStatus } from "../api/types";
import { Icon, type IconName } from "./icons";

type Variant = "success" | "warn" | "danger" | "info" | "neutral";

const STATUS_PRESET: Record<DocumentVersionStatus | "INGESTED", [Variant, IconName]> = {
  VALIDATED: ["success", "check"],
  REJECTED: ["danger", "cross"],
  FAILED: ["danger", "cross"],
  DUPLICATE_DETECTED: ["warn", "warn"],
  NEEDS_REVIEW: ["warn", "warn"],
  EXTRACTED: ["info", "clock"],
  SEMANTIC_READY: ["info", "clock"],
  EXTRACTING: ["info", "clock"],
  STORED: ["neutral", "info"],
  HASHED: ["neutral", "info"],
  UPLOADED: ["neutral", "info"],
  INGESTED: ["neutral", "info"],
};

interface Props {
  status: DocumentVersionStatus | "INGESTED";
  /** Optional override for the visible text; defaults to the status string. */
  label?: string;
}

export const StatusBadge: React.FC<Props> = ({ status, label }) => {
  const [variant, icon] = STATUS_PRESET[status] ?? (["neutral", "info"] as [Variant, IconName]);
  return (
    <span className={`kw-badge kw-badge--${variant}`}>
      <Icon name={icon} size={10} />
      {label ?? status}
    </span>
  );
};
