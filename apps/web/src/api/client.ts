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

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

/**
 * Build an ApiError from a non-OK fetch Response. Used by call sites that
 * bypass openapi-fetch (the multipart upload path).
 */
async function asApiError(response: Response): Promise<ApiError> {
  let detail = response.statusText;
  try {
    const body = (await response.clone().json()) as { detail?: string };
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // Non-JSON or empty body — keep the statusText fallback.
  }
  return new ApiError(response.status, detail);
}

/**
 * Unwrap a typed openapi-fetch `{ data, error, response }` result.
 *
 * On success, return `data`. On failure, build an `ApiError` from the
 * already-parsed `error` body (openapi-fetch consumes the response stream,
 * so we can't re-read it) and surface the FastAPI `detail` when present.
 * Falls back to `response.statusText` for non-JSON error bodies.
 */
function unwrap<T>(result: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (result.data !== undefined) return result.data;
  const { response, error } = result;
  let detail = response.statusText;
  if (error && typeof error === "object" && "detail" in error) {
    const candidate = (error as { detail?: unknown }).detail;
    if (typeof candidate === "string") detail = candidate;
  } else if (typeof error === "string" && error.length > 0) {
    detail = error;
  }
  throw new ApiError(response.status, detail);
}

// ─── Document endpoints ──────────────────────────────────────────────────────

/**
 * GET /documents
 * Returns one page of catalog entries. Pass `cursor` to advance pages.
 */
export async function listDocuments(
  limit = 50,
  cursor?: string,
): Promise<ListDocumentsResponse> {
  return unwrap(
    await http.GET("/documents", {
      params: { query: { limit, ...(cursor ? { cursor } : {}) } },
    }),
  );
}

/**
 * GET /documents/{document_id}
 * Returns a single document with all its versions.
 */
export async function getDocument(documentId: string): Promise<ApiDocument> {
  return unwrap(
    await http.GET("/documents/{document_id}", {
      params: { path: { document_id: documentId } },
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
): Promise<ApiRawExtraction> {
  return unwrap(
    await http.POST("/documents/{document_id}/versions/{version_id}/extract", {
      params: { path: { document_id: documentId, version_id: versionId } },
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
): Promise<ApiRawExtraction> {
  return unwrap(
    await http.GET("/documents/{document_id}/versions/{version_id}/extraction", {
      params: { path: { document_id: documentId, version_id: versionId } },
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
): Promise<ApiSemanticDocument> {
  return unwrap(
    await http.POST("/documents/{document_id}/versions/{version_id}/semantic", {
      params: { path: { document_id: documentId, version_id: versionId } },
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
): Promise<ApiSemanticDocument> {
  return unwrap(
    await http.GET("/documents/{document_id}/versions/{version_id}/semantic", {
      params: { path: { document_id: documentId, version_id: versionId } },
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
): Promise<ApiSemanticDocument> {
  return unwrap(
    await http.POST("/documents/{document_id}/versions/{version_id}/validate", {
      params: { path: { document_id: documentId, version_id: versionId } },
      body: { reviewer_note: reviewerNote ?? null },
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
): Promise<ApiSemanticDocument> {
  return unwrap(
    await http.POST("/documents/{document_id}/versions/{version_id}/reject", {
      params: { path: { document_id: documentId, version_id: versionId } },
      body: { reviewer_note: reviewerNote ?? null },
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
