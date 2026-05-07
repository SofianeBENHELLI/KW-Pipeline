/**
 * Thin fetch wrapper for the KW-Pipeline backend.
 *
 * Mirrors the public-error-envelope handling in `apps/web/src/api/client.ts`
 * (#97) so the widget surfaces the same `code` / `retryable` / `remediation`
 * fields the backend already emits. No external HTTP library — just `fetch`.
 *
 * The error class and envelope parser themselves live in
 * ``apps/_shared/api-core`` (audit #227) so a bug fix to envelope
 * handling lands in one place rather than every frontend's copy.
 *
 * Base-URL resolution order:
 *   1. The widget's persisted setting `apiBaseUrl` (set via SettingsPanel).
 *   2. The `KW_API_BASE_URL` env var captured at build time (so a deployed
 *      bundle can ship pointing at a known production host without a
 *      first-run config step).
 *   3. `http://localhost:8000` — the `make demo-api` default.
 */

import { widget } from "@widget-lab/3ddashboard-utils";

import { ApiError, asApiError } from "../../../_shared/api-core";
import type {
  ChatMode,
  ChatResponse,
  ChunkSearchResponse,
  Document,
  DocumentHashCheck,
  DocumentListResponse,
  DocumentVersion,
  Health,
  KnowledgeGraphPage,
} from "./types";

// Re-export from the shared module so existing import sites
// (``import { ApiError } from "./api/client"``) keep working without
// every consumer needing to know about the shared package layout.
//
// ``setSessionTrigger`` / ``clearSessionTrigger`` are surfaced here
// for the same reason: ``<SessionGuardProvider>`` registers its
// ``trigger`` callback through this re-export so the widget root
// keeps importing everything from ``./api/client`` (#83 slice 3).
export {
  ApiError,
  setSessionTrigger,
  clearSessionTrigger,
} from "../../../_shared/api-core";

const SETTINGS_KEY = "apiBaseUrl";
const ORBITAL_URL_SETTINGS_KEY = "orbitalUrl";
const FALLBACK_BASE_URL = "http://localhost:8000";
const FALLBACK_ORBITAL_URL = "http://localhost:5173";

const buildTimeBaseUrl: string | undefined =
  typeof process !== "undefined" && process.env
    ? process.env.KW_API_BASE_URL
    : undefined;
const buildTimeOrbitalUrl: string | undefined =
  typeof process !== "undefined" && process.env
    ? process.env.KW_ORBITAL_URL
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

export function getOrbitalUrl(): string {
  return (
    safeGetWidgetValue(ORBITAL_URL_SETTINGS_KEY) ??
    (buildTimeOrbitalUrl && buildTimeOrbitalUrl.length > 0
      ? buildTimeOrbitalUrl
      : FALLBACK_ORBITAL_URL)
  );
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
 * POST /knowledge/chat (Phase 3 grounded chat / ADR-016).
 *
 * Asks the backend to answer ``question`` grounded in the configured
 * retrieval mode. ``mode`` selects RAG / GraphRAG / Hybrid; ``top_k``
 * bounds the number of vector hits the prompt is grounded in.
 *
 * Returns 503 with ``KW_CHAT_DISABLED`` when any of the three gates
 * (knowledge layer enabled, Anthropic key, Voyage key) is missing;
 * the ``ApiError`` envelope carries the operator-facing remediation
 * copy verbatim.
 */
export function askKnowledgeChat(
  question: string,
  opts: {
    mode?: ChatMode;
    top_k?: number;
    baseUrl?: string;
    signal?: AbortSignal;
  } = {},
): Promise<ChatResponse> {
  const body = {
    question,
    mode: opts.mode ?? "rag",
    top_k: opts.top_k ?? 5,
  };
  return request<ChatResponse>("/knowledge/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    baseUrl: opts.baseUrl,
    signal: opts.signal,
  });
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

/**
 * GET /documents/by-hash/{sha256} (#292)
 *
 * Pre-import duplicate check. Hash the file locally with
 * ``hashFileSha256`` and call this before posting bytes — when
 * ``exists=true``, surface a banner so the operator can decide whether
 * to keep the upload (the backend will tag it ``DUPLICATE_DETECTED``)
 * or skip it entirely without burning bandwidth.
 */
export function checkDocumentHash(
  sha256: string,
  opts: { baseUrl?: string; signal?: AbortSignal } = {},
): Promise<DocumentHashCheck> {
  return request<DocumentHashCheck>(
    `/documents/by-hash/${encodeURIComponent(sha256)}`,
    opts,
  );
}

/**
 * Compute the SHA-256 hex digest of a File using the Web Crypto API.
 * Streams via ``arrayBuffer()`` so peak memory matches the file size,
 * which is acceptable for the widget's drag-and-drop limit (50 MB).
 */
export async function hashFileSha256(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
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
// Local mirror of the shared ``ResponseBodyShape`` type.
//
// ``uploadDocumentWithProgress`` uses XMLHttpRequest (for the upload
// progress events that fetch doesn't expose) and so cannot call
// ``asApiError(response)`` from the shared module — that helper is
// tied to the fetch ``Response`` object. Reconstructing the envelope
// shape here keeps the XHR path's error semantics aligned with the
// fetch path's. A follow-up of audit #227 could expose a
// ``parseEnvelopeJson(text)`` helper in ``apps/_shared/api-core`` so
// even this last branch shares the parser.
interface XhrErrorEnvelope {
  code?: unknown;
  message?: unknown;
  retryable?: unknown;
  remediation?: unknown;
}

interface XhrResponseBodyShape {
  error?: XhrErrorEnvelope;
  detail?: unknown;
}

export function uploadDocumentWithProgress(
  file: File,
  opts: {
    baseUrl?: string;
    signal?: AbortSignal;
    /** Receives a fraction in `[0, 1]`. Called only when total is known. */
    onProgress?: (fraction: number) => void;
    /**
     * Optional workspace-scope query params (EPIC-D #218 / #250).
     *
     * When omitted, the backend auto-fills ``personal:<current_user.id>``
     * via the ``get_current_user`` dependency. Pass both to land the
     * upload into a different scope (e.g. a 3DSwym community). The
     * route reads them as ``?scope_kind=…&scope_ref=…`` query params,
     * not form fields, so they're appended to the URL below.
     */
    scope_kind?: string;
    scope_ref?: string;
  } = {},
): Promise<DocumentVersion> {
  return new Promise<DocumentVersion>((resolve, reject) => {
    const baseUrl = opts.baseUrl ?? getApiBaseUrl();
    const form = new FormData();
    form.append("file", file);

    // Scope params ride on the query string per #250. Only append when
    // the caller actually picked something — letting both stay absent
    // means the backend falls back to ``personal:<current_user.id>``.
    const url = new URL(baseUrl.replace(/\/$/, "") + "/documents/upload");
    if (opts.scope_kind) url.searchParams.set("scope_kind", opts.scope_kind);
    if (opts.scope_ref) url.searchParams.set("scope_ref", opts.scope_ref);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", url.toString());

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
        const body = JSON.parse(xhr.responseText) as XhrResponseBodyShape;
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
