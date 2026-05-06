/**
 * Fetch wrappers for the transitional Demo-toggle backend
 * (``POST /admin/demo/load``, ``GET /admin/demo/status``,
 * ``POST /admin/demo/reset``).
 *
 * Lives in ``apps/_shared/`` so both the Explorer (webpack tile) and
 * the Web reviewer app (Vite SPA) can import the same helpers without
 * either side picking up the other's openapi-generated schema —
 * Explorer doesn't generate one and we don't want to introduce that
 * dependency for a transitional feature whose entire delete contract
 * is "``git rm apps/_shared/demo-toggle/`` plus the modal wire-ups".
 *
 * Hand-written interfaces below mirror the Pydantic models in
 * ``apps/api/app/schemas/demo.py``. If the backend contract drifts the
 * vitest suite picks it up via the typed assertions; until then this
 * stays a tiny, dependency-free TypeScript surface.
 *
 * Error path: every non-2xx is converted into a shared :class:`ApiError`
 * via the same :func:`asApiError` parser the rest of the app uses, so
 * the 401 session-trigger and structured envelope handling come for
 * free. The 409 conflict case carries a ``non_demo_doc_count`` that the
 * UI needs to render the "Force load" panel — we tuck it onto the
 * thrown :class:`DemoConflictError` subclass below so callers can
 * narrow with ``instanceof`` without poking at JSON.
 */

import { ApiError, asApiError } from "../api-core";

/** Body of ``POST /admin/demo/load``. */
export interface DemoLoadRequest {
  /**
   * When ``false`` (default), the backend refuses with 409 if any
   * non-demo document already lives in the catalog. When ``true``, the
   * conflict guard is bypassed — used by the UI's "Force load" path
   * after the operator has confirmed.
   */
  force?: boolean;
}

/**
 * Snapshot of the demo dataset's lifecycle on the running backend.
 *
 * Returned by all three endpoints so the UI can refresh without a
 * second round-trip to ``/status``. Mirrors
 * :class:`app.schemas.demo.DemoStatusResponse`.
 */
export interface DemoStatusResponse {
  /** At least one demo-tagged document is currently in the catalog. */
  loaded: boolean;
  /** A load is currently executing in a background thread. */
  in_progress: boolean;
  /** Number of catalog rows the demo loader produced; capped at the fixture count. */
  demo_doc_count: number;
  /** Number of catalog rows the demo loader did **not** produce. */
  non_demo_doc_count: number;
  /** ISO-8601 wall clock of the most recent successful load, or ``null``. */
  last_loaded_at: string | null;
  /** Error message from the most recent failed load attempt, or ``null``. */
  last_error: string | null;
}

/**
 * 409 conflict body the backend emits when the load is refused.
 *
 * Lives inside the structured error envelope's ``detail`` field — see
 * :func:`asApiError` and :func:`_json_error_response` on the backend
 * for the full envelope shape.
 */
export interface DemoConflictDetail {
  code: string;
  detail: string;
  non_demo_doc_count: number;
}

/**
 * Subclass of :class:`ApiError` used for the demo-load 409 path.
 *
 * Carrying a dedicated subclass means the modal can ``instanceof``-narrow
 * to the conflict case without sniffing ``error.status === 409`` plus a
 * payload string match — the constructor pulls the
 * ``non_demo_doc_count`` off the parsed envelope so consumers can
 * render the inline confirmation panel directly.
 */
export class DemoConflictError extends ApiError {
  constructor(
    base: ApiError,
    public readonly nonDemoDocCount: number,
  ) {
    super(base.status, base.detail, base.code, base.retryable, base.remediation);
    this.name = "DemoConflictError";
  }
}

/**
 * Strip a trailing ``/`` from the configured base URL so route
 * concatenation never produces ``http://host//admin/demo/...``.
 *
 * The shared :class:`apps/_shared/api-core` helper does the same; we
 * re-implement here rather than re-exporting to keep the
 * ``demo-toggle`` package's public surface minimal.
 */
function joinUrl(baseUrl: string, path: string): string {
  return baseUrl.replace(/\/$/, "") + path;
}

/**
 * Convert a non-OK response into a typed :class:`ApiError`. When the
 * status is 409 and the parsed body carries a ``non_demo_doc_count``
 * field, we wrap it in :class:`DemoConflictError` so the modal can
 * narrow without re-reading the response body (which is a single-use
 * stream).
 */
async function rejectFromResponse(response: Response): Promise<never> {
  // Clone before letting :func:`asApiError` consume the body — we may
  // need to re-read the JSON for the conflict-detail extraction.
  let parsed: unknown = null;
  try {
    parsed = await response.clone().json();
  } catch {
    // Non-JSON or empty body — :func:`asApiError` handles its own fallback.
  }
  const apiError = await asApiError(response);
  if (response.status === 409) {
    const detail = (parsed as { detail?: { non_demo_doc_count?: unknown } } | null)
      ?.detail;
    const count =
      detail && typeof detail === "object" && typeof detail.non_demo_doc_count === "number"
        ? detail.non_demo_doc_count
        : 0;
    throw new DemoConflictError(apiError, count);
  }
  throw apiError;
}

/**
 * ``GET /admin/demo/status`` — read-only snapshot of the toggle state.
 *
 * Called once on mount and then every 2 s while ``in_progress=true``.
 * Throws :class:`ApiError` on non-2xx; the caller is expected to
 * surface that to the UI's error line.
 */
export async function fetchDemoStatus(
  baseUrl: string,
  signal?: AbortSignal,
): Promise<DemoStatusResponse> {
  const response = await fetch(joinUrl(baseUrl, "/admin/demo/status"), {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) await rejectFromResponse(response);
  return (await response.json()) as DemoStatusResponse;
}

/**
 * ``POST /admin/demo/load`` — kick off the bundled demo loader.
 *
 * Returns ``202 Accepted`` + a :class:`DemoStatusResponse` so callers
 * can flip immediately into polling without a second round-trip. On
 * conflict (a load already running, or non-demo docs present without
 * ``force=true``) throws :class:`DemoConflictError` carrying the
 * ``non_demo_doc_count`` for the inline confirmation panel.
 */
export async function postDemoLoad(
  baseUrl: string,
  force: boolean,
  signal?: AbortSignal,
): Promise<DemoStatusResponse> {
  const body: DemoLoadRequest = { force };
  const response = await fetch(joinUrl(baseUrl, "/admin/demo/load"), {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) await rejectFromResponse(response);
  return (await response.json()) as DemoStatusResponse;
}

/**
 * ``POST /admin/demo/reset`` — soft-archive every demo document and
 * clear the toggle state. Returns the post-reset status snapshot.
 *
 * The route is idempotent: hitting it twice in a row is harmless,
 * already-archived rows are left untouched.
 */
export async function postDemoReset(
  baseUrl: string,
  signal?: AbortSignal,
): Promise<DemoStatusResponse> {
  const response = await fetch(joinUrl(baseUrl, "/admin/demo/reset"), {
    method: "POST",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) await rejectFromResponse(response);
  return (await response.json()) as DemoStatusResponse;
}
