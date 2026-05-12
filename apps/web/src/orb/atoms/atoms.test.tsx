/**
 * Atom-level smoke tests for the Knowledge Forge design system.
 *
 * Each atom asserts: (1) it renders the expected DOM, (2) the right CSS
 * class is applied (the visual contract enforced via tokens.css), and
 * (3) common a11y attributes are present. CSS is disabled in vitest
 * (`css: false` in `vitest.config.ts`) so we cannot assert computed
 * styles — class assertions stand in.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it, vi } from "vitest";

import { Btn } from "./Btn";
import { Card, CardHead } from "./Card";
import { Kbd } from "./Kbd";
import { MetaRow } from "./MetaRow";
import { ScopeChip } from "./ScopeChip";
import { SectionH } from "./SectionH";
import { StatusBadge } from "./StatusBadge";
import { OrbI } from "./icons";

describe("<StatusBadge />", () => {
  it("renders every known FSM status with the correct class + label", () => {
    const cases = [
      ["STORED", "STORED", "orb-status--stored"],
      ["EXTRACTING", "EXTRACTING", "orb-status--extracting"],
      ["EXTRACTED", "EXTRACTED", "orb-status--extracted"],
      ["SEMANTIC_READY", "SEMANTIC_READY", "orb-status--semantic"],
      ["NEEDS_REVIEW", "NEEDS_REVIEW", "orb-status--review"],
      ["VALIDATED", "VALIDATED", "orb-status--validated"],
      ["REJECTED", "REJECTED", "orb-status--rejected"],
      ["FAILED", "FAILED", "orb-status--failed"],
      ["DUPLICATE_DETECTED", "DUPLICATE", "orb-status--duplicate"],
    ] as const;

    for (const [status, label, cls] of cases) {
      const { unmount } = render(<StatusBadge status={status} />);
      const node = screen.getByRole("status", { name: label });
      expect(node).toHaveClass("orb-status");
      expect(node).toHaveClass(cls);
      expect(node).toHaveTextContent(label);
      unmount();
    }
  });

  it("falls through to STORED for unknown statuses (graceful degradation)", () => {
    render(<StatusBadge status="WHO_KNOWS" />);
    expect(screen.getByRole("status", { name: "STORED" })).toHaveClass(
      "orb-status--stored",
    );
  });
});

describe("<ScopeChip />", () => {
  it("renders the three known scopes with the correct dot color CSS var", () => {
    const cases = [
      ["personal", "personal", "var(--orb-info)"],
      ["community", "community", "var(--orb-purple)"],
      ["project", "project", "var(--orb-ok)"],
    ] as const;
    for (const [scope, label, color] of cases) {
      const { unmount, container } = render(<ScopeChip scope={scope} />);
      expect(screen.getByText(label)).toBeInTheDocument();
      const chip = container.querySelector(".orb-chip") as HTMLElement | null;
      expect(chip).not.toBeNull();
      expect(chip!.style.color).toBe(color);
      unmount();
    }
  });

  it("falls through to personal for unknown scopes", () => {
    render(<ScopeChip scope="unknown" />);
    expect(screen.getByText("personal")).toBeInTheDocument();
  });
});

describe("<Btn />", () => {
  it("renders a default button + emits onClick", () => {
    const onClick = vi.fn();
    render(<Btn onClick={onClick}>Hello</Btn>);
    const btn = screen.getByRole("button", { name: "Hello" });
    expect(btn).toHaveClass("orb-btn");
    expect(btn).toHaveAttribute("type", "button");
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("applies kind / xs / iconOnly modifier classes", () => {
    render(
      <>
        <Btn kind="primary">P</Btn>
        <Btn kind="ghost">G</Btn>
        <Btn kind="danger">D</Btn>
        <Btn xs>X</Btn>
        <Btn iconOnly icon={OrbI.search} aria-label="search" />
      </>,
    );
    expect(screen.getByRole("button", { name: "P" })).toHaveClass("orb-btn--primary");
    expect(screen.getByRole("button", { name: "G" })).toHaveClass("orb-btn--ghost");
    expect(screen.getByRole("button", { name: "D" })).toHaveClass("orb-btn--danger");
    expect(screen.getByRole("button", { name: "X" })).toHaveClass("orb-btn--xs");
    expect(screen.getByRole("button", { name: "search" })).toHaveClass("orb-btn--icon");
  });

  it("disabled prop blocks click", () => {
    const onClick = vi.fn();
    render(
      <Btn disabled onClick={onClick}>
        Nope
      </Btn>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Nope" }));
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe("<Kbd />", () => {
  it("renders inside a <kbd> element with .orb-kbd", () => {
    const { container } = render(<Kbd>Esc</Kbd>);
    const node = container.querySelector("kbd");
    expect(node).not.toBeNull();
    expect(node).toHaveTextContent("Esc");
    expect(node).toHaveClass("orb-kbd");
  });
});

describe("<MetaRow />", () => {
  it("renders a key/value pair with mono key column", () => {
    const { container } = render(<MetaRow k="ID">doc_8a3f</MetaRow>);
    const row = container.querySelector(".orb-meta-row") as HTMLElement;
    expect(row).not.toBeNull();
    expect(row.querySelector(".k")).toHaveTextContent("ID");
    expect(row.querySelector(".v")).toHaveTextContent("doc_8a3f");
  });
});

describe("<Card /> + <CardHead />", () => {
  it("renders a card with head + body content", () => {
    const { container } = render(
      <Card>
        <CardHead right={<button type="button">refresh</button>}>
          <SectionH>Lifecycle</SectionH>
        </CardHead>
        <div data-testid="body">body</div>
      </Card>,
    );
    expect(container.querySelector(".orb-card")).not.toBeNull();
    expect(screen.getByText("Lifecycle")).toHaveClass("orb-section-h");
    expect(screen.getByRole("button", { name: "refresh" })).toBeInTheDocument();
    expect(screen.getByTestId("body")).toBeInTheDocument();
  });
});

describe("OrbI namespace", () => {
  it("exposes a stable, frozen icon set", () => {
    expect(Object.isFrozen(OrbI)).toBe(true);
    // Non-exhaustive but representative spot-check.
    expect(OrbI.search).toBeTruthy();
    expect(OrbI.check).toBeTruthy();
    expect(OrbI.x).toBeTruthy();
    expect(OrbI.cog).toBeTruthy();
  });
});

describe("axe-core (atoms have no a11y violations)", () => {
  it("renders a representative sample with zero violations", async () => {
    const { container } = render(
      <div className="orb-app" data-theme="light">
        <StatusBadge status="VALIDATED" />
        <ScopeChip scope="project" />
        <Btn>Default</Btn>
        <Btn kind="primary" icon={OrbI.check}>
          Validate
        </Btn>
        <Card>
          <CardHead>
            <SectionH>Detail</SectionH>
          </CardHead>
          <MetaRow k="ID">doc_8a3f</MetaRow>
        </Card>
        <Kbd>v</Kbd>
      </div>,
    );

    // color-contrast needs computed styles that jsdom can't produce.
    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});
