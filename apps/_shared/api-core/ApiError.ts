/**
 * Public API error envelope for the KW-Pipeline backend.
 *
 * The backend (apps/api) returns errors in the structured envelope
 * documented in ``docs/architecture/api_contract.md`` (issue #97):
 *
 *     {
 *       "error": {
 *         "code": "KW_UPLOAD_TOO_LARGE",
 *         "message": "Upload exceeds limit of 50000000 bytes",
 *         "retryable": false,
 *         "remediation": "Compress the file or split it..."
 *       }
 *     }
 *
 * Every frontend that talks to the backend needs to surface those four
 * fields so the UI can render the right "what to do next" copy. Before
 * this module landed, ``apps/widget`` and ``apps/explorer`` carried
 * byte-identical copies of this class plus the ``asApiError`` parser
 * (audit #227); the two webpack apps now import from here so a fix to
 * the envelope handling lands in one place.
 *
 * ``apps/web`` ships its own client today (the openapi-fetch generated
 * shape is slightly different); migrating it to this module is a
 * follow-up slice of the same audit issue.
 */

/**
 * Error thrown by the shared fetch wrapper when an HTTP response is
 * non-OK. Carries every field the public error envelope can supply so
 * UI surfaces never inspect the message string to decide what to do.
 *
 * Note: each frontend bundle has its own copy of this class at runtime
 * (webpack does not deduplicate classes across separately-loaded
 * dashboard tiles), so ``instanceof ApiError`` is reliable WITHIN a
 * bundle but NOT across two bundles. That's fine in practice — every
 * caller is inside the bundle that imported the class.
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
  retryable?: unknown;
  remediation?: unknown;
}

interface ResponseBodyShape {
  error?: ErrorEnvelope;
  detail?: unknown;
}

/**
 * Parse a non-OK ``Response`` into a typed :class:`ApiError`.
 *
 * Tolerates every body shape the backend might emit: the structured
 * envelope (``{error: {code, message, retryable, remediation}}``), a
 * legacy ``{detail: "..."}`` shape, or a non-JSON body. The cascade is
 * deliberate so callers never see a generic ``Error`` for a
 * recognisable backend response.
 */
export async function asApiError(response: Response): Promise<ApiError> {
  let body: ResponseBodyShape | null = null;
  try {
    body = (await response.clone().json()) as ResponseBodyShape;
  } catch {
    // Non-JSON or empty body — falls through to statusText fallback below.
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
