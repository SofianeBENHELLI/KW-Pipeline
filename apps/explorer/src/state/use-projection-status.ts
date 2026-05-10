/**
 * Polls ``GET /knowledge/projection_status/{version_id}`` while the
 * tracker reports IN_PROGRESS, stops when it reaches a terminal state
 * (COMPLETED / FAILED) or 404 (no tracker entry).
 *
 * Drives the "Projecting…" indicator on the Explorer's detail panel
 * for validated documents whose graph is still being populated. Under
 * the default sync projection mode the first poll lands on COMPLETED
 * and the loop exits immediately — no network cost beyond the single
 * poll, no UI noise.
 *
 * Mirrors the same hook in apps/web (Orbital). Kept per-app for now
 * rather than promoted to apps/_shared because each frontend's
 * ``getProjectionStatus`` is wired against its own API client; a
 * shared hook would need a generic fetcher param. Worth doing if a
 * third frontend adopts.
 */

import { useEffect, useRef, useState } from "react";

import { getProjectionStatus } from "../api/client";
import type { ProjectionStatusResponse } from "../api/types";

export interface ProjectionStatusState {
  status: ProjectionStatusResponse | null;
  done: boolean;
}

const POLL_INTERVAL_MS = 1000;

export function useProjectionStatus(
  versionId: string | null,
): ProjectionStatusState {
  const [status, setStatus] = useState<ProjectionStatusResponse | null>(null);
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
        if (entry === null || entry.status !== "IN_PROGRESS") {
          setDone(true);
          return;
        }
        timeoutRef.current = setTimeout(pollOnce, POLL_INTERVAL_MS);
      } catch (err: unknown) {
        if (cancelled) return;
        // Aborted fetches (component unmount, version_id change) exit
        // silently. Other errors stop the loop too — there's no useful
        // UX for "we couldn't reach the status route" and the
        // historical fall-back-to-graph behaviour is fine.
        setDone(true);
        if (err instanceof DOMException && err.name === "AbortError") return;
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
  }, [versionId]);

  return { status, done };
}
