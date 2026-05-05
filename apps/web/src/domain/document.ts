/**
 * Domain types for the Orbital frontend.
 *
 * The canonical shapes are the API response interfaces in `src/api/types.ts`.
 * This module re-exports what the UI actually needs and keeps helpers that
 * work over those types.
 */

export type {
  ApiDocument as PipelineDocument,
  ApiDocumentVersion as DocumentVersion,
  ApiSemanticDocument as SemanticDocument,
  ApiSemanticSection as SemanticSection,
  ApiSourceReference as SourceReference,
  DocumentVersionStatus,
  ValidationStatus,
} from "../api/types";

import type { ApiDocument, ApiDocumentVersion, ApiScope } from "../api/types";

export function latestVersion(document: ApiDocument): ApiDocumentVersion {
  const version = document.versions.find(
    (item) => item.id === document.latest_version_id,
  );
  return version ?? document.versions[document.versions.length - 1];
}

/**
 * Accessor for ``Document.scopes`` (EPIC-D #218 / #250 / #258).
 *
 * Every catalog read endpoint (``GET /documents``,
 * ``GET /documents/{id}``, ``GET /knowledge/catalog``) now populates
 * ``scopes`` server-side (#258), so the helper just reads it. The
 * nullish coalesce keeps the call site safe against pre-#258 schema
 * generations that callers might still have cached, and returns an
 * empty list rather than ``null`` so the chip renders its empty-state
 * branch on "no active scope links" without a separate "missing
 * field" path.
 */
export function documentScopes(
  document: ApiDocument,
): ReadonlyArray<ApiScope> {
  return document.scopes ?? [];
}
