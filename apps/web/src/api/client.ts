/**
 * Harvester API client — typed wrapper over `openapi-fetch`.
 *
 * Path strings, methods, path parameters, and request/response shapes are
 * all enforced at compile time against `generated/schema.ts`. To wire a
 * new endpoint, add it to the FastAPI app, regenerate types (see
 * `docs/workflows/openapi_codegen.md`), and add a thin function below.
 *
 * The base URL comes from VITE_API_BASE_URL at build time, with a
 * sensible local-dev fallback. No external HTTP library beyond the tiny
 * `openapi-fetch` package is used — it's a thin layer over native fetch.
 */

import createClient from "openapi-fetch";

import type { paths } from "./generated/schema";
import type {
  ApiChunkSearchResponse,
  ApiDocument,
  ApiDocumentVersion,
  ApiKnowledgeGraphPage,
  ApiKnowledgeGraphProjection,
  ApiRawExtraction,
  ApiSemanticDocument,
  ApiUploadResponse,
  ListDocumentsResponse,
} from "./types";

// ─── Base URL + transport ────────────────────────────────────────────────────

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// Delegate to `globalThis.fetch` at call time (rather than letting
// openapi-fetch capture a reference at construction). This keeps test
// spies on `globalThis.fetch` effective even though the client is
// created at module load.
const http = createClient<paths>({
  baseUrl: BASE_URL,
  fetch: (...args) => globalThis.fetch(...args),
});

// ─── Errors ──────────────────────────────────────────────────────────────────

/**
 * Public error envelope from the API (#97). The backend always wraps
 * non-OK responses in `{ error: { code, message, status, retryable,
 * remediation }, detail }` — see `apps/api/app/errors.py`.
 *
 * `ApiError` mirrors the public fields onto a JS Error subclass so call
 * sites can `throw err`, `if (err instanceof ApiError)`, and read the
 * structured fields without re-parsing the response.
 */
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
  status?: unknown;
  retryable?: unknown;
  remediation?: unknown;
}

interface ResponseBodyShape {
  error?: ErrorEnvelope;
  detail?: unknown;
}

function fieldsFromBody(
  body: ResponseBodyShape | null,
  fallbackDetail: string,
): {
  detail: string;
  code: string;
  retryable: boolean;
  remediation: string | null;
} {
  let detail = fallbackDetail;
  if (typeof body?.detail === "string") detail = body.detail;

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
  // Prefer the envelope's `message` over a list-shaped `detail` (the
  // RequestValidationError path emits `detail: [...errors]`).
  if (typeof env?.message === "string" && env.message.length > 0) {
    detail = env.message;
  }
  return { detail, code, retryable, remediation };
}

/**
 * Build an ApiError from a non-OK fetch Response. Used by call sites that
 * bypass openapi-fetch (the multipart upload path).
 */
async function asApiError(response: Response): Promise<ApiError> {
  let body: ResponseBodyShape | null = null;
  try {
    body = (await response.clone().json()) as ResponseBodyShape;
  } catch {
    // Non-JSON or empty body — fall through to statusText.
  }
  const { detail, code, retryable, remediation } = fieldsFromBody(
    body,
    response.statusText,
  );
  return new ApiError(response.status, detail, code, retryable, remediation);
}

/**
 * Unwrap a typed openapi-fetch `{ data, error, response }` result.
 *
 * On success, return `data`. On failure, build an `ApiError` from the
 * already-parsed `error` body (openapi-fetch consumes the response stream,
 * so we can't re-read it). Pulls the public envelope fields (`code`,
 * `retryable`, `remediation`) when present; falls back to status-derived
 * defaults otherwise.
 */
function unwrap<T>(result: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (result.data !== undefined) return result.data;
  const { response, error } = result;
  // openapi-fetch parsed the JSON for us — `error` is the body shape.
  const body =
    error && typeof error === "object"
      ? (error as ResponseBodyShape)
      : null;
  const { detail, code, retryable, remediation } = fieldsFromBody(
    body,
    typeof error === "string" && error.length > 0
      ? error
      : response.statusText,
  );
  throw new ApiError(response.status, detail, code, retryable, remediation);
}

// ─── Document endpoints ──────────────────────────────────────────────────────

/**
 * GET /documents
 *
 * Returns one page of catalog entries. Pass ``cursor`` to advance pages.
 *
 * Optional filters introduced by #86:
 *   - ``status``: array of ``DocumentVersionStatus`` strings to filter
 *     by the document's *latest version* status. Repeatable on the
 *     wire (FastAPI handles list-of-strings query params natively).
 *     Example: ``listDocuments({ status: ["NEEDS_REVIEW", "FAILED"] })``.
 *   - ``q``: case-insensitive substring match against
 *     ``original_filename``. Empty / whitespace-only strings act as
 *     "no filter" server-side, but we still drop them client-side so
 *     the URL stays clean.
 *
 * Filters apply *before* pagination — the cursor's semantics are
 * "next page within the current filter set". A different filter
 * combination requires dropping the cursor.
 */
export interface ListDocumentsOptions {
  limit?: number;
  cursor?: string;
  status?: string[];
  q?: string;
}

export async function listDocuments(
  options: ListDocumentsOptions = {},
): Promise<ListDocumentsResponse> {
  const { limit = 50, cursor, status, q } = options;
  const query: Record<string, string | number | string[]> = { limit };
  if (cursor) query.cursor = cursor;
  if (status && status.length > 0) query.status = status;
  const trimmedQ = q?.trim() ?? "";
  if (trimmedQ.length > 0) query.q = trimmedQ;
  return unwrap(
    await http.GET("/documents", {
      params: { query: query as never },
    }),
  );
}

/**
 * GET /documents/{document_id}
 * Returns a single document with all its versions.
 */
export async function getDocument(
  documentId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiDocument> {
  return unwrap(
    await http.GET("/documents/{document_id}", {
      params: { path: { document_id: documentId } },
      signal: options.signal,
    }),
  );
}

/**
 * POST /documents/upload
 * Streams a file to the backend and returns the created DocumentVersion.
 *
 * NOTE: openapi-fetch's typed body helpers don't model multipart/form-data
 * bodies cleanly, so we drop down to native fetch here. Path and response
 * shape stay pinned via the imported response type.
 */
export async function uploadDocument(
  file: File,
  documentId?: string,
): Promise<ApiUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  if (documentId) form.append("document_id", documentId);
  const response = await fetch(`${BASE_URL}/documents/upload`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw await asApiError(response);
  return (await response.json()) as ApiUploadResponse;
}

// ─── Version endpoints ───────────────────────────────────────────────────────

/**
 * GET /documents/{document_id}/versions/{version_id}
 *
 * NOTE: The backend does not currently expose a dedicated single-version
 * route. Callers that need a single version should use getDocument() and
 * filter locally.
 *
 * @throws {Error} "not yet implemented" to make the gap visible at runtime.
 */
export function getVersion(
  _documentId: string,
  _versionId: string,
): Promise<ApiDocumentVersion> {
  return Promise.reject(
    new Error(
      "getVersion: GET /documents/{id}/versions/{vid} is not yet implemented by the backend. " +
        "Use getDocument() and filter versions locally.",
    ),
  );
}

// ─── Extraction endpoints ────────────────────────────────────────────────────

/**
 * POST /documents/{document_id}/versions/{version_id}/extract
 * Triggers raw extraction for a stored document version.
 */
export async function extractVersion(
  documentId: string,
  versionId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiRawExtraction> {
  return unwrap(
    await http.POST("/documents/{document_id}/versions/{version_id}/extract", {
      params: { path: { document_id: documentId, version_id: versionId } },
      signal: options.signal,
    }),
  );
}

/**
 * GET /documents/{document_id}/versions/{version_id}/extraction
 * Returns cached raw extraction JSON.
 */
export async function getExtraction(
  documentId: string,
  versionId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiRawExtraction> {
  return unwrap(
    await http.GET("/documents/{document_id}/versions/{version_id}/extraction", {
      params: { path: { document_id: documentId, version_id: versionId } },
      signal: options.signal,
    }),
  );
}

// ─── Semantic endpoints ──────────────────────────────────────────────────────

/**
 * POST /documents/{document_id}/versions/{version_id}/semantic
 * Generates (or returns cached) semantic output.
 */
export async function generateSemantic(
  documentId: string,
  versionId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiSemanticDocument> {
  return unwrap(
    await http.POST("/documents/{document_id}/versions/{version_id}/semantic", {
      params: { path: { document_id: documentId, version_id: versionId } },
      signal: options.signal,
    }),
  );
}

/**
 * GET /documents/{document_id}/versions/{version_id}/semantic
 * Returns cached semantic JSON.
 */
export async function getSemantic(
  documentId: string,
  versionId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiSemanticDocument> {
  return unwrap(
    await http.GET("/documents/{document_id}/versions/{version_id}/semantic", {
      params: { path: { document_id: documentId, version_id: versionId } },
      signal: options.signal,
    }),
  );
}

/**
 * GET /documents/{document_id}/versions/{version_id}/markdown
 * Returns generated Markdown as plain text.
 *
 * NOTE: openapi-fetch defaults to JSON parsing, so we use a custom parser
 * here to read the response body as text.
 */
export async function getMarkdown(
  documentId: string,
  versionId: string,
): Promise<string> {
  const result = await http.GET(
    "/documents/{document_id}/versions/{version_id}/markdown",
    {
      params: { path: { document_id: documentId, version_id: versionId } },
      parseAs: "text",
    },
  );
  return unwrap(result as { data?: string; error?: unknown; response: Response });
}

// ─── Review endpoints ─────────────────────────────────────────────────────────

/**
 * POST /documents/{document_id}/versions/{version_id}/validate
 */
export async function validateVersion(
  documentId: string,
  versionId: string,
  reviewerNote?: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiSemanticDocument> {
  return unwrap(
    await http.POST("/documents/{document_id}/versions/{version_id}/validate", {
      params: { path: { document_id: documentId, version_id: versionId } },
      body: { reviewer_note: reviewerNote ?? null },
      signal: options.signal,
    }),
  );
}

/**
 * POST /documents/{document_id}/versions/{version_id}/reject
 */
export async function rejectVersion(
  documentId: string,
  versionId: string,
  reviewerNote?: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiSemanticDocument> {
  return unwrap(
    await http.POST("/documents/{document_id}/versions/{version_id}/reject", {
      params: { path: { document_id: documentId, version_id: versionId } },
      body: { reviewer_note: reviewerNote ?? null },
      signal: options.signal,
    }),
  );
}

// ─── Knowledge graph endpoints ───────────────────────────────────────────────

/**
 * GET /documents/{document_id}/graph
 * Returns the knowledge-graph projection (nodes + edges) written on the
 * most recent VALIDATED transition for this document family.
 */
export async function getDocumentGraph(
  documentId: string,
): Promise<ApiKnowledgeGraphProjection> {
  return unwrap(
    await http.GET("/documents/{document_id}/graph", {
      params: { path: { document_id: documentId } },
    }),
  );
}

/**
 * GET /knowledge/graph
 * Cursor-paginated walk of every projected document. Pass `cursor` to
 * advance pages; `next_cursor === null` marks the end.
 */
export async function getKnowledgeGraph(
  limit = 50,
  cursor?: string,
): Promise<ApiKnowledgeGraphPage> {
  return unwrap(
    await http.GET("/knowledge/graph", {
      params: { query: { limit, ...(cursor ? { cursor } : {}) } },
    }),
  );
}

/**
 * GET /knowledge/search (Phase 3 / ADR-015)
 *
 * Top-K cosine-similarity search over the projected chunk embeddings.
 * Returns 503 with ``KW_VECTOR_SEARCH_DISABLED`` when Phase 3 is off
 * (no Voyage key); the route's :class:`ApiError` envelope carries the
 * remediation copy the UI should render verbatim.
 */
export async function searchKnowledgeChunks(
  q: string,
  options: { limit?: number; signal?: AbortSignal } = {},
): Promise<ApiChunkSearchResponse> {
  const { limit = 10, signal } = options;
  return unwrap(
    await http.GET("/knowledge/search", {
      params: { query: { q, limit } },
      signal,
    }),
  );
}
