/**
 * Document version surface tests for the catalog list inside
 * PipelineWidget (#59 + EPIC-C #217).
 *
 * The latest-version badge replaces the previous bare ``v{N}`` text;
 * the "(N versions)" caption only appears when N > 1.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PipelineWidget } from "../PipelineWidget";
import type {
  ApiDocument,
  ApiDocumentVersion,
} from "../../../api/types";

function makeVersion(versionNumber: number): ApiDocumentVersion {
  return {
    id: `ver-${versionNumber}`,
    document_id: "doc-001",
    version_number: versionNumber,
    filename: "test.txt",
    content_type: "text/plain",
    file_size: 100,
    sha256: "abc123def456789012345abcdef1234567890abcdef1234567890abcdef12345",
    storage_uri: "file://test",
    status: "STORED",
    duplicate_of_version_id: null,
    failure_reason: null,
    reviewer_note: null,
    reviewed_at: null,
    created_at: "2026-05-01T00:00:00Z",
  };
}

function makeDocument(versionNumbers: number[]): ApiDocument {
  const versions = versionNumbers.map(makeVersion);
  return {
    id: "doc-001",
    original_filename: "test.txt",
    latest_version_id: versions[versions.length - 1].id,
    created_at: "2026-05-01T00:00:00Z",
    versions,
    scopes: [],
  };
}

describe("PipelineWidget — document version surface", () => {
  it("N == 3 renders v3 badge AND '(3 versions)' caption next to the title", () => {
    render(
      <PipelineWidget
        documents={[makeDocument([1, 2, 3])]}
        selectedDocumentId="doc-001"
        onSelectDocument={() => {}}
      />,
    );

    const badge = screen.getByTestId("latest-version-badge");
    expect(badge.textContent).toBe("v3");
    const count = screen.getByTestId("version-count");
    expect(count.textContent).toMatch(/\(\s*3 versions\s*\)/);
  });

  it("N == 1 renders v1 badge with NO '(N versions)' caption", () => {
    render(
      <PipelineWidget
        documents={[makeDocument([1])]}
        selectedDocumentId="doc-001"
        onSelectDocument={() => {}}
      />,
    );

    const badge = screen.getByTestId("latest-version-badge");
    expect(badge.textContent).toBe("v1");
    expect(screen.queryByTestId("version-count")).toBeNull();
  });
});
