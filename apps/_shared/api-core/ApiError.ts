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
 * Module-level session-expired trigger (#83 slice 3 / ADR-019 §5).
 *
 * The widget + explorer apps register their ``SessionGuardProvider``
 * callback here via :func:`setSessionTrigger`. ApiError's constructor
 * fires it for any 401 response so the user-facing banner appears
 * automatically — every route, every helper, no per-call-site
 * branching.
 *
 * Default is a no-op so unit tests, node scripts, and any other
 * consumer outside the React tree stay quiet without bespoke setup.
 *
 * Limitation: the default ``KW_AUTH_MODE=dev`` (per #245) never
 * returns 401 in normal operation, so the hook is exercised via
 * vitest mocks and the ``#force-session-expired`` URL-hash dev stub
 * each app installs at its root.
 */
type SessionTrigger = () => void;
let sessionTrigger: SessionTrigger = () => {
  // No-op until a provider registers.
};

/**
 * Register the callback that flips the session-expired banner on
 * across every consumer of the shared ApiError class.
 */
export function setSessionTrigger(fn: SessionTrigger): void {
  sessionTrigger = fn;
}

/**
 * Reset the trigger to the default no-op. Tests call this between
 * cases so a 401 in one test doesn't leak into the next.
 */
export function clearSessionTrigger(): void {
  sessionTrigger = () => {
    /* no-op */
  };
}

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
 *
 * Constructing an ApiError with ``status === 401`` always fires the
 * registered :func:`setSessionTrigger` callback (#83 slice 3 /
 * ADR-019 §5). The trigger defaults to a no-op so this works
 * without a SessionGuardProvider mounted.
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
