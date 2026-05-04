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
 * One node in the hybrid taxonomy tree. Mirrors
 * ``app.schemas.taxonomy::TaxonomyCategory`` — see ADR-017 for the
 * shape rationale (tree, not graph; ids stable across runs;
 * description embedded by the classifier).
 *
 * ``source`` (#249) records which half of the hybrid taxonomy the
 * category came from: ``"imposed"`` for operator-authored YAML
 * entries, ``"computed"`` for auto-deduced topic clusters. Optional
 * for forward/backward-compat with older API builds — a missing
 * field is treated as ``"computed"`` by ``adaptTaxonomy`` since
 * that is the safer assumption for the badge in the rail.
 */
export interface TaxonomyCategory {
  id: string;
  label: string;
  description: string;
  subcategories: TaxonomyCategory[];
  source?: "computed" | "imposed";
}

/**
 * Wire shape of ``GET /knowledge/taxonomy``. ``is_configured`` is
 * ``false`` when **both** halves of the hybrid taxonomy are empty —
 * no YAML and no topic clusters — and the route returns 200 with
 * empty ``categories``. As soon as either half has content the
 * route is configured and ``categories`` carries entries from both,
 * each tagged with its ``source``.
 */
export interface TaxonomyResponse {
  schema_version: string;
  is_configured: boolean;
  source_path: string | null;
  categories: TaxonomyCategory[];
}
