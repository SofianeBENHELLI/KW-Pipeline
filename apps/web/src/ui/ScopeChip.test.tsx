/**
 * ScopeChip rendering tests (EPIC-D #218 / #250).
 *
 * Pins the three render modes the chip ships with:
 *   1. Personal scope — single icon + label, ref folded into the title.
 *   2. Community scope — distinct kind class so the catalog row can
 *      colour-code at a glance, and tooltip surfaces the opaque ref.
 *   3. Multi-scope document — primary chip + "+N more" badge whose
 *      tooltip lists the remaining scopes for reviewers.
 *
 * Plus the empty-state guard for the catalog-read path: today
 * ``Document.scopes`` isn't on the wire (only ``UploadDocumentResponse``
 * carries it per #250), so the chip must surface a neutral
 * placeholder rather than collapsing to nothing.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScopeChip } from "./ScopeChip";
import type { ApiScope } from "../api/types";

function scope(
  kind: ApiScope["kind"],
  ref: string,
  added_by: string = "user-1",
): ApiScope {
  return {
    kind,
    ref,
    added_at: "2026-05-01T00:00:00Z",
    added_by,
    removed_at: null,
  };
}

describe("<ScopeChip />", () => {
  it("renders the personal scope as a single chip with ref in the tooltip", () => {
    render(<ScopeChip scopes={[scope("personal", "user-42")]} />);

    const chip = screen.getByTestId("scope-chip");
    expect(chip).toHaveAttribute("data-scope-kind", "personal");
    expect(chip.textContent).toMatch(/Personal/);
    // Tooltip carries the opaque ref so reviewers can verify which
    // user/community/project this doc belongs to without expanding
    // a popover. The label-only chip body keeps row width stable.
    expect(chip.title).toMatch(/user-42/);
    // Single-scope path → no "+N more" badge.
    expect(screen.queryByTestId("scope-chip-more")).toBeNull();
  });

  it("renders a community scope with the swym_community kind", () => {
    render(<ScopeChip scopes={[scope("swym_community", "comm-7")]} />);

    const chip = screen.getByTestId("scope-chip");
    expect(chip).toHaveAttribute("data-scope-kind", "swym_community");
    expect(chip.textContent).toMatch(/Community/);
    expect(chip.title).toMatch(/comm-7/);
    expect(chip.title).toMatch(/3DSwym/);
  });

  it("renders +N more for multi-scope documents and lists rest in tooltip", () => {
    render(
      <ScopeChip
        scopes={[
          scope("personal", "user-42"),
          scope("swym_community", "comm-7"),
          scope("project", "proj-99"),
        ]}
      />,
    );

    // Primary chip is the first scope — chosen deterministically
    // from the array order rather than by kind, so the chip mirrors
    // the catalog's insertion order rather than imposing a UI ranking.
    const primary = screen.getByTestId("scope-chip");
    expect(primary).toHaveAttribute("data-scope-kind", "personal");

    const more = screen.getByTestId("scope-chip-more");
    expect(more.textContent).toMatch(/\+2 more/);
    // Tooltip lists every remaining scope so a hover reveals
    // exactly where the doc landed without a separate popover.
    expect(more.title).toMatch(/comm-7/);
    expect(more.title).toMatch(/proj-99/);
  });

  it("renders an empty placeholder when scopes are missing", () => {
    // ``GET /documents`` doesn't yet return ``scopes`` on each Document
    // (only the upload response does, per #250). The chip surfaces a
    // neutral "No scope info" badge so the row layout is stable and
    // reviewers know the slot is intentional, not a missing field.
    render(<ScopeChip scopes={null} />);
    expect(screen.getByTestId("scope-chip-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("scope-chip")).toBeNull();
  });

  it("renders the empty placeholder for an explicit empty array too", () => {
    render(<ScopeChip scopes={[]} />);
    expect(screen.getByTestId("scope-chip-empty")).toBeInTheDocument();
  });
});
