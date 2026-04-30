export type DocumentVersionStatus =
  | "UPLOADED"
  | "HASHED"
  | "DUPLICATE_DETECTED"
  | "STORED"
  | "EXTRACTING"
  | "EXTRACTED"
  | "SEMANTIC_READY"
  | "NEEDS_REVIEW"
  | "VALIDATED"
  | "REJECTED"
  | "FAILED";

export type ValidationStatus = "needs_review" | "validated" | "rejected";

export interface SourceReference {
  source_id: string;
  page: number | null;
  line_start: number | null;
  line_end: number | null;
  snippet: string;
}

export interface SemanticSection {
  title: string;
  level: number;
  content: string;
  source_references: SourceReference[];
}

export interface SemanticDocument {
  document_version_id: string;
  validation_status: ValidationStatus;
  markdown: string;
  sections: SemanticSection[];
}

export interface DocumentVersion {
  id: string;
  document_id: string;
  version_number: number;
  filename: string;
  content_type: string;
  file_size: number;
  sha256: string;
  status: DocumentVersionStatus;
  duplicate_of_version_id: string | null;
  failure_reason: string | null;
  reviewer_note: string | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface PipelineDocument {
  id: string;
  original_filename: string;
  latest_version_id: string;
  created_at: string;
  versions: DocumentVersion[];
  extraction_text: string;
  semantic: SemanticDocument | null;
}

export function latestVersion(document: PipelineDocument): DocumentVersion {
  const version = document.versions.find(
    (item) => item.id === document.latest_version_id,
  );
  return version ?? document.versions[document.versions.length - 1];
}
