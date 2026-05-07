/**
 * #292 §5 — bulk purge confirmation modal.
 *
 * The operator types ``PURGE ALL DOCUMENTS`` (the literal phrase the
 * backend demands) before the danger button enables. Same Enter to
 * submit / Escape to cancel ergonomics as the per-doc dialog.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>(
    "../../api/client",
  );
  return {
    ...actual,
    orbitalPurgeAll: vi.fn(),
  };
});

import { orbitalPurgeAll } from "../../api/client";
import { PurgeAllDialog } from "./PurgeAllDialog";

const mockedPurgeAll = vi.mocked(orbitalPurgeAll);

afterEach(() => vi.clearAllMocks());

describe("PurgeAllDialog", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <PurgeAllDialog
        open={false}
        documentCount={3}
        onCancel={() => {}}
        onPurged={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("disables the confirm button until the phrase matches exactly", () => {
    render(
      <PurgeAllDialog
        open
        documentCount={3}
        onCancel={() => {}}
        onPurged={() => {}}
      />,
    );
    const confirm = screen.getByTestId("purge-all-dialog-confirm");
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByTestId("purge-all-dialog-input"), {
      target: { value: "purge all documents" }, // wrong case
    });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByTestId("purge-all-dialog-input"), {
      target: { value: "PURGE ALL DOCUMENTS" },
    });
    expect(confirm).not.toBeDisabled();
  });

  it("submits and reports success", async () => {
    mockedPurgeAll.mockResolvedValue({
      documents_purged: 3,
      failed: 0,
      results: [],
      failures: [],
    });
    const onPurged = vi.fn();

    render(
      <PurgeAllDialog
        open
        documentCount={3}
        onCancel={() => {}}
        onPurged={onPurged}
      />,
    );
    fireEvent.change(screen.getByTestId("purge-all-dialog-input"), {
      target: { value: "PURGE ALL DOCUMENTS" },
    });
    fireEvent.click(screen.getByTestId("purge-all-dialog-confirm"));

    await waitFor(() =>
      expect(mockedPurgeAll).toHaveBeenCalledWith("PURGE ALL DOCUMENTS"),
    );
    await waitFor(() => expect(onPurged).toHaveBeenCalledTimes(1));
  });

  it("Escape cancels", () => {
    const onCancel = vi.fn();
    render(
      <PurgeAllDialog
        open
        documentCount={3}
        onCancel={onCancel}
        onPurged={() => {}}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("purge-all-dialog-input"), {
      key: "Escape",
    });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("surfaces a danger banner on failure", async () => {
    mockedPurgeAll.mockRejectedValue(new Error("503 KW_BACKEND_DOWN"));

    render(
      <PurgeAllDialog
        open
        documentCount={2}
        onCancel={() => {}}
        onPurged={() => {}}
      />,
    );
    fireEvent.change(screen.getByTestId("purge-all-dialog-input"), {
      target: { value: "PURGE ALL DOCUMENTS" },
    });
    fireEvent.click(screen.getByTestId("purge-all-dialog-confirm"));

    expect(await screen.findByRole("alert")).toHaveTextContent(/503 KW_BACKEND_DOWN/);
  });
});
