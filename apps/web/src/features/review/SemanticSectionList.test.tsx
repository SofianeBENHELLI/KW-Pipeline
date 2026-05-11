/**
 * Component tests for ``SemanticSectionList`` (#408).
 *
 * Pins the four behaviours the reviewer relies on:
 *   1. Empty state renders the explicit "No sections extracted." copy.
 *   2. Each section row renders heading + preview text.
 *   3. Long lists cap at ``initialCount`` and surface a +N more affordance.
 *   4. Click on the chevron expands a single row's full text.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ApiSemanticSection } from "../../api/types";
import { SemanticSectionList } from "./SemanticSectionList";

function makeSection(i: number, opts: Partial<ApiSemanticSection> = {}): ApiSemanticSection {
  return {
    id: `s-${i}`,
    heading: `Section ${i}`,
    text: opts.text ?? `Body text for section ${i}.`,
    source_reference_ids: opts.source_reference_ids ?? [],
    ...opts,
  };
}

describe("SemanticSectionList", () => {
  it("renders the explicit empty-state copy when given no sections", () => {
    render(<SemanticSectionList sections={[]} />);
    expect(screen.getByTestId("sem-sections-empty")).toHaveTextContent(
      /No sections extracted/i,
    );
  });

  it("renders heading + preview text for each section row", () => {
    const sections = [makeSection(1), makeSection(2)];
    render(<SemanticSectionList sections={sections} />);
    expect(screen.getAllByTestId("sem-section-row")).toHaveLength(2);
    expect(screen.getByText("Section 1")).toBeInTheDocument();
    expect(screen.getByText("Section 2")).toBeInTheDocument();
  });

  it("caps long lists at initialCount and shows the +N more affordance", () => {
    const sections = Array.from({ length: 20 }, (_, i) => makeSection(i));
    render(<SemanticSectionList sections={sections} initialCount={6} />);
    expect(screen.getAllByTestId("sem-section-row")).toHaveLength(6);
    expect(screen.getByTestId("sem-sections-more")).toHaveTextContent("+14 more");
  });

  it("expands every row after clicking the +N more affordance", () => {
    const sections = Array.from({ length: 8 }, (_, i) => makeSection(i));
    render(<SemanticSectionList sections={sections} initialCount={3} />);
    fireEvent.click(screen.getByTestId("sem-sections-more"));
    expect(screen.getAllByTestId("sem-section-row")).toHaveLength(8);
    expect(screen.queryByTestId("sem-sections-more")).toBeNull();
  });

  it("expands a row's full text when its toggle is clicked", () => {
    const longBody = "A".repeat(500);
    const sections = [makeSection(0, { text: longBody })];
    render(<SemanticSectionList sections={sections} />);
    const row = screen.getByTestId("sem-section-row");
    const initial = within(row).getByTestId("sem-section-text").textContent ?? "";
    expect(initial.endsWith("…")).toBe(true);
    fireEvent.click(within(row).getByTestId("sem-section-toggle"));
    const expanded = within(row).getByTestId("sem-section-text").textContent ?? "";
    expect(expanded).toBe(longBody);
  });

  it("renders source-reference IDs when present", () => {
    const sections = [
      makeSection(0, { source_reference_ids: ["ref-a", "ref-b"] }),
    ];
    render(<SemanticSectionList sections={sections} />);
    const refs = screen.getByTestId("sem-section-refs");
    expect(refs).toHaveTextContent("ref-a");
    expect(refs).toHaveTextContent("ref-b");
  });

  it("falls back to '(untitled section)' when heading is empty", () => {
    const sections = [makeSection(0, { heading: "" })];
    render(<SemanticSectionList sections={sections} />);
    expect(screen.getByText("(untitled section)")).toBeInTheDocument();
  });
});
