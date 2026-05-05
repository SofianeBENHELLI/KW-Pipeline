/**
 * ForceAutoCorpusBanner rendering tests (EPIC-A A.8, #215).
 *
 * Pins the three render paths:
 *   1. ``visible=true``  → banner renders with the alert role and
 *      the env-var name in plain text.
 *   2. ``visible=false`` → banner does not render.
 *   3. The banner is non-dismissible (no buttons / close affordances).
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ForceAutoCorpusBanner } from "./ForceAutoCorpusBanner";

describe("<ForceAutoCorpusBanner />", () => {
  it("renders the alert when visible is true", () => {
    render(<ForceAutoCorpusBanner visible={true} />);
    const banner = screen.getByTestId("force-auto-corpus-banner");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveAttribute("role", "alert");
    expect(banner.textContent).toMatch(/Force-auto mode is active/);
    expect(banner.textContent).toMatch(/KW_HITL_FORCE_AUTO_CORPUS/);
  });

  it("does not render when visible is false", () => {
    render(<ForceAutoCorpusBanner visible={false} />);
    expect(screen.queryByTestId("force-auto-corpus-banner")).toBeNull();
  });

  it("renders no dismiss button — config alerts are non-dismissible", () => {
    render(<ForceAutoCorpusBanner visible={true} />);
    // Banner is informational only — buttons would let an end user
    // close a config alert that only the operator can clear.
    expect(screen.queryByRole("button")).toBeNull();
  });
});
