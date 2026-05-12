/**
 * DocRail — pin the search/views/list rendering, sortable headers,
 * batch selection bar, row click → onSelect, and checkbox toggle.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ApiDocument } from "../../api/types";
import { DocRail, type RailSort } from "./DocRail";

const FIXTURE_DOCS: ApiDocument[] = [
  {
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
  },
  {
    id: "doc-b",
    original_filename: "beta.md",
    latest_version_id: "ver-b",
    created_at: "2026-05-10T09:00:00Z",
    archived_at: null,
    scopes: [],
    versions: [
      {
        id: "ver-b",
        document_id: "doc-b",
        version_number: 2,
        filename: "beta.md",
        content_type: "text/markdown",
        file_size: 2048,
        sha256: "hb",
        storage_uri: "file://b",
        status: "VALIDATED",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: "2026-05-10T09:00:00Z",
      },
    ],
  },
];

const NOOP = () => {};
const DEFAULT_SORT: RailSort = { col: "uploaded", dir: "desc" };

function renderRail(overrides: Partial<React.ComponentProps<typeof DocRail>> = {}) {
  return render(
    <DocRail
      view="recent"
      onView={NOOP}
      query=""
      onQuery={NOOP}
      documents={FIXTURE_DOCS}
      activeDocId={null}
      onSelect={NOOP}
      selected={new Set()}
      onToggleSelect={NOOP}
      onClearSelection={NOOP}
      sort={DEFAULT_SORT}
      onToggleSort={NOOP}
      {...overrides}
    />,
  );
}

describe("<DocRail />", () => {
  it("renders the four saved views with optional counts", () => {
    renderRail({
      counts: { recent: 247, review: 23, validated: 1842, failed: 7 },
    });
    expect(screen.getByText("Recent")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.getByText("Validated")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("247")).toBeInTheDocument();
    expect(screen.getByText("1,842")).toBeInTheDocument();
  });

  it("highlights the active view via aria-selected", () => {
    renderRail({ view: "validated" });
    const validated = screen.getByRole("tab", { name: /Validated/ });
    expect(validated).toHaveAttribute("aria-selected", "true");
    expect(validated).toHaveAttribute("aria-current", "page");
  });

  it("forwards filename input to onQuery", () => {
    const onQuery = vi.fn();
    renderRail({ onQuery });
    fireEvent.change(screen.getByPlaceholderText("Filter filename…"), {
      target: { value: "neo4j" },
    });
    expect(onQuery).toHaveBeenCalledWith("neo4j");
  });

  it("renders rows with filename + id + version count + bytes + status", () => {
    renderRail();
    expect(screen.getByText("alpha.md")).toBeInTheDocument();
    expect(screen.getByText("beta.md")).toBeInTheDocument();
    expect(screen.getByText("doc-a")).toBeInTheDocument();
    expect(screen.getByText("doc-b")).toBeInTheDocument();
    // alpha.md = 4096 B → "4 KB"
    expect(screen.getByText("4 KB")).toBeInTheDocument();
    // beta.md = 2048 B → "2 KB"
    expect(screen.getByText("2 KB")).toBeInTheDocument();
    // Status badges from latest version
    expect(screen.getByRole("status", { name: "NEEDS_REVIEW" })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "VALIDATED" })).toBeInTheDocument();
  });

  it("clicking a row invokes onSelect with the doc id", () => {
    const onSelect = vi.fn();
    renderRail({ onSelect });
    fireEvent.click(screen.getByLabelText(/Open alpha\.md/));
    expect(onSelect).toHaveBeenCalledWith("doc-a");
  });

  it("clicking the checkbox toggles selection without triggering onSelect", () => {
    const onSelect = vi.fn();
    const onToggleSelect = vi.fn();
    renderRail({ onSelect, onToggleSelect });
    fireEvent.click(screen.getByRole("checkbox", { name: /Select alpha\.md/ }));
    expect(onToggleSelect).toHaveBeenCalledWith("doc-a");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("shows the batch bar only when something is selected", () => {
    const { rerender } = renderRail();
    expect(screen.queryByRole("region", { name: /Batch selection/ })).toBeNull();
    rerender(
      <DocRail
        view="recent"
        onView={NOOP}
        query=""
        onQuery={NOOP}
        documents={FIXTURE_DOCS}
        activeDocId={null}
        onSelect={NOOP}
        selected={new Set(["doc-a", "doc-b"])}
        onToggleSelect={NOOP}
        onClearSelection={NOOP}
        sort={DEFAULT_SORT}
        onToggleSort={NOOP}
      />,
    );
    const region = screen.getByRole("region", { name: /Batch selection/ });
    expect(within(region).getByText("2 selected")).toBeInTheDocument();
  });

  it("clicking 'Run pipeline' invokes onRunBatch", () => {
    const onRunBatch = vi.fn();
    render(
      <DocRail
        view="recent"
        onView={NOOP}
        query=""
        onQuery={NOOP}
        documents={FIXTURE_DOCS}
        activeDocId={null}
        onSelect={NOOP}
        selected={new Set(["doc-a"])}
        onToggleSelect={NOOP}
        onClearSelection={NOOP}
        sort={DEFAULT_SORT}
        onToggleSort={NOOP}
        onRunBatch={onRunBatch}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Run pipeline/ }));
    expect(onRunBatch).toHaveBeenCalled();
  });

  it("sortable headers fire onToggleSort", () => {
    const onToggleSort = vi.fn();
    renderRail({ onToggleSort });
    fireEvent.click(screen.getByRole("button", { name: /^FILENAME/ }));
    fireEvent.click(screen.getByRole("button", { name: /^STATUS/ }));
    expect(onToggleSort).toHaveBeenNthCalledWith(1, "filename");
    expect(onToggleSort).toHaveBeenNthCalledWith(2, "status");
  });

  it("renders the sort arrow on the active column", () => {
    renderRail({ sort: { col: "filename", dir: "asc" } });
    expect(screen.getByRole("button", { name: /FILENAME ↑/ })).toBeInTheDocument();
  });

  it("renders the loading skeleton when loading + no rows", () => {
    const { container } = render(
      <DocRail
        view="recent"
        onView={NOOP}
        query=""
        onQuery={NOOP}
        documents={[]}
        loading
        activeDocId={null}
        onSelect={NOOP}
        selected={new Set()}
        onToggleSelect={NOOP}
        onClearSelection={NOOP}
        sort={DEFAULT_SORT}
        onToggleSort={NOOP}
      />,
    );
    expect(container.querySelectorAll(".kf-rail__row--skeleton").length).toBe(6);
  });

  it("renders the empty state with a 'Clear filters' button", () => {
    const onView = vi.fn();
    const onQuery = vi.fn();
    render(
      <DocRail
        view="failed"
        onView={onView}
        query="missing"
        onQuery={onQuery}
        documents={[]}
        activeDocId={null}
        onSelect={NOOP}
        selected={new Set()}
        onToggleSelect={NOOP}
        onClearSelection={NOOP}
        sort={DEFAULT_SORT}
        onToggleSort={NOOP}
      />,
    );
    expect(screen.getByText(/No documents match this view/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Clear filters/ }));
    expect(onQuery).toHaveBeenCalledWith("");
    expect(onView).toHaveBeenCalledWith("recent");
  });

  it("surfaces an error message", () => {
    render(
      <DocRail
        view="recent"
        onView={NOOP}
        query=""
        onQuery={NOOP}
        documents={[]}
        errorMessage="boom"
        activeDocId={null}
        onSelect={NOOP}
        selected={new Set()}
        onToggleSelect={NOOP}
        onClearSelection={NOOP}
        sort={DEFAULT_SORT}
        onToggleSort={NOOP}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("boom");
  });
});
