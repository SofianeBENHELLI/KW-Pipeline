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
  ChunkSearchResponse,
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
  opts: {
    limit?: number;
    cursor?: string;
    /**
     * Filter by latest-version status — repeats on the wire as
     * ``?status=A&status=B``. Backend supports the same shape as the
     * web client (#86); see ``apps/web/src/api/client.ts``.
     */
    status?: string[];
    /**
     * Case-insensitive substring match against ``original_filename``.
     * Trimmed; empty values are dropped so the URL stays clean.
     */
    q?: string;
    baseUrl?: string;
    signal?: AbortSignal;
  } = {},
): Promise<DocumentListResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 25));
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

/**
 * GET /knowledge/search (Phase 3 / ADR-015).
 *
 * Returns top-K chunks ranked by cosine similarity to ``q``. Phase 3
 * is gated server-side: when ``KW_KNOWLEDGE_LAYER_ENABLED`` is off or
 * ``VOYAGE_API_KEY`` is unset, the backend returns 503 with the
 * ``KW_VECTOR_SEARCH_DISABLED`` envelope code and the operator-facing
 * remediation copy. The widget surfaces both verbatim.
 */
export function searchKnowledgeChunks(
  q: string,
  opts: { limit?: number; baseUrl?: string; signal?: AbortSignal } = {},
): Promise<ChunkSearchResponse> {
  const params = new URLSearchParams();
  params.set("q", q);
  params.set("limit", String(opts.limit ?? 10));
  return request<ChunkSearchResponse>(
    `/knowledge/search?${params.toString()}`,
    { baseUrl: opts.baseUrl, signal: opts.signal },
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

/**
 * Upload variant that reports byte-level progress via a callback.
 * Uses `XMLHttpRequest` because the Fetch API still does not expose
 * upload progress in the browsers the dashboard targets. The error
 * envelope handling stays consistent with `uploadDocument` — same
 * shape, same `ApiError` instances on failure.
 */
export function uploadDocumentWithProgress(
  file: File,
  opts: {
    baseUrl?: string;
    signal?: AbortSignal;
    /** Receives a fraction in `[0, 1]`. Called only when total is known. */
    onProgress?: (fraction: number) => void;
  } = {},
): Promise<DocumentVersion> {
  return new Promise<DocumentVersion>((resolve, reject) => {
    const baseUrl = opts.baseUrl ?? getApiBaseUrl();
    const form = new FormData();
    form.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", baseUrl.replace(/\/$/, "") + "/documents/upload");

    if (opts.onProgress) {
      xhr.upload.addEventListener("progress", (evt) => {
        if (!evt.lengthComputable || evt.total === 0) return;
        opts.onProgress?.(evt.loaded / evt.total);
      });
    }

    xhr.addEventListener("load", () => {
      const ok = xhr.status >= 200 && xhr.status < 300;
      if (ok) {
        try {
          resolve(JSON.parse(xhr.responseText) as DocumentVersion);
        } catch (err) {
          reject(new ApiError(xhr.status, "Invalid JSON in upload response"));
        }
        return;
      }
      // Reconstruct an `ApiError` with the same envelope semantics as
      // `asApiError`. Hand-parsed because XHR doesn't return a `Response`.
      let detail = xhr.statusText;
      let code = "KW_HTTP_ERROR";
      let retryable = false;
      let remediation: string | null = null;
      try {
        const body = JSON.parse(xhr.responseText) as ResponseBodyShape;
        if (typeof body?.detail === "string") detail = body.detail;
        const env = body?.error;
        if (env) {
          if (typeof env.code === "string" && env.code.length > 0) code = env.code;
          if (env.retryable === true) retryable = true;
          if (typeof env.remediation === "string" && env.remediation.length > 0) {
            remediation = env.remediation;
          }
          if (typeof env.message === "string" && env.message.length > 0) {
            detail = env.message;
          }
        }
      } catch {
        // Non-JSON body — fall through with statusText.
      }
      reject(new ApiError(xhr.status, detail, code, retryable, remediation));
    });
    xhr.addEventListener("error", () => {
      reject(new ApiError(0, "Network error during upload", "KW_NETWORK_ERROR", true));
    });
    xhr.addEventListener("abort", () => {
      const err = new Error("Aborted");
      err.name = "AbortError";
      reject(err);
    });

    if (opts.signal) {
      if (opts.signal.aborted) {
        xhr.abort();
      } else {
        opts.signal.addEventListener("abort", () => xhr.abort(), { once: true });
      }
    }

    xhr.send(form);
  });
}

/**
 * Health probe that also returns the round-trip latency in milliseconds,
 * measured client-side via `performance.now()`. The latency is shown in
 * the health card and the settings panel reachability metadata.
 */
export async function getHealthWithLatency(
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<{ health: Health; latencyMs: number }> {
  const start = performance.now();
  const health = await getHealth(opts);
  const latencyMs = Math.round(performance.now() - start);
  return { health, latencyMs };
}
