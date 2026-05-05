/**
 * Document version count + Latest badge tests for ReviewWorkspace.
 *
 * Pins the surface added on top of the existing `Version {N}` line:
 * - Brand-coloured `v{N}` badge for the latest version.
 * - "(N versions)" caption beside the title when N > 1, hidden at N == 1.
 * - "of N total" suffix on the active-version line when N > 1.
 *
 * The lineage modal (#217 C.5) is deliberately not exercised — that
 * needs the /documents/{id}/lineage endpoint and is deferred.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewWorkspace } from "../ReviewWorkspace";
import type {
  ApiDocument,
  ApiDocumentVersion,
  DocumentVersionStatus,
} from "../../../api/types";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeVersion(
  versionNumber: number,
  status: DocumentVersionStatus = "VALIDATED",
): ApiDocumentVersion {
  return {
    id: `ver-${versionNumber}`,
    document_id: "doc-001",
    version_number: versionNumber,
    filename: `test.txt`,
    content_type: "text/plain",
    file_size: 100,
    sha256: "abc123def456789012345abcdef1234567890abcdef1234567890abcdef12345",
    storage_uri: `file://test-v${versionNumber}`,
    status,
    duplicate_of_version_id: null,
    failure_reason: null,
    reviewer_note: null,
    reviewed_at: null,
    created_at: "2026-05-01T00:00:00Z",
  };
}

function makeDocument(
  versionNumbers: number[],
  latestStatus: DocumentVersionStatus = "VALIDATED",
): ApiDocument {
  const versions = versionNumbers.map((n, idx) =>
    makeVersion(n, idx === versionNumbers.length - 1 ? latestStatus : "VALIDATED"),
  );
  return {
    id: "doc-001",
    original_filename: "test.txt",
    latest_version_id: versions[versions.length - 1].id,
    created_at: "2026-05-01T00:00:00Z",
    archived_at: null,
    versions,
  };
}

describe("ReviewWorkspace — version count + latest badge", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404)),
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it("N == 3 renders v3 badge, '3 versions' caption, and 'of 3 total' on the version line", async () => {
    render(<ReviewWorkspace document={makeDocument([1, 2, 3])} />);

    await waitFor(() => {
      const badge = screen.getByTestId("latest-version-badge");
      expect(badge.textContent).toBe("v3");
    });
    expect(screen.getByTestId("version-count").textContent).toMatch(
      /\(\s*3 versions\s*\)/,
    );
    expect(screen.getByTestId("version-of-total").textContent).toMatch(
      /of\s*3\s*total/,
    );
  });

  it("N == 1 renders v1 badge but no parenthetical and no 'of N total'", async () => {
    render(<ReviewWorkspace document={makeDocument([1])} />);

    await waitFor(() => {
      const badge = screen.getByTestId("latest-version-badge");
      expect(badge.textContent).toBe("v1");
    });
    expect(screen.queryByTestId("version-count")).toBeNull();
    expect(screen.queryByTestId("version-of-total")).toBeNull();
  });

  it("viewing version 1 with N == 2 still shows 'of 2 total' (latest is the addressed version)", async () => {
    // The workspace addresses ``latestVersion(document)`` rather than
    // letting the parent pick a non-latest version (#217 C.5 is the
    // followup that surfaces version pickers). So with two versions,
    // the workspace shows the latest — Version 2 — with " of 2 total".
    render(<ReviewWorkspace document={makeDocument([1, 2])} />);

    await waitFor(() => {
      expect(screen.getByTestId("version-of-total").textContent).toMatch(
        /of\s*2\s*total/,
      );
    });
  });
});
