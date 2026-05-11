import type { DocumentVersionStatus } from "../../domain/document";

interface OrbStatusDefinition {
  label: string;
  modifier: string;
}

const STATUS_DEFS: Record<DocumentVersionStatus, OrbStatusDefinition> = {
  UPLOADED: { label: "UPLOADED", modifier: "orb-status--stored" },
  HASHED: { label: "HASHED", modifier: "orb-status--stored" },
  STORED: { label: "STORED", modifier: "orb-status--stored" },
  QUEUED_FOR_EXTRACTION: { label: "QUEUED", modifier: "orb-status--stored" },
  EXTRACTING: { label: "EXTRACTING", modifier: "orb-status--extracting" },
  EXTRACTED: { label: "EXTRACTED", modifier: "orb-status--extracted" },
  SEMANTIC_READY: { label: "SEMANTIC_READY", modifier: "orb-status--semantic" },
  NEEDS_REVIEW: { label: "NEEDS_REVIEW", modifier: "orb-status--review" },
  VALIDATED: { label: "VALIDATED", modifier: "orb-status--validated" },
  REJECTED: { label: "REJECTED", modifier: "orb-status--rejected" },
  SUPERSEDED: { label: "SUPERSEDED", modifier: "orb-status--duplicate" },
  FAILED: { label: "FAILED", modifier: "orb-status--failed" },
  DUPLICATE_DETECTED: { label: "DUPLICATE", modifier: "orb-status--duplicate" },
  PURGED: { label: "PURGED", modifier: "orb-status--duplicate" },
};

const FALLBACK: OrbStatusDefinition = { label: "UNKNOWN", modifier: "orb-status--stored" };

export interface OrbStatusBadgeProps {
  status: DocumentVersionStatus | string | null | undefined;
  className?: string;
}

export function OrbStatusBadge({ status, className }: OrbStatusBadgeProps) {
  const def = (status && STATUS_DEFS[status as DocumentVersionStatus]) || FALLBACK;
  return (
    <span className={["orb-status", def.modifier, className].filter(Boolean).join(" ")} aria-label={`status: ${def.label}`}>
      <span className="dot" aria-hidden="true" />
      {def.label}
    </span>
  );
}
