/**
 * Direct render tests for ``<SessionExpiredBanner/>`` (audit #227 +
 * #83 slice 3). Picked up by ``apps/widget``'s vitest run via
 * ``test.include`` extending into ``apps/_shared/**``.
 */

// ``@testing-library/jest-dom`` matchers are wired up by the host
// app's test-setup (apps/widget/src/test-setup.ts via the
// ``test.include`` extension). Importing it directly here would
// resolve a different node_modules tree depending on which app
// runs the test, so we lean on the host's setup instead.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SessionExpiredBanner } from "./SessionExpiredBanner";

describe("SessionExpiredBanner", () => {
  it("renders nothing when visible=false", () => {
    const { container } = render(
      <SessionExpiredBanner visible={false} onSignIn={() => undefined} />,
    );
    expect(container.firstChild).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("renders an alert role with the default action label when visible", () => {
    render(
      <SessionExpiredBanner visible={true} onSignIn={() => undefined} />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(alert).toHaveAttribute("aria-live", "polite");
    expect(
      screen.getByRole("button", { name: /sign in again/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/your session has expired/i),
    ).toBeInTheDocument();
  });

  it("renders the override action label when actionLabel is supplied", () => {
    render(
      <SessionExpiredBanner
        visible={true}
        onSignIn={() => undefined}
        actionLabel="Reload"
      />,
    );
    expect(
      screen.getByRole("button", { name: /^reload$/i }),
    ).toBeInTheDocument();
  });

  it("invokes onSignIn when the action button is clicked", () => {
    const onSignIn = vi.fn();
    render(<SessionExpiredBanner visible={true} onSignIn={onSignIn} />);
    fireEvent.click(screen.getByTestId("session-expired-banner-action"));
    expect(onSignIn).toHaveBeenCalledTimes(1);
  });

  it("composes the host className alongside the base kw-session-expired class", () => {
    render(
      <SessionExpiredBanner
        visible={true}
        onSignIn={() => undefined}
        className="kw-widget__banner"
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.className).toContain("kw-session-expired");
    expect(alert.className).toContain("kw-widget__banner");
  });
});
