/**
 * Client-side derivation of the "facets" the Explorer filters on.
 *
 * The backend does not yet emit a first-class taxonomy (no
 * `profile.tags` field on SemanticDocument as of v0.1). Until it does,
 * the Explorer derives a usable filter set from data we already have:
 *
 *   * Document family fields (file extension, latest-version status).
 *   * Lightweight signals computed at fetch time and cached against
 *     the document id (semantic `document_type`, asset-type counts,
 *     graph topic labels). The cache is filled lazily — the Browse
 *     view fetches the semantic + graph projection for each document
 *     it lists, with a hard cap on parallelism to keep the UI snappy.
 *
 * Keeping derivation here rather than inside the React tree means the
 * filter pipeline (facet → matching documents) is straightforward to
 * unit-test if/when we add a vitest pass for the explorer.
 */

import { classifyDocument, type DocumentKind } from "../viewers/document-kind";
import type {
  Document,
  DocumentVersion,
  KnowledgeGraphProjection,
  SemanticDocument,
} from "../api/types";

export interface DocumentFacets {
  /** PDF / Word / PowerPoint / … — derived from content_type + filename. */
  kind: DocumentKind;
  /** `document_profile.document_type` from semantic, or null when missing. */
  documentType: string | null;
  /** Latest-version status, surfaced as a filter dimension. */
  status: DocumentVersion["status"];
  /** Asset-type histogram from the semantic synthesis. */
  assetTypes: string[];
  /** Topic-node labels from the knowledge-graph projection. */
  topics: string[];
}

export function deriveFacets(
  document: Document,
  semantic: SemanticDocument | null,
  projection: KnowledgeGraphProjection | null,
): DocumentFacets {
  const latest = latestVersion(document);
  const kind = classifyDocument(latest?.content_type, latest?.filename ?? document.original_filename);
  const documentType = semantic?.document_profile.document_type ?? null;
  const assetTypes = uniqueSorted(semantic?.assets.map((a) => a.type) ?? []);
  const topics = projection
    ? uniqueSorted(
        projection.nodes
          .filter((n) => n.kind === "Topic" || n.kind === "topic")
          .map((n) => n.label),
      )
    : [];
  return {
    kind,
    documentType,
    status: latest?.status ?? "STORED",
    assetTypes,
    topics,
  };
}

export function latestVersion(document: Document): DocumentVersion | null {
  const byId = document.versions.find((v) => v.id === document.latest_version_id);
  if (byId) return byId;
  return document.versions.length > 0
    ? document.versions[document.versions.length - 1]
    : null;
}

function uniqueSorted(values: string[]): string[] {
  const set = new Set<string>();
  for (const v of values) {
    const trimmed = v.trim();
    if (trimmed.length > 0) set.add(trimmed);
  }
  return Array.from(set).sort((a, b) => a.localeCompare(b));
}

// ─── Filter primitives used by BrowseSection ────────────────────────────────

export interface BrowseFilter {
  /** Substring match against `original_filename`. Empty = no filter. */
  q: string;
  /** Selected document kinds (PDF/Word/…). Empty = no filter. */
  kinds: DocumentKind[];
  /** Selected `document_type` values from semantic. Empty = no filter. */
  documentTypes: string[];
  /** Selected lifecycle statuses. Empty = no filter. */
  statuses: DocumentVersion["status"][];
  /** Selected topic labels from the graph. Empty = no filter. */
  topics: string[];
}

export const EMPTY_FILTER: BrowseFilter = {
  q: "",
  kinds: [],
  documentTypes: [],
  statuses: [],
  topics: [],
};

export function matchesFilter(
  document: Document,
  facets: DocumentFacets,
  filter: BrowseFilter,
): boolean {
  if (filter.q.trim().length > 0) {
    const needle = filter.q.trim().toLowerCase();
    if (!document.original_filename.toLowerCase().includes(needle)) return false;
  }
  if (filter.kinds.length > 0 && !filter.kinds.includes(facets.kind)) return false;
  if (filter.statuses.length > 0 && !filter.statuses.includes(facets.status)) return false;
  if (
    filter.documentTypes.length > 0 &&
    (facets.documentType === null || !filter.documentTypes.includes(facets.documentType))
  ) {
    return false;
  }
  if (
    filter.topics.length > 0 &&
    !filter.topics.some((t) => facets.topics.includes(t))
  ) {
    return false;
  }
  return true;
}

/** Aggregate counts so the filter UI can render "(N)" badges per option. */
export interface FacetHistogram {
  kinds: Map<DocumentKind, number>;
  documentTypes: Map<string, number>;
  statuses: Map<DocumentVersion["status"], number>;
  topics: Map<string, number>;
}

export function buildHistogram(
  documents: Document[],
  facetsByDocument: Map<string, DocumentFacets>,
): FacetHistogram {
  const kinds = new Map<DocumentKind, number>();
  const documentTypes = new Map<string, number>();
  const statuses = new Map<DocumentVersion["status"], number>();
  const topics = new Map<string, number>();
  for (const doc of documents) {
    const facets = facetsByDocument.get(doc.id);
    if (!facets) continue;
    kinds.set(facets.kind, (kinds.get(facets.kind) ?? 0) + 1);
    statuses.set(facets.status, (statuses.get(facets.status) ?? 0) + 1);
    if (facets.documentType !== null) {
      documentTypes.set(
        facets.documentType,
        (documentTypes.get(facets.documentType) ?? 0) + 1,
      );
    }
    for (const topic of facets.topics) {
      topics.set(topic, (topics.get(topic) ?? 0) + 1);
    }
  }
  return { kinds, documentTypes, statuses, topics };
}
