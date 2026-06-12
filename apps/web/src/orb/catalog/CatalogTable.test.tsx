/**
 * CatalogTable — pin column toggles, header checkbox, sort, bulk-bar
 * appearance, and the open callback.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ApiDocument } from "../../api/types";
import { CatalogTable } from "./CatalogTable";

const DOC_A: ApiDocument = {
  origin: "operator",
  id: "doc-a",
  original_filename: "alpha.md",
  latest_version_id: "ver-a",
  created_at: "2026-05-11T14:22:08Z",
  archived_at: null,
  scopes: [
    { kind: "project", ref: "p1", added_at: "x", added_by: "a", removed_at: null },
  ],
  versions: [
    {
      id: "ver-a",
      document_id: "doc-a",
      version_number: 1,
      filename: "alpha.md",
      content_type: "text/markdown",
      file_size: 4096,
      sha256: "ha",
      storage_uri: "file://a",
      status: "NEEDS_REVIEW",
      duplicate_of_version_id: null,
      failure_reason: null,
      reviewer_note: null,
      reviewed_at: null,
      created_at: "2026-05-11T14:22:08Z",
    },
  ],
};

const DOC_B: ApiDocument = {
  ...DOC_A,
  id: "doc-b",
  original_filename: "beta.md",
  latest_version_id: "ver-b",
  versions: [{ ...DOC_A.versions[0], id: "ver-b", document_id: "doc-b", filename: "beta.md", file_size: 2048, status: "VALIDATED", created_at: "2026-05-10T09:00:00Z" }],
};

describe("<CatalogTable />", () => {
  it("renders one row per document", () => {
    render(<CatalogTable documents={[DOC_A, DOC_B]} />);
    expect(screen.getByText("alpha.md")).toBeInTheDocument();
    expect(screen.getByText("beta.md")).toBeInTheDocument();
  });

  it("default columns include Filename, ID, Status, Versions, Bytes, Scope, Uploaded", () => {
    render(<CatalogTable documents={[DOC_A]} />);
    const headers = screen
      .getAllByRole("columnheader")
      .map((th) => (th.textContent ?? "").trim());
    for (const expected of [
      "Filename",
      "ID",
      "Status",
      "Versions",
      "Bytes",
      "Scope",
      "Uploaded",
    ]) {
      expect(
        headers.some((h) => h.startsWith(expected)),
        `expected a header starting with "${expected}", got: ${headers.join(", ")}`,
      ).toBe(true);
    }
  });

  it("toggling a column hides the matching cells", () => {
    render(<CatalogTable documents={[DOC_A]} />);
    const idToggle = screen.getByRole("button", { name: "ID", pressed: true });
    fireEvent.click(idToggle);
    // The header still shows the toggle chip (text "ID") in the column-toggles.
    // The table column header should be gone — there's now no <th> with "ID".
    const ths = screen.getAllByRole("columnheader");
    expect(ths.find((h) => /^ID/.test(h.textContent ?? ""))).toBeUndefined();
  });

  it("the header checkbox selects all visible rows", () => {
    render(<CatalogTable documents={[DOC_A, DOC_B]} />);
    const headerCheckbox = screen.getByLabelText(/Select all/);
    fireEvent.click(headerCheckbox);
    const region = screen.getByRole("region", { name: /Bulk actions/ });
    expect(within(region).getByText("2 selected")).toBeInTheDocument();
  });

  it("clicking a row checkbox reveals the bulk bar; clear empties it", () => {
    render(<CatalogTable documents={[DOC_A, DOC_B]} />);
    fireEvent.click(screen.getByLabelText("Select alpha.md"));
    const region = screen.getByRole("region", { name: /Bulk actions/ });
    expect(within(region).getByText("1 selected")).toBeInTheDocument();
    fireEvent.click(within(region).getByText("clear"));
    expect(screen.queryByRole("region", { name: /Bulk actions/ })).toBeNull();
  });

  it("clicking the filename invokes onOpen", () => {
    const onOpen = vi.fn();
    render(<CatalogTable documents={[DOC_A]} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button", { name: "alpha.md" }));
    expect(onOpen).toHaveBeenCalledWith("doc-a");
  });

  it("clicking the bulk Run pipeline / Purge buttons fires the callbacks", () => {
    const onRunBulk = vi.fn();
    const onPurgeBulk = vi.fn();
    render(
      <CatalogTable
        documents={[DOC_A, DOC_B]}
        onRunBulk={onRunBulk}
        onPurgeBulk={onPurgeBulk}
      />,
    );
    fireEvent.click(screen.getByLabelText("Select alpha.md"));
    fireEvent.click(screen.getByLabelText("Select beta.md"));
    fireEvent.click(screen.getByRole("button", { name: /Run pipeline/ }));
    expect(onRunBulk).toHaveBeenCalledWith(["doc-a", "doc-b"]);
    fireEvent.click(screen.getByRole("button", { name: /^Purge$/ }));
    expect(onPurgeBulk).toHaveBeenCalledWith(["doc-a", "doc-b"]);
  });

  it("renders a loading state", () => {
    render(<CatalogTable documents={[]} loading />);
    expect(screen.getByText(/Loading catalog…/i)).toBeInTheDocument();
  });

  it("renders an error message", () => {
    render(<CatalogTable documents={[]} errorMessage="boom" />);
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
  });

  it("renders an empty state", () => {
    render(<CatalogTable documents={[]} />);
    expect(
      screen.getByText(/No documents match the current filters/i),
    ).toBeInTheDocument();
  });

  it("filename column sort flips on click", () => {
    render(<CatalogTable documents={[DOC_A, DOC_B]} />);
    fireEvent.click(screen.getByRole("button", { name: /Filename/ }));
    // First click sets asc → should show ↑
    expect(
      screen.getByRole("button", { name: /Filename ↑/ }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Filename ↑/ }));
    expect(
      screen.getByRole("button", { name: /Filename ↓/ }),
    ).toBeInTheDocument();
  });
});
