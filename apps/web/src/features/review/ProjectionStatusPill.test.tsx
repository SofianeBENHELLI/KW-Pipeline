/**
 * ``ProjectionStatusPill`` is a leaf component — these tests exercise
 * the four render branches (no entry / IN_PROGRESS / FAILED /
 * COMPLETED+done) without going through the polling hook. The hook is
 * tested separately so the failure modes stay isolated.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProjectionStatusPill } from "./ProjectionStatusPill";
import type { ApiProjectionStatusResponse } from "../../api/types";

const BASE_ENTRY: ApiProjectionStatusResponse = {
  version_id: "v-1",
  status: "IN_PROGRESS",
  started_at: "2026-05-10T00:00:00Z",
  completed_at: null,
  error: null,
};

describe("ProjectionStatusPill", () => {
  it("renders nothing when status is null (pre-first-poll)", () => {
    const { container } = render(
      <ProjectionStatusPill status={null} done={false} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the spinner copy when in progress", () => {
    render(<ProjectionStatusPill status={BASE_ENTRY} done={false} />);
    const pill = screen.getByTestId("projection-status-pill");
    expect(pill).toHaveAttribute("data-state", "in_progress");
    expect(pill).toHaveTextContent(/Projecting…/);
  });

  it("renders the failed copy with title set to the error", () => {
    render(
      <ProjectionStatusPill
        status={{
          ...BASE_ENTRY,
          status: "FAILED",
          completed_at: "2026-05-10T00:00:01Z",
          error: "RuntimeError: graph store down",
        }}
        done
      />,
    );
    const pill = screen.getByTestId("projection-status-pill");
    expect(pill).toHaveAttribute("data-state", "failed");
    expect(pill).toHaveTextContent(/Projection failed/);
    expect(pill).toHaveAttribute("title", "RuntimeError: graph store down");
  });

  it("renders the completed copy only after done flips true", () => {
    const completed: ApiProjectionStatusResponse = {
      ...BASE_ENTRY,
      status: "COMPLETED",
      completed_at: "2026-05-10T00:00:01Z",
    };

    // Mid-poll (done=false): nothing rendered to avoid flicker.
    const { container, rerender } = render(
      <ProjectionStatusPill status={completed} done={false} />,
    );
    expect(container).toBeEmptyDOMElement();

    // Once done flips true, the success pill appears.
    rerender(<ProjectionStatusPill status={completed} done />);
    const pill = screen.getByTestId("projection-status-pill");
    expect(pill).toHaveAttribute("data-state", "completed");
    expect(pill).toHaveTextContent(/Projection up to date/);
  });
});
