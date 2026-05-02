/**
 * Thin fetch wrapper for the KW-Pipeline backend.
 *
 * Mirrors the public-error-envelope handling in `apps/web/src/api/client.ts`
 * (#97) so the widget surfaces the same `code` / `retryable` / `remediation`
 * fields the backend already emits. No external HTTP library — just `fetch`.
 *
 * Base-URL resolution order:
 *   1. The widget's persisted setting `apiBaseUrl` (set via SettingsPanel).
 *   2. The `KW_API_BASE_URL` env var captured at build time (so a deployed
 *      bundle can ship pointing at a known production host without a
 *      first-run config step).
 *   3. `http://localhost:8000` — the `make demo-api` default.
 */

import { widget } from "@widget-lab/3ddashboard-utils";

import type {
  Document,
  DocumentListResponse,
  DocumentVersion,
  Health,
  KnowledgeGraphPage,
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
    // The dashboard host may not have wired up the setting store yet
    // (e.g. when index.html is opened directly for sanity checks).
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

// ─── Core request helper ─────────────────────────────────────────────────────

async function request<T>(
  path: string,
  init: RequestInit & { baseUrl?: string } = {},
): Promise<T> {
  const baseUrl = init.baseUrl ?? getApiBaseUrl();
  const response = await fetch(baseUrl.replace(/\/$/, "") + path, init);
  if (!response.ok) throw await asApiError(response);
  // 204 No Content path — most KW-Pipeline endpoints return JSON, but be
  // defensive in case a future route doesn't.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ─── Endpoints ───────────────────────────────────────────────────────────────

export function getHealth(opts: { baseUrl?: string; signal?: AbortSignal } = {}): Promise<Health> {
  return request<Health>("/health", opts);
}

export function listDocuments(
  opts: { limit?: number; cursor?: string; baseUrl?: string; signal?: AbortSignal } = {},
): Promise<DocumentListResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 25));
  if (opts.cursor) params.set("cursor", opts.cursor);
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

export async function uploadDocument(
  file: File,
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<DocumentVersion> {
  const baseUrl = opts.baseUrl ?? getApiBaseUrl();
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(
    baseUrl.replace(/\/$/, "") + "/documents/upload",
    { method: "POST", body: form, signal: opts.signal },
  );
  if (!response.ok) throw await asApiError(response);
  return (await response.json()) as DocumentVersion;
}
