/**
 * CatalogTable — Phase 1 catalog grid. Pins the empty/error/loading
 * states and the sort + select interactions against a small mocked
 * ApiDocument list.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { components } from "../api/generated/schema";

import { CatalogTable } from "./CatalogTable";

type ApiDocument = components["schemas"]["Document"];

function makeDoc(overrides: Partial<ApiDocument> = {}): ApiDocument {
  const id = overrides.id ?? "doc_test_1";
  return {
    id,
    archived_at: null,
    created_at: "2026-05-11T12:00:00Z",
    latest_version_id: `${id}_v1`,
    original_filename: "test.pdf",
    scopes: [],
    versions: [
      {
        id: `${id}_v1`,
        document_id: id,
        version_number: 1,
        filename: "test.pdf",
        content_type: "application/pdf",
        file_size: 12345,
        sha256: "a".repeat(64),
        storage_uri: "memory://test",
        status: "NEEDS_REVIEW",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: "2026-05-11T12:00:00Z",
      } as ApiDocument["versions"][number],
    ],
    ...overrides,
  };
}

describe("<CatalogTable />", () => {
  it("renders a loading state when no documents have arrived yet", () => {
    render(<CatalogTable documents={[]} loading />);
    expect(screen.getByText("Loading documents…")).toBeInTheDocument();
  });

  it("renders an empty state when the filter yields no rows", () => {
    render(<CatalogTable documents={[]} loading={false} />);
    expect(screen.getByText("No documents match the current filter.")).toBeInTheDocument();
  });

  it("renders an error banner when the fetch fails", () => {
    render(<CatalogTable documents={[]} error="boom" />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Failed to load catalog: boom");
  });

  it("renders one row per document with status badge + filename", () => {
    render(
      <CatalogTable
        documents={[
          makeDoc({ id: "doc_a", original_filename: "alpha.pdf" }),
          makeDoc({ id: "doc_b", original_filename: "bravo.pdf" }),
        ]}
      />,
    );
    expect(screen.getByText("alpha.pdf")).toBeInTheDocument();
    expect(screen.getByText("bravo.pdf")).toBeInTheDocument();
    expect(screen.getAllByLabelText(/^status:/)).toHaveLength(2);
  });

  it("toggles sort direction when a header is clicked twice", () => {
    render(
      <CatalogTable
        documents={[
          makeDoc({ id: "doc_z", original_filename: "zeta.pdf" }),
          makeDoc({ id: "doc_a", original_filename: "alpha.pdf" }),
        ]}
      />,
    );
    const filenameHeader = screen.getByRole("button", { name: /Filename/ });
    fireEvent.click(filenameHeader);
    let rows = screen.getAllByRole("row").slice(1); // skip header
    expect(within(rows[0]).getByText("alpha.pdf")).toBeInTheDocument();
    fireEvent.click(filenameHeader);
    rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("zeta.pdf")).toBeInTheDocument();
  });

  it("fires onSelect when a row is clicked", () => {
    const onSelect = vi.fn();
    render(
      <CatalogTable
        documents={[makeDoc({ id: "doc_click", original_filename: "click.pdf" })]}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByText("click.pdf"));
    expect(onSelect).toHaveBeenCalledWith("doc_click");
  });
});
