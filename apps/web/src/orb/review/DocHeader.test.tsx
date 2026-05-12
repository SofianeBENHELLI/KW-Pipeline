/**
 * DocHeader — pin the title, breadcrumbs, status badge, scope chips,
 * version metadata, projection pill, and the action callbacks.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ApiDocument } from "../../api/types";
import { DocHeader } from "./DocHeader";

function fixtureDoc(): ApiDocument {
  return {
    id: "doc-policy-001",
    original_filename: "supplier-quality-policy.txt",
    latest_version_id: "ver-policy-002",
    created_at: "2026-04-30T08:42:00Z",
    archived_at: null,
    scopes: [
      { kind: "project", ref: "p1", added_at: "x", added_by: "a", removed_at: null },
      {
        kind: "swym_community",
        ref: "c1",
        added_at: "x",
        added_by: "a",
        removed_at: null,
      },
    ],
    versions: [
      {
        id: "ver-policy-001",
        document_id: "doc-policy-001",
        version_number: 1,
        filename: "supplier-quality-policy.txt",
        content_type: "text/plain",
        file_size: 1024,
        sha256: "h1",
        storage_uri: "file://1",
        status: "EXTRACTED",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: "2026-04-29T08:42:00Z",
      },
      {
        id: "ver-policy-002",
        document_id: "doc-policy-001",
        version_number: 2,
        filename: "supplier-quality-policy.txt",
        content_type: "text/plain",
        file_size: 1840,
        sha256: "h2",
        storage_uri: "file://2",
        status: "NEEDS_REVIEW",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: "2026-04-30T08:42:00Z",
      },
    ],
  };
}

describe("<DocHeader />", () => {
  it("renders the empty state when no document is selected", () => {
    render(<DocHeader document={null} />);
    expect(screen.getByText(/Pick a document from the rail/i)).toBeInTheDocument();
  });

  it("renders the breadcrumbs + title + filename + id", () => {
    render(<DocHeader document={fixtureDoc()} />);
    expect(screen.getByText("Documents")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /supplier-quality-policy\.txt/i }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("doc-policy-001").length).toBeGreaterThan(0);
  });

  it("surfaces the latest-version status, version count, bytes", () => {
    render(<DocHeader document={fixtureDoc()} />);
    expect(screen.getByRole("status", { name: "NEEDS_REVIEW" })).toBeInTheDocument();
    // 2 versions, 1840 bytes ≈ 1.8 KB
    expect(screen.getByText(/v2 · 1\.8 KB/)).toBeInTheDocument();
  });

  it("renders distinct scope chips (project + community, dedup'd)", () => {
    render(<DocHeader document={fixtureDoc()} />);
    expect(screen.getByText("project")).toBeInTheDocument();
    expect(screen.getByText("community")).toBeInTheDocument();
  });

  it("renders the projection pill when supplied", () => {
    render(
      <DocHeader
        document={fixtureDoc()}
        projectionPill={{ text: "projection · COMPLETED · 4.2s", tone: "ok" }}
      />,
    );
    expect(
      screen.getByText("projection · COMPLETED · 4.2s"),
    ).toBeInTheDocument();
  });

  it("includes the page count when provided", () => {
    render(<DocHeader document={fixtureDoc()} pages={14} />);
    expect(screen.getByText(/14 pages/)).toBeInTheDocument();
  });

  it("invokes the action callbacks", () => {
    const onCopyLink = vi.fn();
    const onRefresh = vi.fn();
    render(
      <DocHeader
        document={fixtureDoc()}
        onCopyLink={onCopyLink}
        onRefresh={onRefresh}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Copy link/ }));
    fireEvent.click(screen.getByRole("button", { name: /Refresh/ }));
    expect(onCopyLink).toHaveBeenCalled();
    expect(onRefresh).toHaveBeenCalled();
  });
});
