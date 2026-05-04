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

// ─── Semantic Document ──────────────────────────────────────────────────────

export type ApiDocumentProfile = Schemas["DocumentProfile"];
export type ApiSemanticSection = Schemas["SemanticSection"];
export type ApiSemanticAsset = Schemas["SemanticAsset"];
export type ApiSemanticDocument = Schemas["SemanticDocument"];

export type ReviewStatus = ApiSemanticAsset["review_status"];
export type ValidationStatus = ApiSemanticDocument["validation_status"];

// ─── Workspace scope (EPIC-D #218 / #250) ───────────────────────────────────
// One row of the ``document_scopes`` join table. The upload response
// surfaces the full list at upload time; ``GET /documents`` does not
// yet — see ``ScopeChip`` for how the missing-field case is handled.
export type ApiScope = Schemas["Scope"];

// ─── Upload response ─────────────────────────────────────────────────────────
// POST /documents/upload now returns a payload that extends DocumentVersion
// with ``scopes: list[Scope]`` (#250). Use the schema-generated alias so
// the call site stays pinned to the regenerated OpenAPI contract.
export type ApiUploadResponse = Schemas["UploadDocumentResponse"];

// ─── Knowledge graph ─────────────────────────────────────────────────────────

export type ApiGraphNode = Schemas["GraphNode"];
export type ApiGraphEdge = Schemas["GraphEdge"];
export type ApiKnowledgeGraphProjection = Schemas["KnowledgeGraphProjection"];
export type ApiKnowledgeGraphPage = Schemas["KnowledgeGraphPage"];

// ─── Knowledge search (Phase 3 / ADR-015) ──────────────────────────────────

export type ApiChunkSearchResult = Schemas["ChunkSearchResult"];
export type ApiChunkSearchResponse = Schemas["ChunkSearchResponse"];

// ─── Batch upload (#82) ─────────────────────────────────────────────────────

export type ApiBatchUploadOutcome = Schemas["BatchUploadOutcome"];
export type ApiBatchUploadSummary = Schemas["BatchUploadSummary"];
export type ApiBatchUploadResult = Schemas["BatchUploadResult"];

// ─── Knowledge chat (Phase 3 grounded RAG / GraphRAG / Hybrid) ─────────────

export type ApiChatRequest = Schemas["ChatRequest"];
// ``ChatMode`` is a Pydantic ``Literal`` so openapi-typescript inlines it
// on the ``ChatRequest.mode`` field rather than emitting a named alias.
// Re-export the value type so call sites can stay symmetric with the
// backend taxonomy.
export type ApiChatMode = ApiChatRequest["mode"];
export type ApiChatCitation = Schemas["ChatCitation"];
export type ApiChatResponse = Schemas["ChatResponse"];
