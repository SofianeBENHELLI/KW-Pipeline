/**
 * Inline status pill for the Explorer's per-document detail panel.
 *
 * Renders only when the polling hook returns a meaningful state:
 * - ``IN_PROGRESS`` → "Projecting…" with a spinner (the live state)
 * - ``COMPLETED`` (after the loop is done) → "Projection up to date"
 * - ``FAILED`` → "Projection failed" with the error in ``title=``
 * - ``null`` (pre-first-poll, 404, or non-validated version) → nothing
 *
 * Mirrors the matching pill in apps/web (Orbital) but uses Explorer's
 * own ``kx-`` CSS prefix so it slots into the existing detail panel
 * styling without import gymnastics.
 */

import type { ReactElement } from "react";

import type { ProjectionStatusResponse } from "../api/types";

export interface ProjectionStatusPillProps {
  status: ProjectionStatusResponse | null;
  done: boolean;
}

export function ProjectionStatusPill({
  status,
  done,
}: ProjectionStatusPillProps): ReactElement | null {
  if (status === null) return null;

  if (status.status === "IN_PROGRESS") {
    return (
      <span
        className="kx-projection-pill kx-projection-pill--in-progress"
        data-testid="kx-projection-pill"
        data-state="in_progress"
        role="status"
        aria-live="polite"
      >
        <span className="kx-projection-pill__spinner" aria-hidden="true" />
        Projecting…
      </span>
    );
  }

  if (status.status === "FAILED") {
    return (
      <span
        className="kx-projection-pill kx-projection-pill--failed"
        data-testid="kx-projection-pill"
        data-state="failed"
        role="status"
        title={status.error ?? undefined}
      >
        Projection failed
      </span>
    );
  }

  if (status.status === "COMPLETED" && done) {
    return (
      <span
        className="kx-projection-pill kx-projection-pill--completed"
        data-testid="kx-projection-pill"
        data-state="completed"
        role="status"
      >
        Projection up to date
      </span>
    );
  }

  return null;
}
