/**
 * Retry-with-backoff wrapper around ``fetch``.
 *
 * Frontends that read from the KW-Pipeline backend benefit from
 * tolerating brief upstream hiccups. A 502/503/504 from a restarting
 * gunicorn worker, a network blip during a tunnel reconnect, or a
 * Cloudflare edge timeout looks like an outage to the user — but it
 * usually clears within a second or two. Wrapping the read paths in
 * retry-with-backoff means the user sees a brief loading spinner
 * instead of a "something went wrong" page.
 *
 * Default policy:
 *
 * - Only **idempotent** methods are retried (GET, HEAD). Retrying
 *   POST/PUT/PATCH/DELETE risks duplicate side-effects unless the
 *   caller knows the endpoint is idempotent.
 * - Only **truly transient** HTTP statuses are retried (502, 503, 504).
 *   429 is excluded by default — the right response is to surface the
 *   rate limit to the user, not hammer harder. Operators that have
 *   real backpressure can opt into 429 retries via ``retryStatusCodes``.
 * - **Network errors** (``TypeError`` thrown by ``fetch``, e.g. DNS
 *   failure, connection reset) are retried unconditionally for
 *   idempotent methods.
 * - **4xx** responses propagate immediately — they're not transient.
 *
 * The wrapper respects ``Retry-After`` when present (delta-seconds form
 * only; HTTP-date form is rare and not worth the parsing footprint).
 *
 * Configurable via :class:`RetryOptions`. The defaults are conservative
 * so dropping this in front of an existing fetch never makes behaviour
 * worse, only better.
 */

const DEFAULT_RETRY_METHODS = ["GET", "HEAD"] as const;
const DEFAULT_RETRY_STATUS_CODES = [502, 503, 504] as const;
const DEFAULT_MAX_RETRIES = 2;
// Total worst-case wall time for the default policy is base + 2*base
// + jitter ≈ 250–400ms — fast enough that a transient blip looks like
// a slight pause to the user, slow enough that we don't hammer a
// recovering upstream. Operators can dial it up via ``baseDelayMs``
// if their downstream prefers a longer cooldown.
const DEFAULT_BASE_DELAY_MS = 100;
const DEFAULT_BACKOFF_CAP_MS = 8_000;

export interface RetryOptions {
  /** HTTP methods eligible for retry. Default: ``["GET", "HEAD"]``. */
  retryMethods?: readonly string[];
  /** HTTP status codes that trigger a retry. Default: ``[502, 503, 504]``. */
  retryStatusCodes?: readonly number[];
  /** Max retry attempts after the initial call. Default: ``2``. */
  maxRetries?: number;
  /** Base delay (ms). Doubles per attempt. Default: ``250``. */
  baseDelayMs?: number;
  /** Cap on the per-attempt delay (ms). Default: ``8_000``. */
  backoffCapMs?: number;
  /** Sleep override for tests. Default: ``setTimeout``. */
  sleep?: (ms: number) => Promise<void>;
}

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function isMethodRetryable(method: string, allow: readonly string[]): boolean {
  const upper = method.toUpperCase();
  return allow.some((m) => m.toUpperCase() === upper);
}

/**
 * Parse a ``Retry-After`` header. Returns the delta in milliseconds, or
 * ``null`` when the header is absent or unparseable.
 *
 * Only delta-seconds form is recognised (``Retry-After: 5``). HTTP-date
 * form is rare in practice and not worth the parsing surface; we fall
 * back to the computed exponential delay for those.
 */
function parseRetryAfter(value: string | null): number | null {
  if (value === null) return null;
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const seconds = Number(trimmed);
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  return seconds * 1000;
}

function computeBackoffMs(
  attempt: number,
  baseMs: number,
  capMs: number,
): number {
  // Exponential: base * 2^attempt, capped. Add a little jitter so a
  // burst of clients on the same backoff schedule doesn't all retry
  // in lockstep and re-trip the upstream limit.
  const exponential = baseMs * 2 ** attempt;
  const jitter = Math.random() * baseMs;
  return Math.min(capMs, exponential + jitter);
}

/**
 * Wrap a ``fetch``-shaped function with retry-on-transient-failure.
 *
 * Returns a new function with the same signature, so call sites swap
 * the wrapped fetch in transparently:
 *
 * ```ts
 * const fetchWithRetry = withRetry(globalThis.fetch);
 * const http = createClient<paths>({ fetch: fetchWithRetry });
 * ```
 */
export function withRetry(
  fetchFn: typeof fetch,
  options: RetryOptions = {},
): typeof fetch {
  const retryMethods = options.retryMethods ?? DEFAULT_RETRY_METHODS;
  const retryStatusCodes = options.retryStatusCodes ?? DEFAULT_RETRY_STATUS_CODES;
  const maxRetries = options.maxRetries ?? DEFAULT_MAX_RETRIES;
  const baseDelayMs = options.baseDelayMs ?? DEFAULT_BASE_DELAY_MS;
  const backoffCapMs = options.backoffCapMs ?? DEFAULT_BACKOFF_CAP_MS;
  const sleep = options.sleep ?? defaultSleep;

  if (maxRetries < 0) {
    throw new RangeError("maxRetries must be >= 0");
  }
  if (baseDelayMs < 0) {
    throw new RangeError("baseDelayMs must be >= 0");
  }

  return async function fetchWithRetry(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    const method = (init?.method ?? "GET").toUpperCase();
    const eligible = isMethodRetryable(method, retryMethods);

    let attempt = 0;
    while (true) {
      try {
        const response = await fetchFn(input, init);
        if (
          eligible &&
          attempt < maxRetries &&
          retryStatusCodes.includes(response.status)
        ) {
          const retryAfterMs =
            parseRetryAfter(response.headers.get("Retry-After")) ??
            computeBackoffMs(attempt, baseDelayMs, backoffCapMs);
          await sleep(retryAfterMs);
          attempt += 1;
          continue;
        }
        return response;
      } catch (err) {
        // Network errors (TypeError from fetch). Retry if eligible.
        if (!eligible || attempt >= maxRetries) {
          throw err;
        }
        if (!(err instanceof TypeError)) {
          throw err;
        }
        await sleep(computeBackoffMs(attempt, baseDelayMs, backoffCapMs));
        attempt += 1;
      }
    }
  };
}
