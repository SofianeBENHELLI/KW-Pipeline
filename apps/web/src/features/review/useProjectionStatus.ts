/**
 * Polls ``GET /knowledge/projection_status/{version_id}`` while the
 * tracker reports IN_PROGRESS, stops when it reaches a terminal state
 * (COMPLETED / FAILED) or 404 (no tracker entry).
 *
 * Designed so the reviewer can see a "Projecting…" pill after validate
 * once the operator flips ``KW_KNOWLEDGE_PROJECTION_ASYNC=true``.
 * Under the default sync mode the projection is already done by the
 * time validate returns, so the first poll lands on COMPLETED and the
 * loop exits immediately — no behaviour change for the existing
 * default-on-the-tin posture.
 */

import { useEffect, useRef, useState } from "react";

import { getProjectionStatus } from "../../api/client";
import type { ApiProjectionStatusResponse } from "../../api/types";

/** Snapshot the hook returns on every render. */
export interface ProjectionStatusState {
  /** ``null`` while we haven't received the first poll back. */
  status: ApiProjectionStatusResponse | null;
  /** ``true`` once the loop has stopped (terminal state, 404, or
   *  the version id changed and the prior poll was cancelled). */
  done: boolean;
}

/** Re-poll cadence when the entry is IN_PROGRESS, in milliseconds. */
const POLL_INTERVAL_MS = 1000;

/**
 * ``triggerToken`` lets callers force a fresh poll cycle without
 * unmounting — bump it after a successful validate so the hook
 * forgets any prior terminal state and re-polls until the new
 * projection lands.
 */
export function useProjectionStatus(
  versionId: string | null,
  triggerToken: number = 0,
): ProjectionStatusState {
  const [status, setStatus] = useState<ApiProjectionStatusResponse | null>(null);
  const [done, setDone] = useState<boolean>(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (versionId === null) {
      setStatus(null);
      setDone(true);
      return;
    }

    const controller = new AbortController();
    let cancelled = false;
    setStatus(null);
    setDone(false);

    async function pollOnce(): Promise<void> {
      if (cancelled || versionId === null) return;
      try {
        const entry = await getProjectionStatus(versionId, {
          signal: controller.signal,
        });
        if (cancelled) return;
        setStatus(entry);
        // 404 (entry === null) and terminal states stop the loop.
        if (entry === null || entry.status !== "IN_PROGRESS") {
          setDone(true);
          return;
        }
        timeoutRef.current = setTimeout(pollOnce, POLL_INTERVAL_MS);
      } catch (err: unknown) {
        if (cancelled) return;
        // Aborted fetches surface as DOMException("AbortError"); treat
        // those as "we're being torn down" and exit silently. Anything
        // else stops the loop too — there's no useful UX for "we
        // couldn't reach the status route" and the historical
        // fall-back-to-graph behaviour is fine.
        setDone(true);
        if (err instanceof DOMException && err.name === "AbortError") return;
        // Non-abort errors are quietly swallowed — the pill just
        // disappears. Surfacing them would compete with the existing
        // graph-view error banner, which is the user's signal of
        // record for "graph is unreachable."
      }
    }

    void pollOnce();

    return () => {
      cancelled = true;
      controller.abort();
      if (timeoutRef.current !== null) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    };
  }, [versionId, triggerToken]);

  return { status, done };
}
