/**
 * Phase-0 atom smoke tests — render each atom in isolation, assert the
 * key class names + accessible labels survive. Full visual verification
 * happens against the live preview build; vitest just pins the contract.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Btn, Card, Chip, Input, Kbd, MetaRow, Mono, Rule, SectionHeading } from "./atoms";
import { Icon } from "./Icon";
import { OrbScopeChip } from "./ScopeChip";
import { OrbStatusBadge } from "./StatusBadge";

describe("<Btn />", () => {
  it("renders a button with default kind + size", () => {
    render(<Btn>Click me</Btn>);
    const btn = screen.getByRole("button", { name: "Click me" });
    expect(btn).toHaveClass("orb-btn");
    expect(btn).not.toHaveClass("orb-btn--primary");
  });

  it("applies primary, danger, ghost, xs, and iconOnly modifiers", () => {
    render(
      <>
        <Btn kind="primary">P</Btn>
        <Btn kind="danger">D</Btn>
        <Btn kind="ghost">G</Btn>
        <Btn size="xs">X</Btn>
        <Btn iconOnly icon={<span>•</span>} aria-label="dot" />
      </>,
    );
    expect(screen.getByRole("button", { name: "P" })).toHaveClass("orb-btn--primary");
    expect(screen.getByRole("button", { name: "D" })).toHaveClass("orb-btn--danger");
    expect(screen.getByRole("button", { name: "G" })).toHaveClass("orb-btn--ghost");
    expect(screen.getByRole("button", { name: "X" })).toHaveClass("orb-btn--xs");
    expect(screen.getByRole("button", { name: "dot" })).toHaveClass("orb-btn--icon");
  });

  it("respects disabled and fires onClick when enabled", () => {
    const onClick = vi.fn();
    render(
      <>
        <Btn onClick={onClick}>Go</Btn>
        <Btn onClick={onClick} disabled>
          Stop
        </Btn>
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("<Input />", () => {
  it("forwards props and renders the orb-input class", () => {
    render(<Input placeholder="Filter…" aria-label="filter" />);
    const input = screen.getByLabelText("filter");
    expect(input).toHaveClass("orb-input");
    expect(input).toHaveAttribute("placeholder", "Filter…");
  });
});

describe("<Kbd />, <Card />, <Rule />, <SectionHeading />, <Mono />", () => {
  it("renders structural atoms with their class names", () => {
    render(
      <>
        <Kbd>/</Kbd>
        <Card data-testid="card">card</Card>
        <Rule />
        <Rule vertical />
        <SectionHeading>Heading</SectionHeading>
        <Mono>doc_8a3f</Mono>
      </>,
    );
    expect(screen.getByText("/")).toHaveClass("orb-kbd");
    expect(screen.getByTestId("card")).toHaveClass("orb-card");
    expect(screen.getByText("Heading")).toHaveClass("orb-section-h");
    expect(screen.getByText("doc_8a3f")).toHaveClass("orb-mono");
    const separators = screen.getAllByRole("separator");
    expect(separators).toHaveLength(2);
    expect(separators[0]).toHaveClass("orb-rule");
    expect(separators[1]).toHaveClass("orb-vrule");
  });
});

describe("<MetaRow />", () => {
  it("renders label + value with the expected sub-classes", () => {
    const { container } = render(<MetaRow label="sha256">bec2595e…</MetaRow>);
    expect(container.querySelector(".orb-meta-row")).not.toBeNull();
    expect(container.querySelector(".orb-meta-row .k")?.textContent).toBe("sha256");
    expect(container.querySelector(".orb-meta-row .v")?.textContent).toBe("bec2595e…");
  });
});

describe("<Chip />", () => {
  it("renders a chip with an optional colored dot", () => {
    render(<Chip dot color="var(--orb-info)">team</Chip>);
    expect(screen.getByText("team")).toBeInTheDocument();
  });
});

describe("<OrbStatusBadge />", () => {
  it("maps known statuses to the orb-status modifier", () => {
    render(
      <>
        <OrbStatusBadge status="VALIDATED" />
        <OrbStatusBadge status="NEEDS_REVIEW" />
        <OrbStatusBadge status="FAILED" />
        <OrbStatusBadge status="DUPLICATE_DETECTED" />
      </>,
    );
    expect(screen.getByLabelText("status: VALIDATED")).toHaveClass("orb-status--validated");
    expect(screen.getByLabelText("status: NEEDS_REVIEW")).toHaveClass("orb-status--review");
    expect(screen.getByLabelText("status: FAILED")).toHaveClass("orb-status--failed");
    expect(screen.getByLabelText("status: DUPLICATE")).toHaveClass("orb-status--duplicate");
  });

  it("falls back to UNKNOWN for unrecognized statuses", () => {
    render(<OrbStatusBadge status={"WHATEVER" as unknown as string} />);
    expect(screen.getByLabelText("status: UNKNOWN")).toBeInTheDocument();
  });
});

describe("<OrbScopeChip />", () => {
  it("renders each scope kind with the right label", () => {
    render(
      <>
        <OrbScopeChip scope="personal" />
        <OrbScopeChip scope="swym_community" />
        <OrbScopeChip scope="project" />
      </>,
    );
    expect(screen.getByLabelText("scope: personal")).toBeInTheDocument();
    expect(screen.getByLabelText("scope: community")).toBeInTheDocument();
    expect(screen.getByLabelText("scope: project")).toBeInTheDocument();
  });
});

describe("<Icon />", () => {
  it("renders an svg with the requested name", () => {
    const { container } = render(<Icon name="search" data-testid="icon" />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg).toHaveAttribute("aria-hidden", "true");
  });

  it("exposes role=img when an aria-label is supplied", () => {
    render(<Icon name="bolt" aria-label="run pipeline" />);
    expect(screen.getByRole("img", { name: "run pipeline" })).toBeInTheDocument();
  });
});
