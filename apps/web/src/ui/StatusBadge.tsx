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
};

export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-${status.toLowerCase()}`}>
      {statusLabels[status]}
    </span>
  );
}
