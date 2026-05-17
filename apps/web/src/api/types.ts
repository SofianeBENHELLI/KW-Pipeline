/**
 * Public API type aliases for Orbital.
 *
 * These are thin re-exports of types generated from the Harvester OpenAPI
 * schema (see `generated/schema.ts`). Do NOT hand-edit shapes here — change
 * the FastAPI Pydantic models and regenerate (see
 * `docs/workflows/openapi_codegen.md`).
 *
 * The alias layer exists so consumers can import stable names like
 * `ApiDocument` instead of `components["schemas"]["Document"]` everywhere.
 */

import type { components } from "./generated/schema";

type Schemas = components["schemas"];

// ─── Document / Version ────────────────────────────────────────────────────

export type ApiDocument = Schemas["Document"];
export type ApiDocumentVersion = Schemas["DocumentVersion"];
export type DocumentVersionStatus = Schemas["DocumentVersionStatus"];
export type ListDocumentsResponse = Schemas["DocumentListResponse"];

// ─── Extraction ─────────────────────────────────────────────────────────────

export type ApiSourceReference = Schemas["SourceReference"];
export type ApiRawSection = Schemas["RawSection"];
export type ApiRawExtraction = Schemas["RawExtraction"];

/**
 * 202 Accepted body returned by ``POST /documents/.../extract`` when
 * ``KW_EXTRACTION_INLINE=false`` (ADR-006 PR-2). Inline mode keeps
 * returning ``ApiRawExtraction``. Clients that need to distinguish
 * 200 from 202 can branch on the presence of ``job_id`` /
 * ``parser_name``.
 */
export type ApiExtractionJobSnapshot = Schemas["ExtractionJobSnapshot"];

// ─── Semantic Document ──────────────────────────────────────────────────────

export type ApiDocumentProfile = Schemas["DocumentProfile"];
export type ApiSemanticSection = Schemas["SemanticSection"];
export type ApiSemanticAsset = Schemas["SemanticAsset"];
export type ApiSemanticDocument = Schemas["SemanticDocument"];

export type ReviewStatus = ApiSemanticAsset["review_status"];
export type ValidationStatus = ApiSemanticDocument["validation_status"];

// ─── Workspace scope (EPIC-D #218 / #250 / #258) ───────────────────────────
// One row of the ``document_scopes`` join table. Surfaced on every
// catalog read endpoint as ``Document.scopes`` since #258, and on
// upload-time as ``UploadDocumentResponse.scopes`` since #250.
export type ApiScope = Schemas["Scope"];

// ─── Upload response ─────────────────────────────────────────────────────────
// POST /documents/upload now returns a payload that extends DocumentVersion
// with ``scopes: list[Scope]`` (#250). Use the schema-generated alias so
// the call site stays pinned to the regenerated OpenAPI contract.
export type ApiUploadResponse = Schemas["UploadDocumentResponse"];

// ─── Hash precheck (#292) ──────────────────────────────────────────────────
// Response of ``GET /documents/by-hash/{sha256}``. ``exists=true`` means
// the catalog already has this digest and the upload will be flagged
// as DUPLICATE_DETECTED — Forge surfaces this before sending bytes.
export type ApiDocumentHashCheck = Schemas["DocumentHashCheckResponse"];

// ─── PDF viewer chunk locations (Phase 2 of the PDF-viewer plan) ───────────

export type ApiNormalizedRect = Schemas["NormalizedRect"];
export type ApiChunkLocation = Schemas["ChunkLocation"];
export type ApiChunkLocationsResponse = Schemas["ChunkLocationsResponse"];
export type ApiChunkSource = ApiChunkLocation["source"];

// ─── Knowledge graph ─────────────────────────────────────────────────────────

export type ApiGraphNode = Schemas["GraphNode"];
export type ApiGraphEdge = Schemas["GraphEdge"];
export type ApiKnowledgeGraphProjection = Schemas["KnowledgeGraphProjection"];
export type ApiKnowledgeGraphPage = Schemas["KnowledgeGraphPage"];
export type ApiProjectionStatusResponse = Schemas["ProjectionStatusResponse"];

// ─── Knowledge search (Phase 3 / ADR-015) ──────────────────────────────────

export type ApiChunkSearchResult = Schemas["ChunkSearchResult"];
export type ApiChunkSearchResponse = Schemas["ChunkSearchResponse"];

// ─── Batch upload (#82) ─────────────────────────────────────────────────────

export type ApiBatchUploadOutcome = Schemas["BatchUploadOutcome"];
export type ApiBatchUploadSummary = Schemas["BatchUploadSummary"];
export type ApiBatchUploadResult = Schemas["BatchUploadResult"];

// ─── Admin / Archive (D.9 admin UI) ─────────────────────────────────────────

/** One row of the Archive listing (``GET /admin/archive/archived_documents``).
 *  Carries the version-purged / version-remaining split + the most-recently
 *  removed scope link so the admin UI can render a row without per-doc
 *  probes. */
export type ApiArchivedDocumentItem = Schemas["ArchivedDocumentItem"];

/** Paginated response from ``GET /admin/archive/archived_documents``. */
export type ApiArchivedDocumentsResponse = Schemas["ArchivedDocumentsResponse"];

/** Response body of ``POST /admin/archive/unarchive``. */
export type ApiUnarchiveResponse = Schemas["UnarchiveResponse"];

/** Response body of ``POST /admin/archive/purge_artifacts``. Carries
 *  per-version tombstone URIs and the dry-run flag. */
export type ApiPurgeArtifactsResponse = Schemas["PurgeArtifactsResponse"];

/** Per-version row inside a purge response. */
export type ApiVersionPurgeResult = Schemas["VersionPurgeResult"];

/** Body of ``POST /admin/orbital/purge_document`` (#292). */
export type ApiOrbitalPurgeDocumentRequest =
  Schemas["OrbitalPurgeDocumentRequest"];

/** Response body of ``POST /admin/orbital/purge_document`` (#292). */
export type ApiOrbitalPurgeDocumentResponse =
  Schemas["OrbitalPurgeDocumentResponse"];

/** Response body of ``POST /admin/orbital/purge_all`` (#292 — bulk override). */
export type ApiOrbitalPurgeAllResponse = Schemas["OrbitalPurgeAllResponse"];

/**
 * Operator-typed phrase the bulk-purge route demands as a second
 * confirmation. Mirrors ``ORBITAL_PURGE_ALL_PHRASE`` in
 * ``app/schemas/admin_archive.py``.
 */
export const ORBITAL_PURGE_ALL_PHRASE = "PURGE ALL DOCUMENTS";

// ─── Admin / HITL dashboard (#215, EPIC-A close-out) ───────────────────────

/** Read-only HITL routing state snapshot powering the Admin HITL
 *  dashboard. Surfaces config posture + per-bucket SPC counters +
 *  drift ratios + the pending auto-promotion queue depth. */
export type ApiAdminHITLStateResponse = Schemas["AdminHITLStateResponse"];

/** One ``(content_type, topic_cluster)`` row of the dashboard table —
 *  the SPC sampling counters plus the route-derived drift_ratio /
 *  effective_sample_rate. */
export type ApiBucketState = Schemas["BucketState"];

/** Result envelope of ``POST /admin/hitl/run_auto_promote_pass``,
 *  surfaced inline on the dashboard's "Run pass" trigger. */
export type ApiAutoPromoteResult = Schemas["AutoPromoteResult"];

// ─── Admin / Audit log viewer (#206 follow-up) ────────────────────────────

/** One row in the Admin Audit Log Viewer table. ``actor`` is projected
 *  out of ``payload['actor']`` server-side so the UI can filter on it
 *  without re-parsing the JSON blob. */
export type ApiAuditEventItem = Schemas["AuditEventItem"];

/** Paginated response from ``GET /admin/audit/events``. Carries the
 *  cursor for "Load more" plus ``available_event_names`` so the UI's
 *  filter dropdown is self-populating without a second probe. */
export type ApiAdminAuditEventsResponse = Schemas["AdminAuditEventsResponse"];

// ─── Admin / Archive relink + bulk purge (#218 D.9, slices 2 + 5) ──────────

/** Body for ``POST /admin/archive/relink_scope``. */
export type ApiRelinkScopeRequest = Schemas["RelinkScopeRequest"];

/** Response body for ``POST /admin/archive/relink_scope``. */
export type ApiRelinkScopeResponse = Schemas["RelinkScopeResponse"];

/** ``ScopeKind`` literal pulled off the relink request payload. The
 *  Pydantic ``Literal`` is inlined by openapi-typescript so we re-export
 *  it from the request shape rather than from a top-level alias. */
export type ApiScopeKind = ApiRelinkScopeRequest["scope_kind"];

/** Body for ``POST /admin/archive/purge_batch``. */
export type ApiPurgeBatchRequest = Schemas["PurgeBatchRequest"];

/** Response body for ``POST /admin/archive/purge_batch``. */
export type ApiPurgeBatchResponse = Schemas["PurgeBatchResponse"];

/** Per-document row inside a purge_batch response. */
export type ApiPurgeBatchResult = Schemas["PurgeBatchResult"];

// ─── Admin / Taxonomy versioning (EPIC-1 §1.8, ADR-018) ──────────────────

/** One versioned taxonomy resource — wraps the existing ``Taxonomy``
 *  tree with lifecycle metadata (``state``, ``version_number``,
 *  ``state_changed_at``, ``created_by``). Returned by the
 *  ``GET /admin/taxonomy/versions/{tid}/{vnum}`` route and as each
 *  entry in the lineage list response. */
export type ApiTaxonomyVersion = Schemas["TaxonomyVersion"];

/** ``DRAFT | CANDIDATE_V0 | VALIDATED_V1 | ARCHIVED | DISCARDED`` —
 *  the lifecycle state machine pinned by ADR-018 §2. Re-exported off
 *  the version payload so consumers can switch on the literal
 *  exhaustively without importing the raw schema module. */
export type ApiTaxonomyState = ApiTaxonomyVersion["state"];

/** One proposed concept attached to a DRAFT version. The state lives
 *  on its own per-suggestion FSM (ADR-018 §5); ``merge_target_id`` is
 *  required when ``state === "MERGED"``. */
export type ApiConceptSuggestion = Schemas["ConceptSuggestion"];

/** ``NEW | UNDER_REVIEW | ACCEPTED | REJECTED | MERGED | DEFERRED`` —
 *  per-suggestion FSM. Re-exported off the suggestion payload to keep
 *  the import surface symmetric with the version state above. */
export type ApiConceptSuggestionState = ApiConceptSuggestion["state"];

/** Lineage-list payload: every version of one ``taxonomy_id`` sorted
 *  by ``version_number`` ascending. */
export type ApiTaxonomyVersionListResponse =
  Schemas["TaxonomyVersionListResponse"];

/** Currently-active taxonomy descriptor surfaced by
 *  ``GET /knowledge/taxonomy`` — the rail badge consumes only the
 *  lifecycle metadata (id, version number, state, label) and ignores
 *  the embedded category tree, so the alias points at the same
 *  ``TaxonomyVersion`` payload reused across the admin lineage view
 *  (ADR-018 §PR #346). */
export type ApiTaxonomy = ApiTaxonomyVersion;

// ─── Knowledge chat (Phase 3 grounded RAG / GraphRAG / Hybrid) ─────────────

export type ApiChatRequest = Schemas["ChatRequest"];
// ``ChatMode`` is a Pydantic ``Literal`` so openapi-typescript inlines it
// on the ``ChatRequest.mode`` field rather than emitting a named alias.
// Re-export the value type so call sites can stay symmetric with the
// backend taxonomy.
export type ApiChatMode = ApiChatRequest["mode"];
export type ApiChatCitation = Schemas["ChatCitation"];
export type ApiChatResponse = Schemas["ChatResponse"];
