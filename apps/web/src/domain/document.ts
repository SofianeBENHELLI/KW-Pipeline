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

import type { ApiDocument, ApiDocumentVersion } from "../api/types";

export function latestVersion(document: ApiDocument): ApiDocumentVersion {
  const version = document.versions.find(
    (item) => item.id === document.latest_version_id,
  );
  return version ?? document.versions[document.versions.length - 1];
}
