/**
 * DxShell + TopBar + IconRail tests.
 *
 * Pin the brand name, the top-tab + rail tile labels, the active-state
 * marking, the click handlers, and theme/density attribute scoping.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it, vi } from "vitest";

import { DxShell } from "./DxShell";
import { IconRail } from "./IconRail";
import { TopBar } from "./TopBar";

describe("<TopBar />", () => {
  it("defaults brand to 'Knowledge Forge'", () => {
    render(<TopBar />);
    expect(screen.getByText("Knowledge Forge")).toBeInTheDocument();
  });

  it("respects the product override", () => {
    render(<TopBar product="My Forge" />);
    expect(screen.getByText("My Forge")).toBeInTheDocument();
  });

  it("renders all five top-nav tabs (Review, Graph, Search, Chat, Admin)", () => {
    render(<TopBar />);
    for (const label of ["Review", "Graph", "Search", "Chat", "Admin"]) {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  it("marks the active tab via aria-current and is-active", () => {
    render(<TopBar activeTab="graph" />);
    const graph = screen.getByRole("button", { name: /Graph/ });
    expect(graph).toHaveAttribute("aria-current", "page");
    expect(graph).toHaveClass("is-active");
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
  it("renders the seven rail tiles", () => {
    render(<IconRail />);
    for (const label of [
      "Activity",
      "Upload",
      "Review",
      "Search",
      "Document",
      "Graph",
      "Settings",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("marks the active tile (defaults to 'review')", () => {
    render(<IconRail />);
    const active = screen.getByRole("button", { name: "Review" });
    expect(active).toHaveAttribute("aria-current", "page");
    expect(active).toHaveClass("is-active");
  });

  it("respects an explicit active tile", () => {
    render(<IconRail active="graph" />);
    expect(screen.getByRole("button", { name: "Graph" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("invokes onSelect with the tile id", () => {
    const onSelect = vi.fn();
    render(<IconRail onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(onSelect).toHaveBeenCalledWith("upload");
  });
});

describe("<DxShell />", () => {
  it("scopes children under .orb-app with theme + density attrs", () => {
    const { container } = render(
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
    const { container } = render(
      <DxShell>
        <p>x</p>
      </DxShell>,
    );
    const root = container.querySelector(".orb-app") as HTMLElement;
    expect(root).toHaveAttribute("data-theme", "light");
    expect(root).toHaveAttribute("data-density", "compact");
  });

  it("forwards top-bar props (product = Knowledge Forge by default)", () => {
    render(
      <DxShell>
        <p>x</p>
      </DxShell>,
    );
    expect(screen.getByText("Knowledge Forge")).toBeInTheDocument();
  });

  it("hides the rail when showRail=false", () => {
    render(
      <DxShell showRail={false}>
        <p>x</p>
      </DxShell>,
    );
    expect(screen.queryByRole("navigation", { name: /Primary/i })).toBeNull();
  });

  it("has no axe-core a11y violations in the default render", async () => {
    const { container } = render(
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
