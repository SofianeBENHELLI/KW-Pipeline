/**
 * Thin fetch wrapper for the KW-Pipeline backend, scoped to the
 * Knowledge Explorer's read-only surface.
 *
 * Mirrors apps/widget/src/api/client.ts for envelope handling and base-URL
 * resolution (widget-store > build-time env > localhost fallback). The two
 * widgets intentionally duplicate this small layer rather than depending
 * on each other — keeps each tile self-contained on the dashboard host.
 */

import { widget } from "@widget-lab/3ddashboard-utils";

import type {
  Document,
  DocumentListResponse,
  Health,
  KnowledgeGraphPage,
  KnowledgeGraphProjection,
  RawExtraction,
  SemanticDocument,
} from "./types";

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

// ─── Errors ──────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly code: string = "KW_HTTP_ERROR",
    public readonly retryable: boolean = false,
    public readonly remediation: string | null = null,
  ) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

interface ErrorEnvelope {
  code?: unknown;
  message?: unknown;
  retryable?: unknown;
  remediation?: unknown;
}

interface ResponseBodyShape {
  error?: ErrorEnvelope;
  detail?: unknown;
}

async function asApiError(response: Response): Promise<ApiError> {
  let body: ResponseBodyShape | null = null;
  try {
    body = (await response.clone().json()) as ResponseBodyShape;
  } catch {
    // Non-JSON or empty body.
  }
  let detail =
    typeof body?.detail === "string" ? body.detail : response.statusText;
  const env = body?.error;
  const code =
    typeof env?.code === "string" && env.code.length > 0
      ? env.code
      : "KW_HTTP_ERROR";
  const retryable = env?.retryable === true;
  const remediation =
    typeof env?.remediation === "string" && env.remediation.length > 0
      ? env.remediation
      : null;
  if (typeof env?.message === "string" && env.message.length > 0) {
    detail = env.message;
  }
  return new ApiError(response.status, detail, code, retryable, remediation);
}

// ─── Core request helpers ────────────────────────────────────────────────────

async function request<T>(
  path: string,
  init: RequestInit & { baseUrl?: string } = {},
): Promise<T> {
  const baseUrl = init.baseUrl ?? getApiBaseUrl();
  const response = await fetch(baseUrl.replace(/\/$/, "") + path, init);
  if (!response.ok) throw await asApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function requestText(
  path: string,
  init: RequestInit & { baseUrl?: string } = {},
): Promise<string> {
  const baseUrl = init.baseUrl ?? getApiBaseUrl();
  const response = await fetch(baseUrl.replace(/\/$/, "") + path, init);
  if (!response.ok) throw await asApiError(response);
  return response.text();
}

// ─── Endpoints ───────────────────────────────────────────────────────────────

export function getHealth(opts: { baseUrl?: string; signal?: AbortSignal } = {}): Promise<Health> {
  return request<Health>("/health", opts);
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
