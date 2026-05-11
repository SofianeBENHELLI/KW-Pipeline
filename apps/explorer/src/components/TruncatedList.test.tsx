/**
 * Unit tests for the ``<TruncatedList>`` helper.
 *
 * The component was promoted out of ``DetailPanel.tsx`` so the
 * App.tsx local-fallback search dropdown could reuse it (#321).
 * The integration tests in ``DetailPanel.test.tsx`` already cover
 * the component end-to-end; this file pins the contract on its
 * own so future callers (and a future reviewer of the helper)
 * have a focused fixture to read.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TruncatedList } from "./TruncatedList";

function letters(n: number): string[] {
  return Array.from({ length: n }, (_, i) => `item-${i}`);
}

describe("TruncatedList", () => {
  it("renders every item and no affordance when items.length <= initialCount", () => {
    render(
      <TruncatedList
        items={letters(3)}
        initialCount={5}
        renderItem={(it) => <span data-testid="item">{it}</span>}
      />,
    );
    expect(screen.getAllByTestId("item")).toHaveLength(3);
    expect(screen.queryByTestId("kx-truncated-more")).toBeNull();
  });

  it("caps the initial render at initialCount and surfaces +N more when over the cap", () => {
    render(
      <TruncatedList
        items={letters(10)}
        initialCount={4}
        renderItem={(it) => <span data-testid="item">{it}</span>}
      />,
    );
    expect(screen.getAllByTestId("item")).toHaveLength(4);
    const more = screen.getByTestId("kx-truncated-more");
    expect(more).toHaveTextContent("+6 more");
    // Aria label gives the exact hidden count for screen readers.
    expect(more).toHaveAttribute("aria-label", "Show 6 more");
  });

  it("clicking the affordance reveals every item and removes the button", () => {
    render(
      <TruncatedList
        items={letters(7)}
        initialCount={3}
        renderItem={(it) => <span data-testid="item">{it}</span>}
      />,
    );
    fireEvent.click(screen.getByTestId("kx-truncated-more"));
    expect(screen.getAllByTestId("item")).toHaveLength(7);
    expect(screen.queryByTestId("kx-truncated-more")).toBeNull();
  });

  it("testIdPrefix scopes the affordance so multiple lists can co-exist on screen", () => {
    render(
      <>
        <TruncatedList
          items={letters(5)}
          initialCount={2}
          testIdPrefix="alpha"
          renderItem={(it) => <span data-testid="item-alpha">{it}</span>}
        />
        <TruncatedList
          items={letters(8)}
          initialCount={3}
          testIdPrefix="beta"
          renderItem={(it) => <span data-testid="item-beta">{it}</span>}
        />
      </>,
    );
    // Each list owns its own button with the prefixed test id.
    expect(screen.getByTestId("alpha-more")).toHaveTextContent("+3 more");
    expect(screen.getByTestId("beta-more")).toHaveTextContent("+5 more");
    // Default ``kx-truncated-more`` test id stays absent because both
    // lists set their own prefix.
    expect(screen.queryByTestId("kx-truncated-more")).toBeNull();
    // Expanding one does not expand the other.
    fireEvent.click(screen.getByTestId("alpha-more"));
    expect(screen.getAllByTestId("item-alpha")).toHaveLength(5);
    expect(screen.getAllByTestId("item-beta")).toHaveLength(3);
  });

  it("noun overrides the affordance label (singular)", () => {
    render(
      <TruncatedList
        items={letters(6)}
        initialCount={2}
        noun="match"
        renderItem={(it) => <span data-testid="item">{it}</span>}
      />,
    );
    const more = screen.getByTestId("kx-truncated-more");
    expect(more).toHaveTextContent("+4 match");
    expect(more).toHaveAttribute("aria-label", "Show 4 match");
  });

  it("renders nothing visible when given an empty list", () => {
    const { container } = render(
      <TruncatedList
        items={[]}
        initialCount={5}
        renderItem={(it) => <span data-testid="item">{it}</span>}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
