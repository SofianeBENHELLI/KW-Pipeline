/**
 * Provider-state transitions for ``useSessionGuard`` (audit #227 +
 * #83 slice 3). Picked up by ``apps/widget``'s vitest run via
 * ``test.include`` extending into ``apps/_shared/**``.
 */

// ``@testing-library/jest-dom`` matchers are wired up by the host
// app's test-setup (see apps/widget/src/test-setup.ts) so the
// import would otherwise resolve a different node_modules tree
// depending on which app runs the test.
import { act, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { SessionExpiredBanner } from "./SessionExpiredBanner";
import { SessionGuardProvider, useSessionGuard } from "./useSessionGuard";

const Probe: React.FC = () => {
  const session = useSessionGuard();
  return (
    <>
      <span data-testid="probe-state">{session.expired ? "expired" : "ok"}</span>
      <button data-testid="probe-trigger" onClick={session.trigger}>
        trigger
      </button>
      <button data-testid="probe-reset" onClick={session.reset}>
        reset
      </button>
      <SessionExpiredBanner
        visible={session.expired}
        onSignIn={() => undefined}
      />
    </>
  );
};

describe("useSessionGuard / SessionGuardProvider", () => {
  it("starts with expired=false", () => {
    render(
      <SessionGuardProvider>
        <Probe />
      </SessionGuardProvider>,
    );
    expect(screen.getByTestId("probe-state")).toHaveTextContent("ok");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("flips to expired=true after trigger() and back after reset()", () => {
    render(
      <SessionGuardProvider>
        <Probe />
      </SessionGuardProvider>,
    );
    fireEvent.click(screen.getByTestId("probe-trigger"));
    expect(screen.getByTestId("probe-state")).toHaveTextContent("expired");
    expect(screen.getByRole("alert")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("probe-reset"));
    expect(screen.getByTestId("probe-state")).toHaveTextContent("ok");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("collapses multiple trigger() calls onto a single banner instance", () => {
    render(
      <SessionGuardProvider>
        <Probe />
      </SessionGuardProvider>,
    );
    act(() => {
      fireEvent.click(screen.getByTestId("probe-trigger"));
      fireEvent.click(screen.getByTestId("probe-trigger"));
      fireEvent.click(screen.getByTestId("probe-trigger"));
    });
    // Single banner — context state is a boolean, idempotent on retrigger.
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("returns a no-op state when no provider is mounted", () => {
    // Render the probe without a provider — trigger/reset are silent
    // no-ops, expired stays false. This is the documented fallback so
    // components used outside the app shell don't crash.
    render(<Probe />);
    expect(screen.getByTestId("probe-state")).toHaveTextContent("ok");
    fireEvent.click(screen.getByTestId("probe-trigger"));
    expect(screen.getByTestId("probe-state")).toHaveTextContent("ok");
  });
});
