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

import { withRetry } from "../../../_shared/api-core";

import type { paths } from "./generated/schema";
import type {
  ApiAdminAuditEventsResponse,
  ApiAdminHITLStateResponse,
  ApiArchivedDocumentsResponse,
  ApiAutoPromoteResult,
  ApiBatchUploadResult,
  ApiChatMode,
  ApiChatResponse,
  ApiChunkSearchResponse,
  ApiConceptSuggestion,
  ApiConceptSuggestionState,
  ApiDocument,
  ApiDocumentHashCheck,
  ApiDocumentVersion,
  ApiKnowledgeGraphPage,
  ApiKnowledgeGraphProjection,
  ApiProjectionStatusResponse,
  ApiOrbitalPurgeAllResponse,
  ApiOrbitalPurgeDocumentResponse,
  ApiPurgeArtifactsResponse,
  ApiExtractionJobSnapshot,
  ApiPurgeBatchResponse,
  ApiRawExtraction,
  ApiRelinkScopeRequest,
  ApiRelinkScopeResponse,
  ApiSemanticDocument,
  ApiTaxonomyResponse,
  ApiTaxonomyState,
  ApiTaxonomyVersion,
  ApiTaxonomyVersionListResponse,
  ApiUnarchiveResponse,
  ApiUploadResponse,
  ListDocumentsResponse,
} from "./types";

// ─── Base URL + transport ────────────────────────────────────────────────────

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

/** Resolved API base URL — exposed so the Settings surface can show
 *  what's actually being targeted at runtime. Build-time only; the
 *  web app does not let users mutate it (unlike the widget tile). */
export function getApiBaseUrl(): string {
  return BASE_URL;
}

// Delegate to `globalThis.fetch` at call time (rather than letting
// openapi-fetch capture a reference at construction). This keeps test
// spies on `globalThis.fetch` effective even though the client is
// created at module load.
//
// ``withRetry`` transparently re-issues idempotent requests (GET / HEAD)
// on transient backend hiccups (502/503/504 + network errors). The
// defaults are conservative — POST/PUT/PATCH/DELETE never retry — so
// dropping it in front of the existing fetch can only improve
// behaviour, never make it worse. See ``apps/_shared/api-core/retryFetch.ts``.
const fetchWithRetry = withRetry((...args) => globalThis.fetch(...args));
const http = createClient<paths>({
  baseUrl: BASE_URL,
  fetch: fetchWithRetry,
});

// ─── 401 / session-expired hook (#83 slice 3) ────────────────────────────────

/**
 * Module-level callback the SessionGuardProvider registers in a
 * ``useEffect`` so this fetch wrapper can flip the banner on when
 * an :class:`ApiError` with ``status === 401`` is constructed.
 *
 * The seam is intentionally tiny: a single setter, a single call
 * site, and a no-op default so this client keeps working in unit
 * tests that don't mount the provider. ADR-019 §5 mandates the
 * envelope; this is the JS-side hook that turns it into UX.
 *
 * Limitation: the default ``KW_AUTH_MODE=dev`` (per #245) never
 * returns 401 in normal operation, so the hook is exercised via
 * vitest mocks and the ``#force-session-expired`` URL-hash dev
 * stub installed at the app root.
 */
type SessionTrigger = () => void;
let sessionTrigger: SessionTrigger = () => {
  // No-op until the provider registers a real one. Mirrors the
  // useSessionGuard default, so the API client stays usable
  // outside the React tree (codegen smoke tests, node scripts).
};

/**
 * Register the callback that flips the session-expired banner on.
 *
 * Called once from ``<SessionGuardProvider>`` inside a useEffect —
 * the registration happens before any user-driven request, because
 * the provider sits at the app root above every component that
 * fetches.
 */
export function setSessionTrigger(fn: SessionTrigger): void {
  sessionTrigger = fn;
}

/**
 * Reset the registered trigger back to the default no-op. Tests use
 * this between cases so a 401 in one test doesn't leak into the next.
 */
export function clearSessionTrigger(): void {
  sessionTrigger = () => {
    /* no-op */
  };
}

// ─── Errors ──────────────────────────────────────────────────────────────────

/**
 * Public error envelope from the API (#97). The backend always wraps
 * non-OK responses in `{ error: { code, message, status, retryable,
 * remediation }, detail }` — see `apps/api/app/errors.py`.
 *
 * `ApiError` mirrors the public fields onto a JS Error subclass so call
 * sites can `throw err`, `if (err instanceof ApiError)`, and read the
 * structured fields without re-parsing the response.
 *
 * Constructing an ApiError with ``status === 401`` ALWAYS fires the
 * session-expired trigger (#83 slice 3 / ADR-019 §5). The trigger is
 * a no-op until ``<SessionGuardProvider>`` registers its setter, so
 * unit tests that don't mount the provider stay quiet.
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
    // Fire the registered session-expired hook for any 401. Catches
    // every code path that builds an ApiError — openapi-fetch's
    // ``unwrap`` cascade, the multipart upload's ``asApiError``
    // branch, and any future helper that throws an ApiError directly.
    if (status === 401) {
      try {
        sessionTrigger();
      } catch {
        // Trigger callbacks are React state setters — they shouldn't
        // throw, but defend so a buggy register doesn't take down
        // unrelated request handling.
      }
    }
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
/**
 * GET /documents/by-hash/{sha256} (#292)
 *
 * Pre-import duplicate check. The Forge widget hashes the picked file
 * locally and calls this before posting bytes — when ``exists=true``
 * we surface a duplicate banner and let the operator decide whether
 * to skip or proceed.
 */
export async function checkDocumentHash(
  sha256: string,
): Promise<ApiDocumentHashCheck> {
  const { data, error, response } = await http.GET("/documents/by-hash/{sha256}", {
    params: { path: { sha256 } },
  });
  if (error !== undefined || data === undefined) throw await asApiError(response);
  return data;
}

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

/**
 * POST /documents/upload/batch (#82)
 *
 * Multipart upload of N files in a single request. The route never
 * raises on per-file failure — it returns a structured report with
 * one ``BatchUploadOutcome`` per attached file. Clients route on the
 * ``status`` discriminant for each outcome and on the aggregate
 * counters in ``summary``.
 *
 * NOTE: openapi-fetch's typed body helpers don't model
 * ``multipart/form-data`` request bodies cleanly, so we drop down to
 * native fetch here. Path and response shape stay pinned via the
 * imported response type.
 */
export async function uploadDocumentsBatch(
  files: File[],
  options: { signal?: AbortSignal } = {},
): Promise<ApiBatchUploadResult> {
  if (files.length === 0) {
    throw new ApiError(
      400,
      "No files attached. Include at least one file.",
      "KW_UPLOAD_EMPTY",
    );
  }
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  const response = await fetch(`${BASE_URL}/documents/upload/batch`, {
    method: "POST",
    body: form,
    signal: options.signal,
  });
  if (!response.ok) throw await asApiError(response);
  return (await response.json()) as ApiBatchUploadResult;
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
 *
 * Returns ``ApiRawExtraction`` (HTTP 200) when the API runs extraction
 * inline (the default, ``KW_EXTRACTION_INLINE=true``) and an
 * ``ApiExtractionJobSnapshot`` (HTTP 202) when extraction is queued
 * (``KW_EXTRACTION_INLINE=false``, ADR-006 PR-2). PR-3 will flip the
 * default; the polling UI lands in a follow-up — for now the type
 * just documents the contract.
 */
export async function extractVersion(
  documentId: string,
  versionId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiRawExtraction | ApiExtractionJobSnapshot> {
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

// ─── PDF viewer chunk locations (Phase 2 of the PDF-viewer plan) ───────────

import type { ApiChunkLocationsResponse, ApiChunkSource } from "./types";

/**
 * GET /documents/{document_id}/versions/{version_id}/chunks
 *
 * Returns the chunk-location catalog for the PDF viewer's split-pane —
 * one row per parser-emitted section with normalised rects, page,
 * snippet, and topic-derived summary signal.
 *
 * Filters mirror the route's query params (page, source, min_confidence);
 * limit defaults to the server-side ceiling so the viewer typically
 * fetches every chunk in one round-trip.
 */
export async function listDocumentChunks(
  documentId: string,
  versionId: string,
  options: {
    page?: number;
    source?: ApiChunkSource;
    minConfidence?: number;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<ApiChunkLocationsResponse> {
  return unwrap(
    await http.GET("/documents/{document_id}/versions/{version_id}/chunks", {
      params: {
        path: { document_id: documentId, version_id: versionId },
        query: {
          page: options.page,
          source: options.source,
          min_confidence: options.minConfidence,
          limit: options.limit,
        },
      },
      signal: options.signal,
    }),
  );
}

// ─── Semantic endpoints ──────────────────────────────────────────────────────

/**
 * POST /documents/{document_id}/versions/{version_id}/semantic
 * Generates (or returns cached) semantic output.
 *
 * ``method`` selects the semantic-generation strategy. Omit (or pass
 * ``undefined``) for the runtime default (``structure_first`` —
 * Method 1). Passing ``"semantic_intelligence"`` (Method 2) or
 * ``"knowledge_graph"`` (Method 3) runs the instructor-driven
 * generators when a provider key is configured; an unknown id
 * returns 400.
 */
export async function generateSemantic(
  documentId: string,
  versionId: string,
  options: { signal?: AbortSignal; method?: string } = {},
): Promise<ApiSemanticDocument> {
  return unwrap(
    await http.POST("/documents/{document_id}/versions/{version_id}/semantic", {
      params: {
        path: { document_id: documentId, version_id: versionId },
        ...(options.method ? { query: { method: options.method } } : {}),
      },
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

/**
 * POST /documents/{document_id}/versions/{version_id}/reset_to_review
 *
 * Reviewer-override demote: drives a VALIDATED or REJECTED version
 * back to NEEDS_REVIEW so the team can re-open the file when new
 * information surfaces. Backend audit event ``review.demoted``
 * carries the actor + reviewer note. Returns the persisted
 * SemanticDocument with ``validation_status="needs_review"``.
 */
export async function resetVersionToReview(
  documentId: string,
  versionId: string,
  reviewerNote?: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiSemanticDocument> {
  return unwrap(
    await http.POST(
      "/documents/{document_id}/versions/{version_id}/reset_to_review",
      {
        params: { path: { document_id: documentId, version_id: versionId } },
        body: { reviewer_note: reviewerNote ?? null },
        signal: options.signal,
      },
    ),
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
 * GET /knowledge/projection_status/{version_id}
 *
 * Returns the in-process tracker entry for a version's knowledge-layer
 * projection (graph + entity extraction). The reviewer UI polls this
 * after validate to know when the graph is fully populated; a
 * ``"COMPLETED"`` / ``"FAILED"`` response stops the poll loop.
 *
 * Returns ``null`` on 404 — either projection never ran (knowledge
 * layer disabled) or the entry was pruned by the TTL. Both are
 * "fall back to whatever the graph endpoint returns directly", which
 * is the historical contract for clients that don't poll status.
 */
export async function getProjectionStatus(
  versionId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiProjectionStatusResponse | null> {
  const result = await http.GET(
    "/knowledge/projection_status/{version_id}",
    {
      params: { path: { version_id: versionId } },
      signal: options.signal,
    },
  );
  if (result.response.status === 404) return null;
  return unwrap(result);
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

/**
 * POST /knowledge/chat (Phase 3 grounded chat surface)
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
export async function askKnowledgeChat(
  question: string,
  options: {
    mode?: ApiChatMode;
    top_k?: number;
    signal?: AbortSignal;
  } = {},
): Promise<ApiChatResponse> {
  const { mode = "rag", top_k = 5, signal } = options;
  return unwrap(
    await http.POST("/knowledge/chat", {
      body: { question, mode, top_k },
      signal,
    }),
  );
}

// ─── Admin / Archive (D.9 admin UI) ──────────────────────────────────────────

/**
 * GET /admin/archive/archived_documents
 *
 * Paginated walk of flag-archived documents (``archived_at IS NOT NULL``)
 * sorted ``archived_at DESC``. Returns 403 ``KW_FORBIDDEN`` when the
 * caller lacks the ``admin`` role — the UI uses that as its sole
 * "is the user an admin?" probe (we never derive role client-side).
 */
export async function listArchivedDocuments(
  options: { cursor?: string; limit?: number; signal?: AbortSignal } = {},
): Promise<ApiArchivedDocumentsResponse> {
  const { cursor, limit = 50, signal } = options;
  const query: Record<string, string | number> = { limit };
  if (cursor) query.cursor = cursor;
  return unwrap(
    await http.GET("/admin/archive/archived_documents", {
      params: { query: query as never },
      signal,
    }),
  );
}

/**
 * POST /admin/archive/unarchive
 *
 * Admin-only. Clears ``archived_at`` so the document reappears on the
 * standard read path. ``?confirm=true`` is REQUIRED for the real
 * mutation (defence in depth — ADR-027 §5). 403 if non-admin.
 */
export async function unarchiveDocument(
  documentId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiUnarchiveResponse> {
  return unwrap(
    await http.POST("/admin/archive/unarchive", {
      params: { query: { confirm: true } },
      body: { document_id: documentId },
      signal: options.signal,
    }),
  );
}

/**
 * POST /admin/archive/purge_artifacts
 *
 * Admin-only. Hard-deletes the document's source artifacts; the
 * catalog row is preserved as an audit trace per the no-delete
 * policy. ``?dry_run=true`` returns the impact preview without
 * mutating state — the UI uses that for the confirmation modal.
 * ``?confirm=true`` flips the real mutation; the route rejects
 * passing both.
 */
export async function purgeArtifacts(
  documentId: string,
  options: { dryRun?: boolean; signal?: AbortSignal } = {},
): Promise<ApiPurgeArtifactsResponse> {
  const dryRun = options.dryRun ?? false;
  // ``confirm`` and ``dry_run`` are mutually exclusive — the route
  // 400s if both are set. Keep the boolean inversion local so call
  // sites just pick "preview vs real".
  const query = dryRun ? { dry_run: true } : { confirm: true };
  return unwrap(
    await http.POST("/admin/archive/purge_artifacts", {
      params: { query },
      body: { document_id: documentId },
      signal: options.signal,
    }),
  );
}

/**
 * POST /admin/orbital/purge_document (#292)
 *
 * Admin-only. Combined archive + purge_artifacts + KG cleanup in a
 * single audited call. ``confirmation_filename`` MUST equal the
 * target document's ``original_filename``; the route 422s on
 * mismatch so a misclick can't take the wrong family. Emits an
 * ``orbital.document.purge`` audit event on success.
 */
export async function orbitalPurgeDocument(
  documentId: string,
  confirmationFilename: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiOrbitalPurgeDocumentResponse> {
  return unwrap(
    await http.POST("/admin/orbital/purge_document", {
      params: { query: { confirm: true } },
      body: {
        document_id: documentId,
        confirmation_filename: confirmationFilename,
      },
      signal: options.signal,
    }),
  );
}

/**
 * POST /admin/orbital/purge_all (#292 — bulk override)
 *
 * Admin-only. Hard-deletes every active document in the catalog in
 * one audited cascade. Two gates: ``?confirm=true`` and a
 * ``confirmation_phrase`` body field that must equal
 * ``ORBITAL_PURGE_ALL_PHRASE`` (case-sensitive). Emits
 * ``orbital.knowledge_space.purge`` plus one
 * ``orbital.document.purge`` per purged row.
 */
export async function orbitalPurgeAll(
  confirmationPhrase: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiOrbitalPurgeAllResponse> {
  return unwrap(
    await http.POST("/admin/orbital/purge_all", {
      params: { query: { confirm: true } },
      body: { confirmation_phrase: confirmationPhrase },
      signal: options.signal,
    }),
  );
}

/**
 * GET /admin/hitl/state (#215, EPIC-A close-out)
 *
 * Admin-only. Returns the HITL routing config + per-bucket SPC
 * counters + drift ratios + the pending auto-promotion queue depth
 * as a single read-only snapshot. Powers the ``/admin/hitl``
 * dashboard. 403 if the caller lacks the ``admin`` role; 503 with
 * ``KW_HITL_DISABLED`` when ``KW_HITL_DISABLE_SCORER=true``.
 */
export async function getAdminHITLState(
  options: { signal?: AbortSignal } = {},
): Promise<ApiAdminHITLStateResponse> {
  return unwrap(
    await http.GET("/admin/hitl/state", {
      signal: options.signal,
    }),
  );
}

/**
 * POST /admin/archive/relink_scope
 *
 * Admin-only. Reactivates a soft-removed ``document_scopes`` row
 * (ADR-027 §1.2 / #269). Same dry-run-then-real pattern as the
 * other admin actions: ``?dry_run=true`` returns the impact preview
 * without mutating state, ``?confirm=true`` flips the real call.
 * Backend rejects passing both. Returns 404 if the scope link cannot
 * be found.
 */
export async function relinkScope(
  request: ApiRelinkScopeRequest,
  options: { dryRun?: boolean; signal?: AbortSignal } = {},
): Promise<ApiRelinkScopeResponse> {
  const dryRun = options.dryRun ?? false;
  const query = dryRun ? { dry_run: true } : { confirm: true };
  return unwrap(
    await http.POST("/admin/archive/relink_scope", {
      params: { query },
      body: request,
      signal: options.signal,
    }),
  );
}

/**
 * POST /admin/hitl/run_auto_promote_pass (#215 slice 3)
 *
 * Admin-only. Synchronously runs one HITL auto-promotion pass and
 * returns the structured per-version outcome. The dashboard exposes
 * this behind a single "Run pass" button next to the queue-depth
 * counter. 503 with ``KW_HITL_DISABLED`` when the worker is unwired
 * (same kill switch as the scorer / router).
 */
export async function runAutoPromotePass(
  options: { maxVersions?: number; signal?: AbortSignal } = {},
): Promise<ApiAutoPromoteResult> {
  const query: Record<string, number> = {};
  if (options.maxVersions !== undefined) {
    query.max_versions = options.maxVersions;
  }
  return unwrap(
    await http.POST("/admin/hitl/run_auto_promote_pass", {
      params: { query: query as never },
      signal: options.signal,
    }),
  );
}

/**
 * GET /admin/audit/events (#206 follow-up)
 *
 * Admin-only. Cursor-paginated walk over the structured audit event
 * log, sorted ``created_at DESC``. Filter by ``event_name`` / ``actor``
 * / ``since`` / ``until``; pass back ``next_cursor`` from a prior
 * response to load the next page. The response always carries
 * ``available_event_names`` so the UI's filter dropdown is
 * self-populating.
 *
 * Returns 403 ``KW_FORBIDDEN`` when the caller lacks the ``admin``
 * role; 503 ``KW_AUDIT_DISABLED`` when ``KW_AUDIT_ENABLED=false``
 * (the in-memory default — the audit DB is opt-in for persistent
 * deployments).
 */
export interface ListAuditEventsOptions {
  eventName?: string;
  actor?: string;
  since?: string;
  until?: string;
  cursor?: string;
  limit?: number;
  signal?: AbortSignal;
}

export async function listAuditEvents(
  opts: ListAuditEventsOptions = {},
): Promise<ApiAdminAuditEventsResponse> {
  const { eventName, actor, since, until, cursor, limit = 50, signal } = opts;
  const query: Record<string, string | number> = { limit };
  if (eventName) query.event_name = eventName;
  if (actor) query.actor = actor;
  if (since) query.since = since;
  if (until) query.until = until;
  if (cursor) query.cursor = cursor;
  return unwrap(
    await http.GET("/admin/audit/events", {
      params: { query: query as never },
      signal,
    }),
  );
}

/**
 * POST /admin/archive/purge_batch
 *
 * Admin-only. Bulk wrapper around ``purge_artifacts`` (ADR-027 §4 /
 * #273). Best-effort: a per-doc failure is reported on the matching
 * ``results[i]`` row rather than aborting the batch. The backend caps
 * the list at 100 ids per call (422 with ``KW_UNPROCESSABLE_ENTITY``).
 * Same dry-run-then-real flow as the per-doc routes.
 */
export async function purgeBatch(
  documentIds: string[],
  options: { dryRun?: boolean; signal?: AbortSignal } = {},
): Promise<ApiPurgeBatchResponse> {
  const dryRun = options.dryRun ?? false;
  const query = dryRun ? { dry_run: true } : { confirm: true };
  return unwrap(
    await http.POST("/admin/archive/purge_batch", {
      params: { query },
      body: { document_ids: documentIds },
      signal: options.signal,
    }),
  );
}

/**
 * GET /admin/taxonomy/versions/{taxonomy_id}
 *
 * Admin-only. Returns every version of one taxonomy lineage sorted by
 * ``version_number`` ascending. Unknown ``taxonomy_id`` returns
 * ``{taxonomy_id, versions: []}`` (200, not 404) so the Explorer can
 * render an empty lineage panel without a special-case error path.
 */
export async function listTaxonomyVersions(
  taxonomyId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiTaxonomyVersionListResponse> {
  return unwrap(
    await http.GET("/admin/taxonomy/versions/{taxonomy_id}", {
      params: { path: { taxonomy_id: taxonomyId } },
      signal: options.signal,
    }),
  );
}

/**
 * GET /admin/taxonomy/versions/{taxonomy_id}/{version_number}
 *
 * Admin-only. Returns the single version at the requested coordinate.
 * 404 ``KW_NOT_FOUND`` when the version doesn't exist.
 */
export async function getTaxonomyVersion(
  taxonomyId: string,
  versionNumber: number,
  options: { signal?: AbortSignal } = {},
): Promise<ApiTaxonomyVersion> {
  return unwrap(
    await http.GET(
      "/admin/taxonomy/versions/{taxonomy_id}/{version_number}",
      {
        params: {
          path: {
            taxonomy_id: taxonomyId,
            version_number: versionNumber,
          },
        },
        signal: options.signal,
      },
    ),
  );
}

/**
 * POST /admin/taxonomy/versions/{taxonomy_id}/{version_number}/transition
 *
 * Drives a TaxonomyVersion to its next lifecycle state (ADR-018 §2).
 * 409 on illegal transitions, 400 on missing fields, 503 KW_LLM_DISABLED
 * when the synthesize path is the prerequisite.
 */
export async function transitionTaxonomyVersion(
  taxonomyId: string,
  versionNumber: number,
  body: {
    to_state: ApiTaxonomyState;
    version_label?: string | null;
    reason?: string | null;
  },
  options: { signal?: AbortSignal } = {},
): Promise<ApiTaxonomyVersion> {
  return unwrap(
    await http.POST(
      "/admin/taxonomy/versions/{taxonomy_id}/{version_number}/transition",
      {
        params: {
          path: {
            taxonomy_id: taxonomyId,
            version_number: versionNumber,
          },
        },
        body,
        signal: options.signal,
      },
    ),
  );
}

/**
 * POST /admin/taxonomy/versions/{tid}/{vnum}/concepts/{cid}/transition
 *
 * Drives one ``ConceptSuggestion`` through its lifecycle (ADR-018 §5).
 * ``merge_target_id`` is required when ``to_state === "MERGED"`` and
 * rejected for every other target. 409 on illegal moves.
 */
export async function transitionTaxonomyConcept(
  taxonomyId: string,
  versionNumber: number,
  suggestionId: string,
  body: {
    to_state: ApiConceptSuggestionState;
    merge_target_id?: string | null;
    reason?: string | null;
  },
  options: { signal?: AbortSignal } = {},
): Promise<ApiConceptSuggestion> {
  return unwrap(
    await http.POST(
      "/admin/taxonomy/versions/{taxonomy_id}/{version_number}/concepts/{suggestion_id}/transition",
      {
        params: {
          path: {
            taxonomy_id: taxonomyId,
            version_number: versionNumber,
            suggestion_id: suggestionId,
          },
        },
        body,
        signal: options.signal,
      },
    ),
  );
}

/**
 * GET /knowledge/taxonomy
 *
 * Public read of the merged (imposed + computed) taxonomy. ADR-017.
 * Never 404s — an unconfigured deployment returns
 * ``is_configured=false`` with an empty categories list.
 */
export async function getKnowledgeTaxonomy(
  options: { signal?: AbortSignal } = {},
): Promise<ApiTaxonomyResponse> {
  return unwrap(
    await http.GET("/knowledge/taxonomy", {
      signal: options.signal,
    }),
  );
}

/**
 * POST /admin/taxonomy/drafts
 *
 * Mints a new ``DRAFT`` taxonomy version. Three modes per ADR-018 §2:
 *  - empty body → fresh ``taxonomy_id``, empty tree, ``version_number=1``
 *  - ``taxonomy_id`` only → next version for that id, empty tree
 *  - both → next version inheriting the source's tree
 */
export async function createTaxonomyDraft(
  body: { taxonomy_id?: string | null; source_version_number?: number | null } = {},
  options: { signal?: AbortSignal } = {},
): Promise<ApiTaxonomyVersion> {
  return unwrap(
    await http.POST("/admin/taxonomy/drafts", {
      body,
      signal: options.signal,
    }),
  );
}
