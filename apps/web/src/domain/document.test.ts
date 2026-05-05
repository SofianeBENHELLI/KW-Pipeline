/**
 * documentScopes accessor tests (#258).
 *
 * After #258 every catalog read endpoint populates ``Document.scopes``
 * server-side, so the helper is a thin pass-through. The nullish
 * fallback stays for resilience against pre-#258 cached schemas.
 */

import { describe, expect, it } from "vitest";

import type { ApiDocument, ApiScope } from "../api/types";
import { documentScopes } from "./document";

function makeDocument(overrides: Partial<ApiDocument> = {}): ApiDocument {
  return {
    id: "doc-001",
    original_filename: "test.txt",
    latest_version_id: "ver-001",
    created_at: "2026-05-01T00:00:00Z",
    versions: [],
    scopes: [],
    ...overrides,
  };
}

function makeScope(
  kind: ApiScope["kind"] = "personal",
  ref: string = "user-1",
): ApiScope {
  return {
    kind,
    ref,
    added_at: "2026-05-01T00:00:00Z",
    added_by: "user-1",
    removed_at: null,
  };
}

describe("documentScopes", () => {
  it("returns the populated scopes array verbatim", () => {
    const scopes = [makeScope("personal", "user-42"), makeScope("project", "p1")];
    const document = makeDocument({ scopes });

    expect(documentScopes(document)).toBe(scopes);
  });

  it("returns an empty array when the document has no active scopes", () => {
    const document = makeDocument({ scopes: [] });

    expect(documentScopes(document)).toEqual([]);
  });

  it("falls back to an empty array when the field is missing on a stale schema", () => {
    // Pre-#258 cached schemas may serve documents without the field.
    // Cast through ``unknown`` to simulate the legacy shape — the
    // runtime contract is that the helper never returns ``null``.
    const stale = {
      id: "doc-001",
      original_filename: "test.txt",
      latest_version_id: "ver-001",
      created_at: "2026-05-01T00:00:00Z",
      versions: [],
    } as unknown as ApiDocument;

    expect(documentScopes(stale)).toEqual([]);
  });
});
