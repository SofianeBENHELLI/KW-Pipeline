/**
 * Hand-written API shapes for KW-Pipeline endpoints the widget calls.
 *
 * Kept narrow on purpose — the widget only needs a fraction of the full
 * OpenAPI surface, and pinning the few fields we render decouples this
 * package from `apps/web`'s generated schema (1k+ lines). The canonical
 * source of truth is `apps/api/app/schemas/`; if those models change in
 * a way that affects the fields below, update them here too.
 */

export interface Health {
  status: string;
  /** Optional version string — backend may not emit yet, treated as best-effort. */
  version?: string;
}

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

export interface DocumentVersion {
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

export interface Document {
  id: string;
  original_filename: string;
  latest_version_id: string;
  created_at: string;
  versions: DocumentVersion[];
}

export interface DocumentListResponse {
  items: Document[];
  next_cursor: string | null;
}

export interface GraphNode {
  id: string;
  kind: string;
  label: string;
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  source_id: string;
  target_id: string;
  kind: string;
  properties: Record<string, unknown>;
}

export interface KnowledgeGraphPage {
  schema_version: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  next_cursor: string | null;
}
