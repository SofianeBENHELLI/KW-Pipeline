/**
 * Component tests for ``SemanticWarningList`` (#408).
 *
 * Pins:
 *   1. Empty state with the explicit "ran cleanly" copy.
 *   2. Each warning string renders one row with the glyph + text.
 *   3. Long lists cap at ``initialCount`` with +N more.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SemanticWarningList } from "./SemanticWarningList";

describe("SemanticWarningList", () => {
  it("renders the explicit empty-state copy when given no warnings", () => {
    render(<SemanticWarningList warnings={[]} />);
    expect(screen.getByTestId("sem-warnings-empty")).toHaveTextContent(
      /No warnings/i,
    );
  });

  it("renders one row per warning string", () => {
    render(
      <SemanticWarningList
        warnings={[
          "Section 4 has low confidence.",
          "Asset 'iso-9001' missing source reference.",
        ]}
      />,
    );
    const rows = screen.getAllByTestId("sem-warning-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Section 4 has low confidence.");
    expect(rows[1]).toHaveTextContent("Asset 'iso-9001' missing source reference.");
  });

  it("caps long lists at initialCount and reveals on click", () => {
    const warnings = Array.from({ length: 18 }, (_, i) => `Warning ${i}`);
    render(<SemanticWarningList warnings={warnings} initialCount={4} />);
    expect(screen.getAllByTestId("sem-warning-row")).toHaveLength(4);
    expect(screen.getByTestId("sem-warnings-more")).toHaveTextContent("+14 more");
    fireEvent.click(screen.getByTestId("sem-warnings-more"));
    expect(screen.getAllByTestId("sem-warning-row")).toHaveLength(18);
  });
});
