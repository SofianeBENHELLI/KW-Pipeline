/**
 * Hand-written API shapes for the Knowledge Explorer.
 *
 * Mirrors the public Pydantic models in apps/api/app/schemas/. We hand-
 * write rather than re-use apps/web's generated `schema.ts` so the widget
 * stays self-contained on the dashboard host. If the backend models
 * change in a way that touches the fields below, update them here too —
 * the OpenAPI snapshot test guards the wire shape against drift.
 */

export interface Health {
  status: string;
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

// ─── Raw extraction ──────────────────────────────────────────────────────────

export interface SourceReference {
  id: string;
  document_version_id: string;
  section_id: string;
  page_number: number | null;
  line_start: number | null;
  line_end: number | null;
  snippet: string;
}

export interface RawSection {
  id: string;
  heading: string;
  text: string;
  source_reference_ids: string[];
  page_number: number | null;
  bbox: [number, number, number, number] | null;
  parser_metadata: Record<string, string>;
}

export interface RawExtraction {
  id: string;
  document_version_id: string;
  parser_name: string;
  parser_version: string;
  text: string;
  sections: RawSection[];
  source_references: SourceReference[];
  warnings: string[];
  created_at: string;
}

// ─── Semantic document ───────────────────────────────────────────────────────

export interface DocumentProfile {
  title: string;
  document_type: string;
  purpose: string | null;
  audience: string | null;
  executive_summary: string | null;
}

export interface SemanticSection {
  id: string;
  heading: string;
  text: string;
  source_reference_ids: string[];
}

export type ReviewStatus =
  | "needs_review"
  | "source_backed"
  | "validated"
  | "rejected";

export interface SemanticAsset {
  id: string;
  type: string;
  text: string;
  confidence: number;
  review_status: ReviewStatus;
  source_reference_ids: string[];
}

export type ValidationStatus = "needs_review" | "validated" | "rejected";

export interface SemanticDocument {
  id: string;
  document_version_id: string;
  schema_version: string;
  document_profile: DocumentProfile;
  sections: SemanticSection[];
  assets: SemanticAsset[];
  warnings: string[];
  source_references: SourceReference[];
  validation_status: ValidationStatus;
  markdown: string | null;
  created_at: string;
}

// ─── Knowledge graph ─────────────────────────────────────────────────────────

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

export interface KnowledgeGraphProjection {
  schema_version: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface KnowledgeGraphPage {
  schema_version: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  next_cursor: string | null;
}

// ─── Taxonomy (ADR-017) ──────────────────────────────────────────────────────

/**
 * One node in the operator-imposed taxonomy tree. Mirrors
 * ``app.schemas.taxonomy::TaxonomyCategory`` — see ADR-017 for the
 * shape rationale (tree, not graph; ids stable across runs;
 * description embedded by the classifier).
 */
export interface TaxonomyCategory {
  id: string;
  label: string;
  description: string;
  subcategories: TaxonomyCategory[];
}

/**
 * Wire shape of ``GET /knowledge/taxonomy``. ``is_configured`` is
 * ``false`` when the operator hasn't pointed ``KW_TAXONOMY_PATH`` at
 * a YAML file — the route never 404s on that condition (it returns
 * 200 with empty ``categories``). The Explorer uses ``is_configured``
 * to decide whether the cluster rail should label categories
 * ``imposed`` vs fall back to the auto-deduced (``computed``) source
 * derived from snapshot data.
 */
export interface TaxonomyResponse {
  schema_version: string;
  is_configured: boolean;
  source_path: string | null;
  categories: TaxonomyCategory[];
}
