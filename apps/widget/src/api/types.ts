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

export interface DocumentHashCheck {
  exists: boolean;
  document_id: string | null;
  version_id: string | null;
  version_number: number | null;
  original_filename: string | null;
  sha256: string;
}

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

/**
 * Single hit returned by ``GET /knowledge/search`` (Phase 3 / ADR-015).
 * Mirrors ``apps/api/app/schemas/knowledge.py::ChunkSearchResult``.
 */
export interface ChunkSearchResult {
  chunk_id: string;
  document_id: string;
  version_id: string;
  section_id: string;
  snippet: string | null;
  score: number;
}

/**
 * Response envelope for ``GET /knowledge/search``. Mirrors
 * ``apps/api/app/schemas/knowledge.py::ChunkSearchResponse``.
 */
export interface ChunkSearchResponse {
  schema_version: string;
  query: string;
  embedding_model: string;
  query_embedding_dim: number;
  results: ChunkSearchResult[];
}

/**
 * Chat retrieval mode (ADR-016). One of ``rag`` (vector only),
 * ``graph`` (projected entity triples only), or ``hybrid`` (both).
 */
export type ChatMode = "rag" | "graph" | "hybrid";

/**
 * Request body for ``POST /knowledge/chat``. Mirrors
 * ``apps/api/app/schemas/knowledge.py::ChatRequest``.
 */
export interface ChatRequest {
  question: string;
  mode?: ChatMode;
  top_k?: number;
}

/**
 * One context source the chat answer was grounded in. Mirrors
 * ``apps/api/app/schemas/knowledge.py::ChatCitation``.
 */
export interface ChatCitation {
  chunk_id: string;
  document_id: string;
  version_id: string;
  section_id: string;
  snippet: string | null;
  score: number;
}

/**
 * Response body for ``POST /knowledge/chat``. Mirrors
 * ``apps/api/app/schemas/knowledge.py::ChatResponse``.
 */
export interface ChatResponse {
  schema_version: string;
  question: string;
  mode: ChatMode;
  answer: string;
  citations: ChatCitation[];
  embedding_model: string | null;
  llm_model: string;
  token_usage: Record<string, number>;
  warnings: string[];
}
