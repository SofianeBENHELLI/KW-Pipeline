/**
 * Thin fetch wrapper for the KW-Pipeline backend, scoped to the
 * Knowledge Explorer's read-only surface.
 *
 * Mirrors apps/widget/src/api/client.ts for envelope handling and
 * base-URL resolution. The error class and envelope parser themselves
 * live in ``apps/_shared/api-core`` (audit #227) so a bug fix to
 * envelope handling lands in one place rather than every frontend's
 * copy.
 */

import { widget } from "@widget-lab/3ddashboard-utils";

import { asApiError, withRetry } from "../../../_shared/api-core";
import type {
  AggregatedRelationEvidence,
  ChunkLocationsResponse,
  ChunkSource,
  Document,
  DocumentListResponse,
  ExploreSearchResponse,
  Health,
  KnowledgeGraphPage,
  KnowledgeGraphProjection,
  ProjectionStatusResponse,
  RawExtraction,
  RelationEvidence,
  SemanticDocument,
  TaxonomyResponse,
} from "./types";

// Re-export from the shared module so existing import sites
// (``import { ApiError } from "./api/client"``) keep working.
//
// ``setSessionTrigger`` / ``clearSessionTrigger`` are surfaced here
// so ``<SessionGuardProvider>`` (mounted at the explorer's root)
// can register its 401-trigger via this same module path
// (#83 slice 3).
export {
  ApiError,
  setSessionTrigger,
  clearSessionTrigger,
} from "../../../_shared/api-core";

const SETTINGS_KEY = "apiBaseUrl";
const FALLBACK_BASE_URL = "http://localhost:8000";

// Webpack's ``EnvironmentPlugin`` (see webpack.config.js) substitutes
// this expression with a string literal at build time — defaulting to
// ``""`` when the env var is unset, which the ``|| undefined`` then
// collapses to the FALLBACK_BASE_URL path below. The previous
// ``typeof process !== "undefined"`` guard was load-bearing only when
// no DefinePlugin was wired; under webpack 5's lazy ``process`` shim
// the guard left the substitution working *by accident* on every
// platform that happened to ship a shim, and silently fell back to
// ``http://localhost:8000`` on those that didn't. With the explicit
// EnvironmentPlugin in place, the simpler form below is the one
// source of truth.
const buildTimeBaseUrl: string | undefined =
  process.env.KW_API_BASE_URL || undefined;

function safeGetWidgetValue(key: string): string | null {
  try {
    const v = widget.getValue(key);
    return typeof v === "string" && v.length > 0 ? v : null;
  } catch {
    return null;
  }
}

function safeSetWidgetValue(key: string, value: string): void {
  try {
    widget.setValue(key, value);
  } catch {
    // Best-effort; persistence is unavailable when running outside the host.
  }
}

export function getApiBaseUrl(): string {
  return (
    safeGetWidgetValue(SETTINGS_KEY) ??
    (buildTimeBaseUrl && buildTimeBaseUrl.length > 0
      ? buildTimeBaseUrl
      : FALLBACK_BASE_URL)
  );
}

export function setApiBaseUrl(value: string): void {
  safeSetWidgetValue(SETTINGS_KEY, value);
}

// ─── Core request helpers ────────────────────────────────────────────────────

// ``fetchWithRetry`` transparently re-issues idempotent requests
// (GET / HEAD) on transient backend hiccups (502/503/504 + network
// errors). Only safe methods retry by default — see
// ``apps/_shared/api-core/retryFetch.ts`` for the full policy.
const fetchWithRetry = withRetry((...args) => fetch(...args));

async function request<T>(
  path: string,
  init: RequestInit & { baseUrl?: string } = {},
): Promise<T> {
  const baseUrl = init.baseUrl ?? getApiBaseUrl();
  const response = await fetchWithRetry(
    baseUrl.replace(/\/$/, "") + path,
    init,
  );
  if (!response.ok) throw await asApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function requestText(
  path: string,
  init: RequestInit & { baseUrl?: string } = {},
): Promise<string> {
  const baseUrl = init.baseUrl ?? getApiBaseUrl();
  const response = await fetchWithRetry(
    baseUrl.replace(/\/$/, "") + path,
    init,
  );
  if (!response.ok) throw await asApiError(response);
  return response.text();
}

// ─── Endpoints ───────────────────────────────────────────────────────────────

export function getHealth(
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<Health> {
  return request<Health>("/health", opts);
}

/**
 * GET /knowledge/projection_status/{version_id}
 *
 * Returns the in-process tracker entry for a version's knowledge-layer
 * projection (graph + entity extraction). The Explorer's detail panel
 * polls this on validated documents to know when the graph is fully
 * populated; a ``"COMPLETED"`` / ``"FAILED"`` response stops the poll
 * loop.
 *
 * Returns ``null`` on 404 — either projection never ran (knowledge
 * layer disabled) or the entry was pruned by the TTL. Both are "fall
 * back to whatever the graph endpoint returns directly", which is the
 * historical contract for clients that don't poll status.
 */
export async function getProjectionStatus(
  versionId: string,
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<ProjectionStatusResponse | null> {
  const baseUrl = opts.baseUrl ?? getApiBaseUrl();
  const path = `/knowledge/projection_status/${encodeURIComponent(versionId)}`;
  const response = await fetchWithRetry(baseUrl.replace(/\/$/, "") + path, {
    signal: opts.signal,
  });
  if (response.status === 404) return null;
  if (!response.ok) throw await asApiError(response);
  return (await response.json()) as ProjectionStatusResponse;
}

/**
 * List documents. Defaults to the broadest filter — the Explorer is a
 * navigation surface, not a review queue, so it shows every document in
 * the catalog regardless of lifecycle status. Callers can narrow via
 * ``status`` / ``q``.
 */
export function listDocuments(
  opts: {
    limit?: number;
    cursor?: string;
    status?: string[];
    q?: string;
    /**
     * When false, asks the backend to drop demo-corpus rows
     * (``include_demo=false``, Explorer Sprint 1). Omitted/true
     * keeps the legacy everything-visible behaviour.
     */
    includeDemo?: boolean;
    baseUrl?: string;
    signal?: AbortSignal;
  } = {},
): Promise<DocumentListResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 50));
  if (opts.cursor) params.set("cursor", opts.cursor);
  if (opts.status && opts.status.length > 0) {
    for (const value of opts.status) params.append("status", value);
  }
  if (opts.includeDemo === false) params.set("include_demo", "false");
  const trimmedQ = opts.q?.trim() ?? "";
  if (trimmedQ.length > 0) params.set("q", trimmedQ);
  return request<DocumentListResponse>(`/documents?${params.toString()}`, {
    baseUrl: opts.baseUrl,
    signal: opts.signal,
  });
}

export function getDocument(
  documentId: string,
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<Document> {
  return request<Document>(
    `/documents/${encodeURIComponent(documentId)}`,
    opts,
  );
}

export function getExtraction(
  documentId: string,
  versionId: string,
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<RawExtraction> {
  return request<RawExtraction>(
    `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/extraction`,
    opts,
  );
}

export function getSemantic(
  documentId: string,
  versionId: string,
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<SemanticDocument> {
  return request<SemanticDocument>(
    `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/semantic`,
    opts,
  );
}

export function getMarkdown(
  documentId: string,
  versionId: string,
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<string> {
  return requestText(
    `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/markdown`,
    opts,
  );
}

export function getDocumentGraph(
  documentId: string,
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<KnowledgeGraphProjection> {
  return request<KnowledgeGraphProjection>(
    `/documents/${encodeURIComponent(documentId)}/graph`,
    opts,
  );
}

/**
 * Fetch the operator-imposed taxonomy (ADR-017). Returns the full
 * ``TaxonomyResponse`` including ``is_configured`` so callers can
 * distinguish "operator hasn't authored a YAML yet" (200,
 * ``is_configured=false``, empty categories) from "auto-deduced
 * topic clustering" (the explorer's default left rail).
 *
 * Per the route docstring, this never returns 404. Older API
 * deployments without the route will surface as a 404 ApiError —
 * the Explorer treats that the same as ``is_configured=false`` and
 * falls back to its existing topic-clustering source.
 */
export function getKnowledgeTaxonomy(
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<TaxonomyResponse> {
  return request<TaxonomyResponse>("/knowledge/taxonomy", opts);
}

export function getKnowledgeGraph(
  opts: {
    limit?: number;
    cursor?: string;
    baseUrl?: string;
    signal?: AbortSignal;
  } = {},
): Promise<KnowledgeGraphPage> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 200));
  if (opts.cursor) params.set("cursor", opts.cursor);
  return request<KnowledgeGraphPage>(`/knowledge/graph?${params.toString()}`, {
    baseUrl: opts.baseUrl,
    signal: opts.signal,
  });
}

/**
 * GET /knowledge/explore/search
 *
 * Multi-kind grouped semantic search (#319 / #313, ADR-028). Returns
 * results bucketed by kind (chunks / documents / topics / entities /
 * relations) so the Explorer's search bar can render section-by-section.
 *
 * Requires the same gates as ``GET /knowledge/search`` —
 * ``KW_KNOWLEDGE_LAYER_ENABLED=true`` plus ``VOYAGE_API_KEY``. When
 * either is missing the route returns 503 with
 * ``KW_VECTOR_SEARCH_DISABLED``; the caller should surface a "vector
 * search disabled" panel and fall back to the local typeahead.
 *
 * Empty / whitespace queries should not be sent — the route rejects
 * them with 422; callers should short-circuit before the call.
 */
export function exploreSearch(
  query: string,
  opts: {
    chunkLimit?: number;
    documentLimit?: number;
    topicLimit?: number;
    contributingChunksPerDocument?: number;
    baseUrl?: string;
    signal?: AbortSignal;
  } = {},
): Promise<ExploreSearchResponse> {
  const params = new URLSearchParams();
  params.set("q", query);
  if (opts.chunkLimit !== undefined)
    params.set("chunk_limit", String(opts.chunkLimit));
  if (opts.documentLimit !== undefined) {
    params.set("document_limit", String(opts.documentLimit));
  }
  if (opts.topicLimit !== undefined)
    params.set("topic_limit", String(opts.topicLimit));
  if (opts.contributingChunksPerDocument !== undefined) {
    params.set(
      "contributing_chunks_per_document",
      String(opts.contributingChunksPerDocument),
    );
  }
  return request<ExploreSearchResponse>(
    `/knowledge/explore/search?${params.toString()}`,
    { baseUrl: opts.baseUrl, signal: opts.signal },
  );
}

/**
 * Build the URL for streaming the original uploaded binary back to the
 * browser. Returned as a string (not a Promise) because the typical
 * consumer is a `<iframe src=…>` or a `<embed>` — we don't want to
 * eagerly fetch the bytes ourselves and shovel them through a blob URL
 * unless we have to.
 */
/**
 * Health probe that also returns the round-trip latency in milliseconds,
 * measured client-side via `performance.now()`. Used by the SettingsPanel
 * reachability check.
 */
export async function getHealthWithLatency(
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<{ health: Health; latencyMs: number }> {
  const start = performance.now();
  const health = await getHealth(opts);
  const latencyMs = Math.round(performance.now() - start);
  return { health, latencyMs };
}

/**
 * GET /documents/{document_id}/versions/{version_id}/chunks
 *
 * Returns the chunk-location catalog for the PDF viewer: one row per
 * parser-emitted section with normalised rects, page, snippet, and a
 * topic-derived summary signal. See ``apps/_shared/pdf-viewer`` for
 * the consumer-side primitives that render this payload.
 *
 * Filters mirror the backend route's query params (page, source,
 * min_confidence); ``limit`` defaults to the server-side ceiling so
 * the viewer typically fetches every chunk in one round-trip.
 */
export function listDocumentChunks(
  documentId: string,
  versionId: string,
  opts: {
    baseUrl?: string;
    signal?: AbortSignal;
    page?: number;
    source?: ChunkSource;
    minConfidence?: number;
    limit?: number;
  } = {},
): Promise<ChunkLocationsResponse> {
  const params = new URLSearchParams();
  if (opts.page !== undefined) params.set("page", String(opts.page));
  if (opts.source !== undefined) params.set("source", opts.source);
  if (opts.minConfidence !== undefined)
    params.set("min_confidence", String(opts.minConfidence));
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  const query = params.toString();
  const path =
    `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/chunks` +
    (query ? `?${query}` : "");
  return request<ChunkLocationsResponse>(path, opts);
}

export function rawFileUrl(
  documentId: string,
  versionId: string,
  baseUrl: string = getApiBaseUrl(),
): string {
  return (
    baseUrl.replace(/\/$/, "") +
    `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/raw`
  );
}

/**
 * GET /knowledge/relations/{relation_id}
 *
 * Single-edge evidence lookup. ``relation_id`` is the engine's
 * structural composite (kind:source->target) — it contains ``:`` and
 * ``->`` separators that MUST be URL-encoded, which
 * ``encodeURIComponent`` handles.
 *
 * Returns ``null`` on 404 — the relation may have been pruned by a
 * subsequent re-projection. Callers should treat that the same as
 * "evidence unavailable" rather than an error state.
 */
export async function getRelationEvidence(
  relationId: string,
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<RelationEvidence | null> {
  const baseUrl = opts.baseUrl ?? getApiBaseUrl();
  const path = `/knowledge/relations/${encodeURIComponent(relationId)}`;
  const response = await fetchWithRetry(baseUrl.replace(/\/$/, "") + path, {
    signal: opts.signal,
  });
  if (response.status === 404) return null;
  if (!response.ok) throw await asApiError(response);
  return (await response.json()) as RelationEvidence;
}

/**
 * GET /knowledge/relations/aggregate?source_document_id=X&target_document_id=Y
 *
 * Aggregated doc→doc evidence — returns the top contributing chunk
 * pairs that explain why the projection drew an edge between two
 * documents. Used by the Explorer's relation evidence drawer.
 *
 * Returns ``null`` on 404 — there are no boundary edges between the
 * two documents (e.g. both belong to the same neighborhood and the
 * projection didn't materialise a cross-document link). Callers
 * should treat that the same as "no evidence to show".
 */
export async function getAggregateRelationEvidence(
  sourceDocumentId: string,
  targetDocumentId: string,
  opts: { topN?: number; baseUrl?: string; signal?: AbortSignal } = {},
): Promise<AggregatedRelationEvidence | null> {
  const baseUrl = opts.baseUrl ?? getApiBaseUrl();
  const params = new URLSearchParams();
  params.set("source_document_id", sourceDocumentId);
  params.set("target_document_id", targetDocumentId);
  if (opts.topN !== undefined) params.set("top_n", String(opts.topN));
  const path = `/knowledge/relations/aggregate?${params.toString()}`;
  const response = await fetchWithRetry(baseUrl.replace(/\/$/, "") + path, {
    signal: opts.signal,
  });
  if (response.status === 404) return null;
  if (!response.ok) throw await asApiError(response);
  return (await response.json()) as AggregatedRelationEvidence;
}
