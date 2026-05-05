/**
 * ``useAdminConfig`` — fetch ``GET /admin/config`` once at app boot
 * (EPIC-A A.8 #215).
 *
 * The response drives two surfaces:
 *
 *  1. The :class:`SettingsModal` (existing, separate fetch path).
 *  2. The corpus-wide force-auto banner at the app root — when
 *     ``hitl.force_auto_corpus === true`` the operator must see a
 *     non-dismissible alert that every version is being auto-validated
 *     regardless of confidence.
 *
 * 403 (caller is not admin) is handled silently: non-admin users
 * never see admin alerts, the hook resolves with ``config: null`` and
 * the banner stays hidden. Other errors surface ``error`` so the
 * caller may log them — the banner itself simply hides on error.
 *
 * The hook returns a ``status`` discriminator so consumers can
 * branch cleanly without inspecting null patterns:
 *
 *   - ``loading``    — initial fetch in flight.
 *   - ``ok``         — config available, render driven by the fields.
 *   - ``forbidden``  — non-admin user; hide admin-scoped surfaces.
 *   - ``error``      — fetch failed (network / 5xx); also hide.
 */

import { useEffect, useState } from "react";

import { ApiError } from "../../../_shared/api-core";
import { fetchAdminConfig, type AdminConfigResponse } from "../../../_shared/settings-hub";

export type AdminConfigStatus = "loading" | "ok" | "forbidden" | "error";

export interface UseAdminConfigResult {
  status: AdminConfigStatus;
  config: AdminConfigResponse | null;
  error: Error | null;
}

/**
 * Fetches /admin/config once on mount. Caching policy: we don't
 * refetch on focus or reconnect — admin config is operator-controlled
 * (env vars on the server) and the page reload cycle is expected when
 * an operator flips a flag. The session-change refresh that the spec
 * mentions is implicit: a 401 reloads the page, which re-runs the
 * hook.
 */
export function useAdminConfig(apiBaseUrl: string): UseAdminConfigResult {
  const [state, setState] = useState<UseAdminConfigResult>({
    status: "loading",
    config: null,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    fetchAdminConfig(apiBaseUrl, controller.signal)
      .then((config) => {
        if (cancelled) return;
        setState({ status: "ok", config, error: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // Aborted fetches surface as DOMException("AbortError"); ignore
        // them so unmount during dev / strict-mode doesn't flip the
        // banner state into "error".
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError && err.status === 403) {
          setState({ status: "forbidden", config: null, error: null });
          return;
        }
        const error = err instanceof Error ? err : new Error(String(err));
        setState({ status: "error", config: null, error });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [apiBaseUrl]);

  return state;
}
