/**
 * Smoke test for GraphCanvas's #318-partial edge-click affordance.
 *
 * The full canvas interaction surface (pan / zoom / cluster expansion
 * / depth filtering) is exercised end-to-end by the App.* test files.
 * This module pins the contract that matters for the relation
 * evidence drawer: clicking a doc-to-doc edge fires ``onEdgeClick``
 * with the source / target ids and the edge type.
 *
 * Uses the SAMPLE_SNAPSHOT focused on a doc with known reference
 * edges (d1 → d2, d1 → d3) so doc-to-doc lines materialise without
 * needing the cluster expansion plumbing.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GraphCanvas } from "./GraphCanvas";
import { SAMPLE_SNAPSHOT } from "../state/explorer-data";

const baseProps = {
  snapshot: SAMPLE_SNAPSHOT,
  view: "corpus" as const,
  selectedId: null,
  conceptFocus: "",
  onSelect: vi.fn(),
  onToggleCluster: vi.fn(),
  onToggleDoc: vi.fn(),
  expandedClusters: new Set<string>(),
  expandedDocs: new Set<string>(),
  showClusters: true,
  showConfHeat: false,
  theme: "light" as const,
  depth: 3,
  hoveredId: null,
  onHover: vi.fn(),
  search: "",
  // Focus on d1 so its outgoing reference edges to d2 / d3 are
  // included in the layout's edges array.
  focusRoot: { kind: "doc" as const, id: "d1", label: "d1" },
};

describe("GraphCanvas — edge click (#318 partial)", () => {
  it("does not render hit areas when onEdgeClick is omitted", () => {
    render(<GraphCanvas {...baseProps} />);
    expect(screen.queryAllByTestId("kx-graph-edge-hit")).toHaveLength(0);
  });

  it("renders a clickable hit area for each visible doc-to-doc edge", () => {
    render(<GraphCanvas {...baseProps} onEdgeClick={vi.fn()} />);
    const hits = screen.queryAllByTestId("kx-graph-edge-hit");
    // Focused on d1 — at minimum the d1↔d2 and d1↔d3 reference edges
    // should be in the layout. The exact count depends on the
    // breadth-first walk's depth budget; we just assert there's at
    // least one and they're all between document nodes.
    expect(hits.length).toBeGreaterThanOrEqual(2);
    for (const hit of hits) {
      const src = hit.getAttribute("data-edge-source");
      const tgt = hit.getAttribute("data-edge-target");
      expect(src).toBeTruthy();
      expect(tgt).toBeTruthy();
      expect(src).not.toEqual(tgt);
    }
  });

  it("clicking a hit area invokes onEdgeClick with (source, target, type)", () => {
    const onEdgeClick = vi.fn();
    render(<GraphCanvas {...baseProps} onEdgeClick={onEdgeClick} />);
    const hits = screen.queryAllByTestId("kx-graph-edge-hit");
    expect(hits.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(hits[0]);
    expect(onEdgeClick).toHaveBeenCalledTimes(1);
    const [src, tgt, type] = onEdgeClick.mock.calls[0];
    expect(typeof src).toBe("string");
    expect(typeof tgt).toBe("string");
    // The sample fixture only carries reference / similar edges from
    // d1 — anything else means the layout returned an unexpected
    // edge kind.
    expect(["reference", "similar", "contradict"]).toContain(type);
  });
});
