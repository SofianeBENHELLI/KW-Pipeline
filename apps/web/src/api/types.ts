/**
 * TypeScript interfaces derived from the Harvester API response models.
 *
 * Keep these in sync with:
 *   apps/api/app/schemas/document.py
 *   apps/api/app/schemas/extraction.py
 *   apps/api/app/schemas/semantic_document.py
 */

// ─── Document / Version ────────────────────────────────────────────────────

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

export interface ApiDocumentVersion {
  id: string;
  document_id: string;
  version_number: number;
  filename: string;
  content_type: string;
  file_size: number;
  sha256: string;
  storage_uri: string;
  status: DocumentVersionStatus;
  duplicate_of_version_id: string | null;
  failure_reason: string | null;
  reviewer_note: string | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface ApiDocument {
  id: string;
  original_filename: string;
  latest_version_id: string;
  created_at: string;
  versions: ApiDocumentVersion[];
}

export interface ListDocumentsResponse {
  items: ApiDocument[];
  next_cursor: string | null;
}

// ─── Extraction ─────────────────────────────────────────────────────────────

export interface ApiSourceReference {
  id: string;
  document_version_id: string;
  section_id: string;
  page_number: number | null;
  line_start: number | null;
  line_end: number | null;
  snippet: string;
}

export interface ApiRawSection {
  id: string;
  heading: string;
  text: string;
  source_reference_ids: string[];
  page_number: number | null;
  bbox: [number, number, number, number] | null;
  parser_metadata: Record<string, string>;
}

export interface ApiRawExtraction {
  id: string;
  document_version_id: string;
  parser_name: string;
  parser_version: string;
  text: string;
  sections: ApiRawSection[];
  source_references: ApiSourceReference[];
  warnings: string[];
  created_at: string;
}

// ─── Semantic Document ───────────────────────────────────────────────────────

export type ReviewStatus = "needs_review" | "source_backed" | "validated" | "rejected";
export type ValidationStatus = "needs_review" | "validated" | "rejected";

export interface ApiDocumentProfile {
  title: string;
  document_type: string;
  purpose: string | null;
  audience: string | null;
  executive_summary: string | null;
}

export interface ApiSemanticSection {
  id: string;
  heading: string;
  text: string;
  source_reference_ids: string[];
}

export interface ApiSemanticAsset {
  id: string;
  type: string;
  text: string;
  confidence: number;
  review_status: ReviewStatus;
  source_reference_ids: string[];
}

export interface ApiSemanticDocument {
  id: string;
  document_version_id: string;
  schema_version: "v0.1";
  document_profile: ApiDocumentProfile;
  sections: ApiSemanticSection[];
  assets: ApiSemanticAsset[];
  warnings: string[];
  source_references: Record<string, unknown>[];
  validation_status: ValidationStatus;
  markdown: string | null;
  created_at: string;
}

// ─── Upload response ─────────────────────────────────────────────────────────
// POST /documents/upload returns a DocumentVersion (schemas/document.py)
export type ApiUploadResponse = ApiDocumentVersion;
