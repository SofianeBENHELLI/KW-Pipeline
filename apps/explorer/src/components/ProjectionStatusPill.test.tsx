/**
 * Pill component is a leaf — these tests exercise the four render
 * branches without going through the polling hook (the hook is
 * tested separately in apps/web; the explorer copy mirrors it).
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProjectionStatusPill } from "./ProjectionStatusPill";
import type { ProjectionStatusResponse } from "../api/types";

const BASE_ENTRY: ProjectionStatusResponse = {
  version_id: "v-1",
  status: "IN_PROGRESS",
  started_at: "2026-05-10T00:00:00Z",
  completed_at: null,
  error: null,
};

describe("ProjectionStatusPill (explorer)", () => {
  it("renders nothing when status is null (pre-first-poll)", () => {
    const { container } = render(
      <ProjectionStatusPill status={null} done={false} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the spinner copy when in progress", () => {
    render(<ProjectionStatusPill status={BASE_ENTRY} done={false} />);
    const pill = screen.getByTestId("kx-projection-pill");
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
    const pill = screen.getByTestId("kx-projection-pill");
    expect(pill).toHaveAttribute("data-state", "failed");
    expect(pill).toHaveTextContent(/Projection failed/);
    expect(pill).toHaveAttribute("title", "RuntimeError: graph store down");
  });

  it("renders the completed copy only after done flips true", () => {
    const completed: ProjectionStatusResponse = {
      ...BASE_ENTRY,
      status: "COMPLETED",
      completed_at: "2026-05-10T00:00:01Z",
    };

    const { container, rerender } = render(
      <ProjectionStatusPill status={completed} done={false} />,
    );
    expect(container).toBeEmptyDOMElement();

    rerender(<ProjectionStatusPill status={completed} done />);
    const pill = screen.getByTestId("kx-projection-pill");
    expect(pill).toHaveAttribute("data-state", "completed");
    expect(pill).toHaveTextContent(/Projection up to date/);
  });
});
