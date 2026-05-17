/**
 * DxShell + TopBar + IconRail tests.
 *
 * Pin the brand name, the top-tab + rail tile labels, the active-state
 * marking, the click handlers, and theme/density attribute scoping.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DxShell } from "./DxShell";
import { IconRail } from "./IconRail";
import { TopBar } from "./TopBar";

// IconRail / DxShell now host the cosmetic TaxonomyModeBadge which
// uses ``useNavigate`` and fetches on mount — wrap every render in a
// MemoryRouter and stub fetch so the rest of these specs stay focused
// on the chrome (ADR-018 §PR #346).
function renderInRouter(ui: ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

beforeEach(() => {
  // 503 keeps the badge silent — these tests don't probe its presence.
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response("{}", {
      status: 503,
      headers: { "Content-Type": "application/json" },
    }),
  );
});

afterEach(() => vi.restoreAllMocks());

describe("<TopBar />", () => {
  it("defaults brand to 'Knowledge Forge'", () => {
    render(<TopBar />);
    expect(screen.getByText("Knowledge Forge")).toBeInTheDocument();
  });

  it("respects the product override", () => {
    render(<TopBar product="My Forge" />);
    expect(screen.getByText("My Forge")).toBeInTheDocument();
  });

  it("renders the four top-nav tabs (Review, Search, Chat, Admin)", () => {
    render(<TopBar />);
    for (const label of ["Review", "Search", "Chat", "Admin"]) {
      expect(
        screen.getByRole("button", { name: new RegExp(label) }),
      ).toBeInTheDocument();
    }
  });

  it("never exposes a corpus-level Graph top-nav tab", () => {
    // Graph is a per-document tab inside the Review Workspace.
    // Corpus-wide graph exploration is the Knowledge Explorer's scope.
    render(<TopBar />);
    expect(screen.queryByRole("button", { name: /^Graph$/ })).toBeNull();
  });

  it("marks the active tab via aria-current and is-active", () => {
    render(<TopBar activeTab="search" />);
    const search = screen.getByRole("button", { name: /Search/ });
    expect(search).toHaveAttribute("aria-current", "page");
    expect(search).toHaveClass("is-active");
    const review = screen.getByRole("button", { name: /Review/ });
    expect(review).not.toHaveAttribute("aria-current");
  });

  it("invokes onTabSelect when a tab is clicked", () => {
    const onTabSelect = vi.fn();
    render(<TopBar onTabSelect={onTabSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /Search/ }));
    expect(onTabSelect).toHaveBeenCalledWith("search");
  });

  it("renders the status pill + crumb", () => {
    render(<TopBar status="alpha · ok" crumb="kw-pipeline · alpha" />);
    expect(screen.getByText("alpha · ok")).toBeInTheDocument();
    expect(screen.getByText("kw-pipeline · alpha")).toBeInTheDocument();
  });

  it("renders the avatar with provided initials", () => {
    render(<TopBar initials="SB" />);
    expect(screen.getByText("SB")).toBeInTheDocument();
  });

  it("invokes onOpenSettings on cog click", () => {
    const onOpenSettings = vi.fn();
    render(<TopBar onOpenSettings={onOpenSettings} />);
    fireEvent.click(screen.getByRole("button", { name: /Open settings/i }));
    expect(onOpenSettings).toHaveBeenCalled();
  });
});

describe("<IconRail />", () => {
  it("renders the six rail tiles", () => {
    renderInRouter(<IconRail />);
    for (const label of [
      "Activity",
      "Upload",
      "Review",
      "Search",
      "Document",
      "Settings",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("never exposes a corpus-level Graph rail tile", () => {
    // Same scope rule as the top-bar — graph is per-document only.
    renderInRouter(<IconRail />);
    expect(screen.queryByRole("button", { name: "Graph" })).toBeNull();
  });

  it("marks the active tile (defaults to 'review')", () => {
    renderInRouter(<IconRail />);
    const active = screen.getByRole("button", { name: "Review" });
    expect(active).toHaveAttribute("aria-current", "page");
    expect(active).toHaveClass("is-active");
  });

  it("respects an explicit active tile", () => {
    renderInRouter(<IconRail active="upload" />);
    expect(screen.getByRole("button", { name: "Upload" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("invokes onSelect with the tile id", () => {
    const onSelect = vi.fn();
    renderInRouter(<IconRail onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(onSelect).toHaveBeenCalledWith("upload");
  });
});

describe("<DxShell />", () => {
  it("scopes children under .orb-app with theme + density attrs", () => {
    const { container } = renderInRouter(
      <DxShell theme="dark" density="dense">
        <p>hello</p>
      </DxShell>,
    );
    const root = container.querySelector(".orb-app") as HTMLElement;
    expect(root).not.toBeNull();
    expect(root).toHaveAttribute("data-theme", "dark");
    expect(root).toHaveAttribute("data-density", "dense");
    expect(screen.getByText("hello")).toBeInTheDocument();
  });

  it("uses light/compact defaults", () => {
    const { container } = renderInRouter(
      <DxShell>
        <p>x</p>
      </DxShell>,
    );
    const root = container.querySelector(".orb-app") as HTMLElement;
    expect(root).toHaveAttribute("data-theme", "light");
    expect(root).toHaveAttribute("data-density", "compact");
  });

  it("forwards top-bar props (product = Knowledge Forge by default)", () => {
    renderInRouter(
      <DxShell>
        <p>x</p>
      </DxShell>,
    );
    expect(screen.getByText("Knowledge Forge")).toBeInTheDocument();
  });

  it("hides the rail when showRail=false", () => {
    renderInRouter(
      <DxShell showRail={false}>
        <p>x</p>
      </DxShell>,
    );
    expect(screen.queryByRole("navigation", { name: /Primary/i })).toBeNull();
  });

  it("has no axe-core a11y violations in the default render", async () => {
    const { container } = renderInRouter(
      <DxShell topBar={{ crumb: "kw-pipeline · alpha", initials: "KF" }}>
        <div>workspace body</div>
      </DxShell>,
    );
    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});
