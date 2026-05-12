/**
 * Pure-formatter tests for the Review Workspace.
 */

import { describe, expect, it } from "vitest";

import type { ApiDocument, ApiDocumentVersion } from "../../api/types";
import {
  distinctScopeKinds,
  formatBytes,
  latestStatus,
  latestVersion,
  scopeKindToChipScope,
  splitIsoTimestamp,
} from "./format";

function ver(overrides: Partial<ApiDocumentVersion> = {}): ApiDocumentVersion {
  return {
    id: "ver-1",
    document_id: "doc-1",
    version_number: 1,
    filename: "x.txt",
    content_type: "text/plain",
    file_size: 100,
    sha256: "h",
    storage_uri: "file://x",
    status: "STORED",
    duplicate_of_version_id: null,
    failure_reason: null,
    reviewer_note: null,
    reviewed_at: null,
    created_at: "2026-05-11T14:22:08Z",
    ...overrides,
  };
}

function doc(overrides: Partial<ApiDocument> = {}): ApiDocument {
  return {
    id: "doc-1",
    original_filename: "x.txt",
    latest_version_id: "ver-1",
    created_at: "2026-05-11T14:22:08Z",
    archived_at: null,
    versions: [],
    scopes: [],
    ...overrides,
  };
}

describe("formatBytes", () => {
  it("renders bytes / KB / MB / GB with one decimal", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1024)).toBe("1 KB");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(1024 * 1024)).toBe("1 MB");
    expect(formatBytes(Math.round(1.4 * 1024 * 1024))).toBe("1.4 MB");
    expect(formatBytes(1024 * 1024 * 1024)).toBe("1 GB");
  });

  it("renders an em-dash for missing / negative bytes", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(undefined)).toBe("—");
    expect(formatBytes(-1)).toBe("—");
    expect(formatBytes(Number.NaN)).toBe("—");
  });
});

describe("splitIsoTimestamp", () => {
  it("splits an ISO string into day + HH:MM", () => {
    expect(splitIsoTimestamp("2026-05-11T14:22:08Z")).toEqual({
      day: "2026-05-11",
      time: "14:22",
    });
  });

  it("falls back to em-dashes for empty input", () => {
    expect(splitIsoTimestamp(null)).toEqual({ day: "—", time: "" });
    expect(splitIsoTimestamp("")).toEqual({ day: "—", time: "" });
  });
});

describe("latestVersion / latestStatus", () => {
  it("returns the version flagged by latest_version_id when present", () => {
    const v1 = ver({ id: "v1", status: "EXTRACTED" });
    const v2 = ver({ id: "v2", status: "VALIDATED" });
    const d = doc({ versions: [v1, v2], latest_version_id: "v1" });
    expect(latestVersion(d)?.id).toBe("v1");
    expect(latestStatus(d)).toBe("EXTRACTED");
  });

  it("falls back to the last entry when the flag is absent", () => {
    const v1 = ver({ id: "v1", status: "STORED" });
    const v2 = ver({ id: "v2", status: "VALIDATED" });
    const d = doc({ versions: [v1, v2] });
    expect(latestVersion(d)?.id).toBe("v2");
    expect(latestStatus(d)).toBe("VALIDATED");
  });

  it("returns STORED when no version exists", () => {
    expect(latestVersion(doc())).toBeNull();
    expect(latestStatus(doc())).toBe("STORED");
    expect(latestStatus(null)).toBe("STORED");
  });
});

describe("scopeKindToChipScope + distinctScopeKinds", () => {
  it("maps swym_community → community", () => {
    expect(scopeKindToChipScope("swym_community")).toBe("community");
    expect(scopeKindToChipScope("project")).toBe("project");
    expect(scopeKindToChipScope("personal")).toBe("personal");
    expect(scopeKindToChipScope("anything-else")).toBe("personal");
  });

  it("dedupes scope kinds across multiple links", () => {
    const d = doc({
      scopes: [
        { kind: "project", ref: "p1", added_at: "x", added_by: "a", removed_at: null },
        { kind: "swym_community", ref: "c1", added_at: "x", added_by: "a", removed_at: null },
        { kind: "project", ref: "p2", added_at: "y", added_by: "a", removed_at: null },
      ],
    });
    expect(distinctScopeKinds(d)).toEqual(["project", "community"]);
  });

  it("returns [] for null doc", () => {
    expect(distinctScopeKinds(null)).toEqual([]);
  });
});
