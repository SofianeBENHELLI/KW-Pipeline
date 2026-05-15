/**
 * DocTabs — pin the three tab labels (Linked view / Pipeline & FSM /
 * Graph), the active state, the default-on tag, and the hint that
 * swaps with the active tab.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DocTabs } from "./DocTabs";

describe("<DocTabs />", () => {
  it("renders the three tabs with the right labels", () => {
    render(<DocTabs active="linked" onChange={() => {}} />);
    expect(screen.getByRole("tab", { name: /Linked view/ })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /Pipeline & FSM/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^Graph/ })).toBeInTheDocument();
  });

  it("marks the active tab via aria-selected and aria-current", () => {
    render(<DocTabs active="pipeline" onChange={() => {}} />);
    const pipeline = screen.getByRole("tab", { name: /Pipeline & FSM/ });
    expect(pipeline).toHaveAttribute("aria-selected", "true");
    expect(pipeline).toHaveAttribute("aria-current", "page");
    expect(pipeline).toHaveClass("is-active");
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
    fireEvent.click(screen.getByRole("tab", { name: /Pipeline & FSM/ }));
    expect(onChange).toHaveBeenCalledWith("pipeline");
  });

  it("swaps the contextual hint with the active tab", () => {
    const { rerender } = render(<DocTabs active="linked" onChange={() => {}} />);
    expect(screen.getByText(/hover any object/i)).toBeInTheDocument();
    rerender(<DocTabs active="pipeline" onChange={() => {}} />);
    expect(
      screen.getByText(/lifecycle · extraction · semantic · versions/i),
    ).toBeInTheDocument();
    rerender(<DocTabs active="graph" onChange={() => {}} />);
    expect(
      screen.getByText(/topics · entities · chunks projected for this document/i),
    ).toBeInTheDocument();
  });

  it("invokes onChange when the Graph tab is clicked", () => {
    const onChange = vi.fn();
    render(<DocTabs active="linked" onChange={onChange} />);
    fireEvent.click(screen.getByRole("tab", { name: /^Graph/ }));
    expect(onChange).toHaveBeenCalledWith("graph");
  });
});
