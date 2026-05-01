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

// ─── Upload response ─────────────────────────────────────────────────────────
// POST /documents/upload returns a DocumentVersion.
export type ApiUploadResponse = ApiDocumentVersion;
