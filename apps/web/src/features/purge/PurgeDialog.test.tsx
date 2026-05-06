/**
 * #292 §5 — Orbital purge confirmation modal.
 *
 * Asserts the confirmation rules: type-the-filename gate, Enter to
 * submit when the filename matches, Escape / backdrop click to
 * cancel, and the success path that calls back to the parent.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>(
    "../../api/client",
  );
  return {
    ...actual,
    orbitalPurgeDocument: vi.fn(),
  };
});

import { orbitalPurgeDocument } from "../../api/client";
import { PurgeDialog } from "./PurgeDialog";

const mockedPurge = vi.mocked(orbitalPurgeDocument);

afterEach(() => vi.clearAllMocks());

const TARGET = {
  id: "doc-1",
  original_filename: "policy.pdf",
  version_count: 2,
};

describe("PurgeDialog", () => {
  it("renders nothing when document is null", () => {
    const { container } = render(
      <PurgeDialog document={null} onCancel={() => {}} onPurged={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("disables the confirm button until the operator types the filename exactly", () => {
    render(
      <PurgeDialog
        document={TARGET}
        onCancel={() => {}}
        onPurged={() => {}}
      />,
    );
    const confirm = screen.getByTestId("purge-dialog-confirm");
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByTestId("purge-dialog-input"), {
      target: { value: "policy" },
    });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByTestId("purge-dialog-input"), {
      target: { value: "policy.pdf" },
    });
    expect(confirm).not.toBeDisabled();
  });

  it("calls orbitalPurgeDocument with the typed filename and reports success", async () => {
    mockedPurge.mockResolvedValue({
      document_id: "doc-1",
      original_filename: "policy.pdf",
      archived_at: "2026-05-06T00:00:00Z",
      versions_purged: [],
      kg_subgraph_purged: false,
    });
    const onPurged = vi.fn();

    render(
      <PurgeDialog
        document={TARGET}
        onCancel={() => {}}
        onPurged={onPurged}
      />,
    );
    fireEvent.change(screen.getByTestId("purge-dialog-input"), {
      target: { value: "policy.pdf" },
    });
    fireEvent.click(screen.getByTestId("purge-dialog-confirm"));

    await waitFor(() => expect(mockedPurge).toHaveBeenCalledWith("doc-1", "policy.pdf"));
    await waitFor(() => expect(onPurged).toHaveBeenCalledTimes(1));
  });

  it("surfaces a danger banner when the purge call fails", async () => {
    mockedPurge.mockRejectedValue(new Error("403 Forbidden"));

    render(
      <PurgeDialog
        document={TARGET}
        onCancel={() => {}}
        onPurged={() => {}}
      />,
    );
    fireEvent.change(screen.getByTestId("purge-dialog-input"), {
      target: { value: "policy.pdf" },
    });
    fireEvent.click(screen.getByTestId("purge-dialog-confirm"));

    expect(await screen.findByRole("alert")).toHaveTextContent(/403 Forbidden/);
  });

  it("Escape inside the input triggers cancel", () => {
    const onCancel = vi.fn();
    render(
      <PurgeDialog
        document={TARGET}
        onCancel={onCancel}
        onPurged={() => {}}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("purge-dialog-input"), {
      key: "Escape",
    });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("Enter submits when filename matches", async () => {
    mockedPurge.mockResolvedValue({
      document_id: "doc-1",
      original_filename: "policy.pdf",
      archived_at: "2026-05-06T00:00:00Z",
      versions_purged: [],
      kg_subgraph_purged: false,
    });
    render(
      <PurgeDialog
        document={TARGET}
        onCancel={() => {}}
        onPurged={() => {}}
      />,
    );
    const input = screen.getByTestId("purge-dialog-input");
    fireEvent.change(input, { target: { value: "policy.pdf" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(mockedPurge).toHaveBeenCalledTimes(1));
  });
});
