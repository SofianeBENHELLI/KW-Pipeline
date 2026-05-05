import type { DocumentVersionStatus } from "../domain/document";

interface StatusBadgeProps {
  status: DocumentVersionStatus;
}

const statusLabels: Record<DocumentVersionStatus, string> = {
  UPLOADED: "Uploaded",
  HASHED: "Hashed",
  DUPLICATE_DETECTED: "Duplicate",
  STORED: "Stored",
  EXTRACTING: "Extracting",
  EXTRACTED: "Extracted",
  SEMANTIC_READY: "Semantic ready",
  NEEDS_REVIEW: "Needs review",
  VALIDATED: "Validated",
  REJECTED: "Rejected",
  FAILED: "Failed",
  SUPERSEDED: "Superseded",
  PURGED: "Purged",
};

const statusDescriptions: Record<DocumentVersionStatus, string> = {
  UPLOADED: "File received but not yet hashed.",
  HASHED: "Content hashed; checking for duplicates.",
  DUPLICATE_DETECTED: "This file matches the bytes of an earlier version.",
  STORED: "Stored and ready for extraction.",
  EXTRACTING: "Parser is running.",
  EXTRACTED: "Raw extraction is available; ready to generate semantic output.",
  SEMANTIC_READY: "Semantic output generated; ready for reviewer.",
  NEEDS_REVIEW: "Awaiting reviewer validation.",
  VALIDATED: "Approved by a reviewer.",
  REJECTED: "Rejected by a reviewer.",
  FAILED: "Pipeline error; see the failure reason on the document.",
  SUPERSEDED: "Replaced by a newer validated version of the same document.",
  PURGED: "Source artifacts were physically deleted by an admin; only the catalog row remains.",
};

export function StatusBadge({ status }: StatusBadgeProps) {
  const label = statusLabels[status];
  const description = statusDescriptions[status];
  return (
    <span
      className={`status-badge status-${status.toLowerCase()}`}
      title={description}
      aria-label={`${label}. ${description}`}
    >
      {label}
    </span>
  );
}
