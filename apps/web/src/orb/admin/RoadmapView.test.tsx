/**
 * RoadmapView — pin the gallery shape and the disabled-by-design
 * affordance per converged plan §C.3.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RoadmapView } from "./RoadmapView";
import {
  ROADMAP_CARDS,
  ROADMAP_CATEGORY_ORDER,
} from "./RoadmapView.config";

describe("<RoadmapView />", () => {
  it("renders one section per category in the configured order", () => {
    render(<RoadmapView />);
    const sections = ROADMAP_CATEGORY_ORDER.map((c) =>
      screen.queryByTestId(`kf-roadmap-grid-${c}`),
    );
    // Every category that has at least one card surfaces a grid.
    const present = sections.filter(Boolean);
    expect(present.length).toBeGreaterThan(0);
    // Each card from the config renders exactly once.
    for (const card of ROADMAP_CARDS) {
      expect(
        screen.getByTestId(`kf-roadmap-card-${card.id}`),
      ).toBeInTheDocument();
    }
  });

  it("renders every card as a disabled button so nothing dispatches", () => {
    render(<RoadmapView />);
    for (const card of ROADMAP_CARDS) {
      const btn = screen.getByTestId(`kf-roadmap-card-${card.id}`);
      expect(btn).toBeDisabled();
      expect(btn).toHaveAttribute("aria-disabled", "true");
    }
  });

  it("surfaces the converged-plan section reference on each card", () => {
    render(<RoadmapView />);
    for (const card of ROADMAP_CARDS) {
      const btn = screen.getByTestId(`kf-roadmap-card-${card.id}`);
      expect(
        within(btn).getByText(card.planSection, { exact: false }),
      ).toBeInTheDocument();
    }
  });

  it("flags blocked items so the demo audience can see them", () => {
    render(<RoadmapView />);
    const blockedCards = ROADMAP_CARDS.filter((c) => c.blockedOn);
    expect(blockedCards.length).toBeGreaterThan(0);
    for (const card of blockedCards) {
      const btn = screen.getByTestId(`kf-roadmap-card-${card.id}`);
      expect(within(btn).getByText(/blocked/i)).toBeInTheDocument();
    }
  });

  it("renders the explainer header so audiences understand the gallery's intent", () => {
    render(<RoadmapView />);
    expect(
      screen.getByText(/not yet shipped/i, { exact: false }),
    ).toBeInTheDocument();
  });
});
