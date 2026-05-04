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
 * Best-effort accessor for ``Document.scopes`` (EPIC-D #218 / #250).
 *
 * The OpenAPI ``Document`` schema does not currently carry a ``scopes``
 * field — only ``UploadDocumentResponse`` does. Real catalog responses
 * therefore arrive without it, but a follow-up will likely extend
 * ``GET /documents`` to expose the same field (D.5). This helper
 * reads the field defensively so the UI is forward-compatible without
 * waiting on the schema regen, and returns ``null`` when absent so
 * the chip can render its "No scope info" placeholder.
 */
export function documentScopes(
  document: ApiDocument,
): ReadonlyArray<ApiScope> | null {
  // ``Document`` doesn't declare ``scopes`` in the generated types,
  // so cast through ``unknown`` to keep the access strictly local.
  const maybe = (document as unknown as { scopes?: ReadonlyArray<ApiScope> })
    .scopes;
  return Array.isArray(maybe) ? maybe : null;
}
