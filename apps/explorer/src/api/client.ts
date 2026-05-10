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
  Document,
  DocumentListResponse,
  Health,
  KnowledgeGraphPage,
  KnowledgeGraphProjection,
  ProjectionStatusResponse,
  RawExtraction,
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

const buildTimeBaseUrl: string | undefined =
  typeof process !== "undefined" && process.env
    ? process.env.KW_API_BASE_URL
    : undefined;

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
  const response = await fetchWithRetry(baseUrl.replace(/\/$/, "") + path, init);
  if (!response.ok) throw await asApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function requestText(
  path: string,
  init: RequestInit & { baseUrl?: string } = {},
): Promise<string> {
  const baseUrl = init.baseUrl ?? getApiBaseUrl();
  const response = await fetchWithRetry(baseUrl.replace(/\/$/, "") + path, init);
  if (!response.ok) throw await asApiError(response);
  return response.text();
}

// ─── Endpoints ───────────────────────────────────────────────────────────────

export function getHealth(opts: { baseUrl?: string; signal?: AbortSignal } = {}): Promise<Health> {
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
  opts: { limit?: number; cursor?: string; baseUrl?: string; signal?: AbortSignal } = {},
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
