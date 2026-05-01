/**
 * Harvester API client — thin fetch wrapper with typed helpers.
 *
 * Base URL is read from VITE_API_BASE_URL at build time (with a sensible
 * local-dev default). No external HTTP library is required — native fetch
 * is used throughout.
 */

import type {
  ApiDocument,
  ApiDocumentVersion,
  ApiRawExtraction,
  ApiSemanticDocument,
  ApiUploadResponse,
  ListDocumentsResponse,
} from "./types";

// ─── Base URL ────────────────────────────────────────────────────────────────

// VITE_API_BASE_URL is injected at build time via Vite. Falls back to the
// local-dev default when the env var is not set.
const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// ─── Low-level helpers ───────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // ignore JSON parse errors — keep the statusText fallback
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

async function requestText(path: string, init?: RequestInit): Promise<string> {
  const response = await fetch(`${BASE_URL}${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // ignore
    }
    throw new ApiError(response.status, detail);
  }
  return response.text();
}

// ─── Document endpoints ──────────────────────────────────────────────────────

/**
 * GET /documents
 * Returns one page of catalog entries. Pass `cursor` to advance pages.
 */
export function listDocuments(
  limit = 50,
  cursor?: string,
): Promise<ListDocumentsResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return request<ListDocumentsResponse>(`/documents?${params.toString()}`);
}

/**
 * GET /documents/{document_id}
 * Returns a single document with all its versions.
 */
export function getDocument(documentId: string): Promise<ApiDocument> {
  return request<ApiDocument>(`/documents/${encodeURIComponent(documentId)}`);
}

/**
 * POST /documents/upload
 * Streams a file to the backend and returns the created DocumentVersion.
 */
export function uploadDocument(
  file: File,
  documentId?: string,
): Promise<ApiUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  if (documentId) form.append("document_id", documentId);
  return request<ApiUploadResponse>("/documents/upload", {
    method: "POST",
    body: form,
  });
}

// ─── Version endpoints ───────────────────────────────────────────────────────

/**
 * GET /documents/{document_id}/versions/{version_id}
 *
 * NOTE: The backend does not currently expose a dedicated
 * GET /documents/{document_id}/versions/{version_id} route. Callers that
 * need a single version should use getDocument() and filter locally.
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
export function extractVersion(
  documentId: string,
  versionId: string,
): Promise<ApiRawExtraction> {
  return request<ApiRawExtraction>(
    `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/extract`,
    { method: "POST" },
  );
}

/**
 * GET /documents/{document_id}/versions/{version_id}/extraction
 * Returns cached raw extraction JSON.
 */
export function getExtraction(
  documentId: string,
  versionId: string,
): Promise<ApiRawExtraction> {
  return request<ApiRawExtraction>(
    `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/extraction`,
  );
}

// ─── Semantic endpoints ──────────────────────────────────────────────────────

/**
 * POST /documents/{document_id}/versions/{version_id}/semantic
 * Generates (or returns cached) semantic output.
 */
export function generateSemantic(
  documentId: string,
  versionId: string,
): Promise<ApiSemanticDocument> {
  return request<ApiSemanticDocument>(
    `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/semantic`,
    { method: "POST" },
  );
}

/**
 * GET /documents/{document_id}/versions/{version_id}/semantic
 * Returns cached semantic JSON.
 */
export function getSemantic(
  documentId: string,
  versionId: string,
): Promise<ApiSemanticDocument> {
  return request<ApiSemanticDocument>(
    `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/semantic`,
  );
}

/**
 * GET /documents/{document_id}/versions/{version_id}/markdown
 * Returns generated Markdown as plain text.
 */
export function getMarkdown(
  documentId: string,
  versionId: string,
): Promise<string> {
  return requestText(
    `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/markdown`,
  );
}

// ─── Review endpoints ─────────────────────────────────────────────────────────

/**
 * POST /documents/{document_id}/versions/{version_id}/validate
 */
export function validateVersion(
  documentId: string,
  versionId: string,
  reviewerNote?: string,
): Promise<ApiSemanticDocument> {
  return request<ApiSemanticDocument>(
    `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/validate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer_note: reviewerNote ?? null }),
    },
  );
}

/**
 * POST /documents/{document_id}/versions/{version_id}/reject
 */
export function rejectVersion(
  documentId: string,
  versionId: string,
  reviewerNote?: string,
): Promise<ApiSemanticDocument> {
  return request<ApiSemanticDocument>(
    `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/reject`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer_note: reviewerNote ?? null }),
    },
  );
}
