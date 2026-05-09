/**
 * Small status pill rendered next to the validate button so the
 * reviewer can tell whether the knowledge graph for this version is
 * fully populated.
 *
 * Hidden by default — only renders when the polling hook returns a
 * meaningful state. Under sync projection (the default posture today)
 * the first poll lands on COMPLETED instantly, so the pill briefly
 * shows "Projection up to date" then disappears on the next render
 * cycle. Under async projection (operator-opt-in via
 * ``KW_KNOWLEDGE_PROJECTION_ASYNC=true``) the pill shows "Projecting…"
 * with a spinner, then transitions to "Projection up to date" or
 * "Projection failed" based on the tracker entry.
 */

import type { ReactElement } from "react";

import type { ApiProjectionStatusResponse } from "../../api/types";

export interface ProjectionStatusPillProps {
  status: ApiProjectionStatusResponse | null;
  done: boolean;
}

export function ProjectionStatusPill({
  status,
  done,
}: ProjectionStatusPillProps): ReactElement | null {
  // Pre-first-poll: render nothing rather than an empty placeholder
  // so the workspace layout doesn't shift while we wait.
  if (status === null) {
    return null;
  }

  if (status.status === "IN_PROGRESS") {
    return (
      <span
        className="projection-status-pill projection-status-pill--in-progress"
        data-testid="projection-status-pill"
        data-state="in_progress"
        role="status"
        aria-live="polite"
      >
        <span className="projection-status-pill__spinner" aria-hidden="true" />
        Projecting…
      </span>
    );
  }

  if (status.status === "FAILED") {
    return (
      <span
        className="projection-status-pill projection-status-pill--failed"
        data-testid="projection-status-pill"
        data-state="failed"
        role="status"
        title={status.error ?? undefined}
      >
        Projection failed
      </span>
    );
  }

  // COMPLETED: only render when the loop has flipped ``done`` so we
  // don't flicker the pill in / out on the first successful poll.
  if (status.status === "COMPLETED" && done) {
    return (
      <span
        className="projection-status-pill projection-status-pill--completed"
        data-testid="projection-status-pill"
        data-state="completed"
        role="status"
      >
        Projection up to date
      </span>
    );
  }

  return null;
}
