import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PipelineWidget } from "./PipelineWidget";
import type { ApiDocument } from "../../api/types";

// #292 — upload UI moved to the Forge widget; PipelineWidget is now
// read-only for ingestion. These tests assert the Orbital lens
// concerns: document list rendering, ordering, status filters, the
// duplicate marker, and the Forge hint that replaces the old "+"
// button.

function makeDoc(overrides: Partial<ApiDocument> & { id: string }): ApiDocument {
  return {
    id: overrides.id,
    original_filename: overrides.original_filename ?? `${overrides.id}.txt`,
    latest_version_id: overrides.latest_version_id ?? `ver-${overrides.id}`,
    created_at: overrides.created_at ?? "2026-05-01T00:00:00Z",
    archived_at: overrides.archived_at ?? null,
    scopes: overrides.scopes ?? [],
    versions: overrides.versions ?? [
      {
        id: `ver-${overrides.id}`,
        document_id: overrides.id,
        version_number: 1,
        filename: `${overrides.id}.txt`,
        content_type: "text/plain",
        file_size: 1000,
        sha256: "abc123def456789012345abcdef1234567890abcdef1234567890abcdef12345",
        storage_uri: `file://${overrides.id}`,
        status: "STORED",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: overrides.created_at ?? "2026-05-01T00:00:00Z",
      },
    ],
  };
}

describe("PipelineWidget", () => {
  it("does not render an upload button or file input — Orbital is read-only for ingestion (#292)", () => {
    render(
      <PipelineWidget
        documents={[makeDoc({ id: "doc-001" })]}
        selectedDocumentId="doc-001"
        onSelectDocument={() => {}}
      />,
    );
    expect(
      screen.queryByRole("button", { name: /Upload document/i }),
    ).not.toBeInTheDocument();
    expect(document.querySelector("input[type='file']")).toBeNull();
  });

  it("renders a Forge hint where the old upload button used to be (#292)", () => {
    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );
    expect(screen.getByTestId("forge-import-hint")).toHaveTextContent(/forge/i);
  });

  it("orders documents by created_at descending — newest first (#292)", () => {
    const oldDoc = makeDoc({
      id: "doc-old",
      original_filename: "old.txt",
      created_at: "2026-01-01T00:00:00Z",
    });
    const midDoc = makeDoc({
      id: "doc-mid",
      original_filename: "mid.txt",
      created_at: "2026-03-01T00:00:00Z",
    });
    const newDoc = makeDoc({
      id: "doc-new",
      original_filename: "new.txt",
      created_at: "2026-05-01T00:00:00Z",
    });

    // Pass the documents in arbitrary order; the widget must re-sort.
    render(
      <PipelineWidget
        documents={[oldDoc, newDoc, midDoc]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );

    const rows = screen.getAllByRole("button");
    const filenames = rows.map((row) => row.textContent ?? "");
    const newIdx = filenames.findIndex((t) => t.startsWith("new.txt"));
    const midIdx = filenames.findIndex((t) => t.startsWith("mid.txt"));
    const oldIdx = filenames.findIndex((t) => t.startsWith("old.txt"));
    expect(newIdx).toBeGreaterThan(-1);
    expect(midIdx).toBeGreaterThan(newIdx);
    expect(oldIdx).toBeGreaterThan(midIdx);
  });

  it("renders the Recent saved-view chip and toggles its statuses", () => {
    const onFilterChange = vi.fn();
    render(
      <PipelineWidget
        documents={[makeDoc({ id: "doc-001" })]}
        selectedDocumentId="doc-001"
        onSelectDocument={() => {}}
        filter={{ status: [], q: "" }}
        onFilterChange={onFilterChange}
      />,
    );
    const tablist = screen.getByRole("tablist", { name: /Saved views/i });
    const stored = within(tablist).getByRole("tab", { name: /^Recent$/i });
    fireEvent.click(stored);
    expect(onFilterChange).toHaveBeenCalledWith({
      status: [
        "STORED",
        "EXTRACTING",
        "EXTRACTED",
        "SEMANTIC_READY",
        "NEEDS_REVIEW",
      ],
      q: "",
    });
  });

  it("renders Review / Validated / Failed chips alongside Recent", () => {
    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
        filter={{ status: [], q: "" }}
        onFilterChange={() => {}}
      />,
    );
    const tablist = screen.getByRole("tablist", { name: /Saved views/i });
    expect(within(tablist).getByRole("tab", { name: /^Recent$/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /^Review$/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /^Validated$/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /^Failed$/i })).toBeInTheDocument();
  });

  it("renders a per-row batch progress pill for each entry in batchProgress", () => {
    render(
      <PipelineWidget
        documents={[
          makeDoc({ id: "doc-001" }),
          makeDoc({ id: "doc-002" }),
          makeDoc({ id: "doc-003" }),
        ]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
        selectedBatchIds={new Set(["doc-001", "doc-002", "doc-003"])}
        onToggleBatchDocument={() => {}}
        onRunBatchPipeline={() => {}}
        onClearBatchSelection={() => {}}
        batchProgress={
          new Map([
            ["doc-001", { status: "extracting" }],
            ["doc-002", { status: "done" }],
            ["doc-003", { status: "failed", reason: "boom" }],
          ])
        }
      />,
    );

    const pills = screen.getAllByTestId("batch-row-pill");
    expect(pills).toHaveLength(3);
    const statuses = pills.map((pill) => pill.getAttribute("data-status"));
    expect(statuses).toEqual(
      expect.arrayContaining(["extracting", "done", "failed"]),
    );
    // Failure reason rides as a tooltip on the pill.
    const failed = pills.find((p) => p.getAttribute("data-status") === "failed");
    expect(failed).toHaveAttribute("title", "boom");
  });

  it("renders a structured failure list when batchFailures is non-empty", () => {
    render(
      <PipelineWidget
        documents={[makeDoc({ id: "doc-001" }), makeDoc({ id: "doc-002" })]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
        selectedBatchIds={new Set()}
        onToggleBatchDocument={() => {}}
        onRunBatchPipeline={() => {}}
        onClearBatchSelection={() => {}}
        batchFailures={[
          {
            document_id: "doc-001",
            filename: "doc-001.txt",
            reason: "Parser crashed",
          },
          {
            document_id: "doc-002",
            filename: "doc-002.txt",
            reason: "Voyage rate limit",
          },
        ]}
      />,
    );

    const list = screen.getByTestId("batch-failure-list");
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("doc-001.txt");
    expect(items[0]).toHaveTextContent("Parser crashed");
    expect(items[1]).toHaveTextContent("doc-002.txt");
    expect(items[1]).toHaveTextContent("Voyage rate limit");
    expect(screen.getByText(/2 documents failed/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Failed documents stay selected/i),
    ).toBeInTheDocument();
  });

  it("scrolls the selected row into view when scrollSelectedToken changes", () => {
    const scrollSpy = vi.fn();
    Element.prototype.scrollIntoView = scrollSpy;

    const { rerender } = render(
      <PipelineWidget
        documents={[makeDoc({ id: "doc-001" }), makeDoc({ id: "doc-002" })]}
        selectedDocumentId="doc-002"
        onSelectDocument={() => {}}
        scrollSelectedToken={0}
      />,
    );
    // Token=0 is the "no scroll yet" sentinel; nothing should fire.
    expect(scrollSpy).not.toHaveBeenCalled();

    rerender(
      <PipelineWidget
        documents={[makeDoc({ id: "doc-001" }), makeDoc({ id: "doc-002" })]}
        selectedDocumentId="doc-002"
        onSelectDocument={() => {}}
        scrollSelectedToken={1}
      />,
    );
    expect(scrollSpy).toHaveBeenCalledTimes(1);
    expect(scrollSpy).toHaveBeenCalledWith({ block: "center", behavior: "smooth" });
  });

  it("selects documents for the batch semantic pipeline and runs the selected action", () => {
    const onToggle = vi.fn();
    const onRun = vi.fn();
    render(
      <PipelineWidget
        documents={[makeDoc({ id: "doc-001" }), makeDoc({ id: "doc-002" })]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
        selectedBatchIds={new Set(["doc-001"])}
        onToggleBatchDocument={onToggle}
        onRunBatchPipeline={onRun}
        onClearBatchSelection={() => {}}
      />,
    );

    expect(
      screen.getByRole("checkbox", {
        name: /Select doc-001\.txt for batch pipeline/i,
      }),
    ).toBeChecked();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /Select doc-002\.txt for batch pipeline/i,
      }),
    );
    expect(onToggle).toHaveBeenCalledWith("doc-002", true);
    fireEvent.click(screen.getByRole("button", { name: /Run selected pipeline/i }));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("calls onSelectDocument when a document row is clicked", () => {
    const onSelect = vi.fn();
    render(
      <PipelineWidget
        documents={[makeDoc({ id: "doc-001" })]}
        selectedDocumentId=""
        onSelectDocument={onSelect}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /doc-001\.txt/i }));
    expect(onSelect).toHaveBeenCalledWith("doc-001");
  });

  it("flags duplicates with the Duplicate marker", () => {
    const dup = makeDoc({
      id: "doc-dup",
      versions: [
        {
          id: "ver-dup",
          document_id: "doc-dup",
          version_number: 2,
          filename: "doc-dup.txt",
          content_type: "text/plain",
          file_size: 1000,
          sha256: "x",
          storage_uri: "file://x",
          status: "DUPLICATE_DETECTED",
          duplicate_of_version_id: "ver-001",
          failure_reason: null,
          reviewer_note: null,
          reviewed_at: null,
          created_at: "2026-05-01T00:00:00Z",
        },
      ],
    });
    render(
      <PipelineWidget
        documents={[dup]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );
    expect(screen.getByLabelText(/Duplicate of an earlier version/i)).toBeInTheDocument();
  });

  it("shows version-count chip when a document has more than one version", () => {
    const multi = makeDoc({
      id: "doc-multi",
      versions: [
        {
          id: "ver-1",
          document_id: "doc-multi",
          version_number: 1,
          filename: "f.txt",
          content_type: "text/plain",
          file_size: 1,
          sha256: "a",
          storage_uri: "file://a",
          status: "VALIDATED",
          duplicate_of_version_id: null,
          failure_reason: null,
          reviewer_note: null,
          reviewed_at: null,
          created_at: "2026-05-01T00:00:00Z",
        },
        {
          id: "ver-2",
          document_id: "doc-multi",
          version_number: 2,
          filename: "f.txt",
          content_type: "text/plain",
          file_size: 1,
          sha256: "b",
          storage_uri: "file://b",
          status: "STORED",
          duplicate_of_version_id: null,
          failure_reason: null,
          reviewer_note: null,
          reviewed_at: null,
          created_at: "2026-05-02T00:00:00Z",
        },
      ],
    });
    render(
      <PipelineWidget
        documents={[multi]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );
    expect(screen.getByTestId("version-count")).toHaveTextContent(/2 versions/);
  });

  it("renders the Purge-all button only when onPurgeAllRequest is set and the list is non-empty (#292 §5)", () => {
    const onPurgeAllRequest = vi.fn();
    const { rerender } = render(
      <PipelineWidget
        documents={[makeDoc({ id: "doc-001" })]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
        onPurgeAllRequest={onPurgeAllRequest}
      />,
    );
    fireEvent.click(screen.getByTestId("purge-all-button"));
    expect(onPurgeAllRequest).toHaveBeenCalledTimes(1);

    // Hidden when there are no documents (nothing to purge).
    rerender(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
        onPurgeAllRequest={onPurgeAllRequest}
      />,
    );
    expect(screen.queryByTestId("purge-all-button")).not.toBeInTheDocument();
  });

  it("renders an empty-state message when documents is empty", () => {
    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );
    expect(screen.getByText(/No documents yet\./)).toBeInTheDocument();
  });

  it("renders 'No documents match this filter.' when a filter is active and the list is empty", () => {
    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
        filter={{ status: ["STORED"], q: "" }}
        onFilterChange={() => {}}
      />,
    );
    expect(screen.getByText(/No documents match this filter\./)).toBeInTheDocument();
  });
});
