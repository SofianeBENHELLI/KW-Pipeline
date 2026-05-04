/**
 * Plain fetch helper for ``GET /admin/config``.
 *
 * No React import on purpose — ``apps/_shared`` stays peer-dep free
 * so the package resolves cleanly under each host app's tsconfig
 * without needing a hoisted ``node_modules``. Each host wires its
 * own React state on top (see ``apps/widget/src/sections/SettingsSection.tsx``
 * etc).
 */

import { ApiError, asApiError } from "../api-core";
import type { AdminConfigResponse } from "./types";

export async function fetchAdminConfig(
  apiBaseUrl: string,
  signal?: AbortSignal,
): Promise<AdminConfigResponse> {
  const response = await fetch(
    apiBaseUrl.replace(/\/$/, "") + "/admin/config",
    { signal },
  );
  if (!response.ok) throw await asApiError(response);
  return (await response.json()) as AdminConfigResponse;
}

export { ApiError };
