/**
 * Coverage for the Admin navigation hub at ``/admin``.
 *
 * Pinned scenarios:
 * - Renders the three v1 cards (Archive / HITL / Audit log).
 * - Each card click drives ``useNavigate`` with the matching path —
 *   the whole card is the click target, not just the chevron.
 *
 * The hub itself does no API calls — the role is checked server-side
 * on the destination route, so the hub is reachable to everyone.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminHubView } from "../AdminHubView";

// ``useNavigate`` is the only side-effecting hook. Mock at the module
// level so each card click can be asserted against the call list
// without a full Routes harness.
const navigateMock = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

function renderHub() {
  return render(
    <MemoryRouter initialEntries={["/admin"]}>
      <AdminHubView />
    </MemoryRouter>,
  );
}

describe("AdminHubView", () => {
  beforeEach(() => navigateMock.mockReset());

  it("renders the three v1 admin cards with title + description", () => {
    renderHub();

    expect(screen.getByText("Administration")).toBeInTheDocument();
    expect(
      screen.getByText("Tools for operators with the admin role."),
    ).toBeInTheDocument();

    const grid = screen.getByTestId("admin-hub-grid");
    // 4 cards: archive / hitl / audit / taxonomy. Each has a testid
    // so the count is pinned even if titles drift.
    expect(grid.querySelectorAll("[data-testid^='admin-hub-card-']")).toHaveLength(4);

    expect(screen.getByTestId("admin-hub-card-archive")).toBeInTheDocument();
    expect(screen.getByTestId("admin-hub-card-hitl")).toBeInTheDocument();
    expect(screen.getByTestId("admin-hub-card-audit")).toBeInTheDocument();
    expect(screen.getByTestId("admin-hub-card-taxonomy")).toBeInTheDocument();

    // Sub-line copy is what an admin scans for; spot-check each.
    expect(
      screen.getByText(/Manage archived documents/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Inspect HITL routing state/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Filter the audit event store/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Inspect the DRAFT → CANDIDATE → VALIDATED → ARCHIVED/),
    ).toBeInTheDocument();
  });

  it("clicking a card calls useNavigate with the matching admin path", () => {
    renderHub();

    fireEvent.click(screen.getByTestId("admin-hub-card-archive"));
    expect(navigateMock).toHaveBeenCalledWith("/admin/archive");

    fireEvent.click(screen.getByTestId("admin-hub-card-hitl"));
    expect(navigateMock).toHaveBeenCalledWith("/admin/hitl");

    fireEvent.click(screen.getByTestId("admin-hub-card-audit"));
    expect(navigateMock).toHaveBeenCalledWith("/admin/audit");

    fireEvent.click(screen.getByTestId("admin-hub-card-taxonomy"));
    expect(navigateMock).toHaveBeenCalledWith("/admin/taxonomy");

    expect(navigateMock).toHaveBeenCalledTimes(4);
  });

  it("renders the role footer note", () => {
    renderHub();
    expect(
      screen.getByText(/All admin actions require the admin role/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Routes return 403 if your role is insufficient\./),
    ).toBeInTheDocument();
  });
});

