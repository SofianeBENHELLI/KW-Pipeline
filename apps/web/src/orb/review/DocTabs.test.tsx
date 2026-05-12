/**
 * DocTabs — pin the three tab labels, the active state, the
 * default-on tag, and the hint that swaps with the active tab.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DocTabs } from "./DocTabs";

describe("<DocTabs />", () => {
  it("renders the three tabs with the right labels", () => {
    render(<DocTabs active="linked" onChange={() => {}} />);
    expect(screen.getByRole("tab", { name: /Linked view/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^Review$/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^Pipeline$/ })).toBeInTheDocument();
  });

  it("marks the active tab via aria-selected and aria-current", () => {
    render(<DocTabs active="review" onChange={() => {}} />);
    const review = screen.getByRole("tab", { name: /^Review$/ });
    expect(review).toHaveAttribute("aria-selected", "true");
    expect(review).toHaveAttribute("aria-current", "page");
    expect(review).toHaveClass("is-active");
    const linked = screen.getByRole("tab", { name: /Linked view/ });
    expect(linked).toHaveAttribute("aria-selected", "false");
  });

  it("flags Linked view as the default-on tab", () => {
    render(<DocTabs active="linked" onChange={() => {}} />);
    expect(screen.getByText("default")).toBeInTheDocument();
  });

  it("invokes onChange when a tab is clicked", () => {
    const onChange = vi.fn();
    render(<DocTabs active="linked" onChange={onChange} />);
    fireEvent.click(screen.getByRole("tab", { name: /^Pipeline$/ }));
    expect(onChange).toHaveBeenCalledWith("pipeline");
  });

  it("swaps the contextual hint with the active tab", () => {
    const { rerender } = render(<DocTabs active="linked" onChange={() => {}} />);
    expect(screen.getByText(/hover any object/i)).toBeInTheDocument();
    rerender(<DocTabs active="review" onChange={() => {}} />);
    expect(
      screen.getByText(/lifecycle · extraction · semantic · versions/i),
    ).toBeInTheDocument();
    rerender(<DocTabs active="pipeline" onChange={() => {}} />);
    expect(
      screen.getByText(/every state transition with actor/i),
    ).toBeInTheDocument();
  });
});
