/**
 * Tests for AdminHub + PurgeDialog + PurgeAllDialog + SettingsModal.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { AdminHub } from "./AdminHub";
import { PurgeDialog } from "./PurgeDialog";
import { PurgeAllDialog } from "./PurgeAllDialog";
import { SettingsModal } from "./SettingsModal";
import { ORBITAL_PURGE_ALL_PHRASE } from "../../api/types";

describe("<AdminHub />", () => {
  it("renders four tiles linking to the right destinations", () => {
    render(
      <MemoryRouter>
        <AdminHub />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("kf-admin-tile-hitl")).toHaveAttribute(
      "href",
      "/admin/hitl",
    );
    expect(screen.getByTestId("kf-admin-tile-audit")).toHaveAttribute(
      "href",
      "/admin/audit",
    );
    expect(screen.getByTestId("kf-admin-tile-archive")).toHaveAttribute(
      "href",
      "/admin/archive",
    );
    expect(screen.getByTestId("kf-admin-tile-config")).toHaveAttribute(
      "href",
      "/kf/settings",
    );
  });
});

describe("<PurgeDialog />", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns null when closed", () => {
    const { container } = render(
      <PurgeDialog
        open={false}
        documentId="doc-x"
        filename="x.txt"
        onConfirm={async () => {}}
        onCancel={() => {}}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("disables Purge until the typed filename matches exactly", async () => {
    const onConfirm = vi.fn(async () => {});
    render(
      <PurgeDialog
        open
        documentId="doc-x"
        filename="policy.txt"
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    const button = screen.getByTestId("kf-purge-confirm");
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Filename confirmation"), {
      target: { value: "wrong" },
    });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Filename confirmation"), {
      target: { value: "policy.txt" },
    });
    expect(button).not.toBeDisabled();
    fireEvent.click(button);
    await waitFor(() => expect(onConfirm).toHaveBeenCalled());
  });

  it("surfaces an error if onConfirm rejects", async () => {
    render(
      <PurgeDialog
        open
        documentId="doc-x"
        filename="x.txt"
        onConfirm={async () => {
          throw new Error("412 phrase mismatch");
        }}
        onCancel={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText("Filename confirmation"), {
      target: { value: "x.txt" },
    });
    fireEvent.click(screen.getByTestId("kf-purge-confirm"));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/412/),
    );
  });
});

describe("<PurgeAllDialog />", () => {
  afterEach(() => vi.restoreAllMocks());

  it("requires the rotating phrase + a 5-second cool-off before submit", async () => {
    vi.useFakeTimers();
    const onConfirm = vi.fn(async () => {});
    render(
      <PurgeAllDialog
        open
        documentCount={42}
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    const button = screen.getByTestId("kf-purge-all-confirm");
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Purge phrase"), {
      target: { value: ORBITAL_PURGE_ALL_PHRASE },
    });
    expect(button).toBeDisabled(); // cool-off > 0
    expect(screen.getByTestId("kf-purge-cooloff")).toHaveTextContent("5s");
    await vi.advanceTimersByTimeAsync(5500);
    expect(button).not.toBeDisabled();
    vi.useRealTimers();
    fireEvent.click(button);
    await waitFor(() => expect(onConfirm).toHaveBeenCalled());
  });

  it("renders the document count in the body", () => {
    render(
      <PurgeAllDialog open documentCount={2116} onConfirm={async () => {}} onCancel={() => {}} />,
    );
    expect(
      screen.getByText(/2,116 documents will be destroyed/i),
    ).toBeInTheDocument();
  });
});

describe("<SettingsModal />", () => {
  it("opens with the Account tab and switches to Pipeline", () => {
    render(
      <SettingsModal
        open
        onClose={() => {}}
        config={{
          pipelineName: "kw-pipeline",
          autoValidateThreshold: 0.85,
        }}
      />,
    );
    expect(screen.getByTestId("kf-settings-modal")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Account", current: "page" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Pipeline" }));
    expect(screen.getByText("kw-pipeline")).toBeInTheDocument();
    expect(screen.getByText("0.85")).toBeInTheDocument();
  });

  it("closes via the X button", () => {
    const onClose = vi.fn();
    render(<SettingsModal open onClose={onClose} />);
    fireEvent.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalled();
  });
});
