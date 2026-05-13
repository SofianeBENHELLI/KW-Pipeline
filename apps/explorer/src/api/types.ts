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

// Re-export the PDF-viewer chunk-location types from the shared
// module so Explorer's hand-written API surface aligns with what
// Orbital ships — both apps consume the same backend route, the
// shared mirror is the single source of truth this side of the wire.
export type {
  ChunkLocation,
  ChunkLocationsResponse,
  ChunkSource,
  NormalizedRect,
} from "../../../_shared/pdf-viewer";

import type { NormalizedRect } from "../../../_shared/pdf-viewer";

export interface SourceReference {
  id: string;
  document_version_id: string;
  section_id: string;
  page_number: number | null;
  line_start: number | null;
  line_end: number | null;
  snippet: string;
  // ``rects`` carries the normalised page rectangles emitted by the
  // PDF parser at ``parser_version >= 0.2`` (PDF-viewer Phase 1). The
  // field is optional on the wire (defaults to ``[]``) so legacy
  // payloads still deserialise; consumers that don't render highlights
  // can ignore it entirely.
  rects?: NormalizedRect[];
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

/**
 * Tracker entry for a knowledge-projection cycle.
 *
 * Mirrors ``apps/api/app/schemas/knowledge.py::ProjectionStatusResponse``
 * (added in #359). Polled by ``useProjectionStatus`` while
 * ``status === "IN_PROGRESS"`` so the Explorer's detail panel can show
 * a "Projecting…" indicator on validated documents whose graph is
 * still being populated.
 */
export interface ProjectionStatusResponse {
  version_id: string;
  status: "IN_PROGRESS" | "COMPLETED" | "FAILED";
  started_at: string;
  completed_at: string | null;
  error: string | null;
}

/**
 * Per-group result types for the multi-kind Explorer search
 * (#319 / #313, ADR-028). Mirrors
 * ``apps/api/app/schemas/knowledge_explore_search.py``.
 *
 * v0.1 ships chunks / documents / topics with content; entities and
 * relations ride through as empty lists so the wire shape stays
 * forward-compat for v0.2.
 *
 * Trust fields:
 *   - ``validation_status`` populated only at the document level in
 *     v0.1 (the backend leaves it ``null`` on chunks). The Explorer
 *     uses it to drive a "validated only" filter on the document
 *     group; chunk-level filtering by trust waits for v0.2.
 *   - ``is_source_backed`` is reserved at every level; ``false`` in
 *     v0.1.
 */
export interface ExploreSearchChunk {
  chunk_id: string;
  document_id: string;
  version_id: string;
  section_id: string;
  snippet: string | null;
  score: number;
  validation_status: string | null;
  is_source_backed: boolean;
}

export interface ExploreSearchDocument {
  document_id: string;
  title: string;
  score: number;
  validation_status: string | null;
  is_source_backed: boolean;
  contributing_chunks: ExploreSearchChunk[];
}

export interface ExploreSearchTopic {
  topic_id: string;
  label: string;
  keywords: string[];
  score: number;
  evidence_chunks: ExploreSearchChunk[];
}

export interface ExploreSearchEntity {
  entity_id: string;
  label: string;
  score: number;
  mention_chunks: ExploreSearchChunk[];
}

export interface ExploreSearchRelation {
  relation_id: string;
  kind: string;
  score: number;
  reason: string | null;
  shared_keywords: string[];
}

export interface ExploreSearchResponse {
  schema_version: "v0.1";
  query: string;
  embedding_model: string;
  chunks: ExploreSearchChunk[];
  documents: ExploreSearchDocument[];
  topics: ExploreSearchTopic[];
  entities: ExploreSearchEntity[];
  relations: ExploreSearchRelation[];
}

/**
 * Wire shapes for the relation explanation API (#311 / #318, ADR-028).
 *
 * Mirrors ``apps/api/app/schemas/knowledge_relations.py``. Used by
 * the relation evidence drawer (#318) to answer "why does this link
 * exist?" with score, reason, shared keywords, source chunks, and
 * citations.
 *
 * ``RelationEvidence`` covers single-edge evidence — every stored
 * graph-edge kind projects onto this one shape. The drawer pattern-
 * matches on ``provenance_class`` to know which evidence fields to
 * read (structural / deterministic / llm).
 *
 * ``AggregatedRelationEvidence`` synthesises a doc-doc relation from
 * the chunk-level edges that cross the (source, target) document
 * boundary; the drawer uses this for the "Related documents" surface
 * where a single edge id isn't available.
 */
export type ProvenanceClass = "structural" | "deterministic" | "llm";

export type StrengthClass = "strong" | "medium" | "weak";

export interface RelationEvidence {
  relation_id: string;
  kind: string;
  provenance_class: ProvenanceClass;
  source_id: string;
  target_id: string;

  // Scoring (#314) — populated for DETERMINISTIC edges only.
  score: number | null;
  strength_class: StrengthClass | null;
  is_bridge: boolean | null;
  is_outlier: boolean | null;
  contributing_factors: Record<string, number>;

  // Deterministic-edge evidence.
  reason: string | null;
  shared_keywords: string[];
  source_chunk_ids: string[];

  // LLM-edge evidence.
  confidence: number | null;
  predicate: string | null;
  source_section_id: string | null;
  source_reference_ids: string[];

  // Document context.
  document_id: string | null;
  version_id: string | null;
}

export interface ContributingChunkPair {
  relation_id: string;
  kind: string;
  source_chunk_id: string;
  target_chunk_id: string;
  score: number;
  strength_class: StrengthClass;
  reason: string;
  shared_keywords: string[];
}

export interface AggregatedRelationEvidence {
  source_document_id: string;
  target_document_id: string;
  aggregate_score: number;
  pair_count: number;
  top_contributing_pairs: ContributingChunkPair[];
  is_bridge: boolean;
  is_outlier: boolean;
}
