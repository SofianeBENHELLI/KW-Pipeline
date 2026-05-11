/**
 * CatalogRail — Phase 1 left rail. Pin the saved-view list, active
 * highlight, count rendering, and the query change handler.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CatalogRail, viewToStatuses } from "./CatalogRail";

describe("<CatalogRail />", () => {
  it("renders the four saved views and highlights the active one", () => {
    render(
      <CatalogRail view="review" onView={() => {}} query="" onQuery={() => {}} />,
    );
    expect(screen.getByText("Recent")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.getByText("Validated")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    const active = screen.getByText("Review").closest("button");
    expect(active).toHaveAttribute("aria-current", "page");
    expect(active).toHaveClass("is-active");
  });

  it("invokes onView with the selected view id", () => {
    const onView = vi.fn();
    render(<CatalogRail view="recent" onView={onView} query="" onQuery={() => {}} />);
    fireEvent.click(screen.getByText("Validated").closest("button")!);
    expect(onView).toHaveBeenCalledWith("validated");
  });

  it("renders counts when provided", () => {
    render(
      <CatalogRail
        view="recent"
        onView={() => {}}
        query=""
        onQuery={() => {}}
        counts={{ recent: 247, review: 23, validated: 1842, failed: 7 }}
      />,
    );
    expect(screen.getByText("247")).toBeInTheDocument();
    expect(screen.getByText("1,842")).toBeInTheDocument();
  });

  it("forwards filename query input to onQuery", () => {
    const onQuery = vi.fn();
    render(<CatalogRail view="recent" onView={() => {}} query="" onQuery={onQuery} />);
    fireEvent.change(screen.getByPlaceholderText("Filter filename…"), { target: { value: "neo4j" } });
    expect(onQuery).toHaveBeenCalledWith("neo4j");
  });
});

describe("viewToStatuses", () => {
  it("returns the expected status array per view", () => {
    expect(viewToStatuses("recent")).toEqual([]);
    expect(viewToStatuses("review")).toEqual(["NEEDS_REVIEW"]);
    expect(viewToStatuses("validated")).toEqual(["VALIDATED"]);
    expect(viewToStatuses("failed")).toEqual(["FAILED"]);
  });
});
