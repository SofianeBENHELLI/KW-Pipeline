/**
 * Smoke tests for ``explorer-data`` lookup helpers (audit P0 #230
 * first slice).
 *
 * These functions are pure (snapshot in, single value out) so they
 * are the ideal first slice of explorer test coverage: zero React,
 * zero DOM, zero network. Any future refactor (e.g. promoting the
 * lookups to a Map-backed index for O(1) lookups, P2 finding) must
 * keep these contracts.
 */

import { describe, expect, it } from "vitest";

import type { TaxonomyResponse } from "../api/types";
import {
  SAMPLE_SNAPSHOT,
  adaptTaxonomy,
  chunkById,
  chunksForConcept,
  chunksForDoc,
  conceptById,
  docById,
} from "./explorer-data";

describe("explorer-data lookup helpers", () => {
  it("docById returns the matching document or undefined", () => {
    const firstDoc = SAMPLE_SNAPSHOT.documents[0];
    expect(docById(SAMPLE_SNAPSHOT, firstDoc.id)).toBe(firstDoc);
    expect(docById(SAMPLE_SNAPSHOT, "nope")).toBeUndefined();
  });

  it("chunkById returns the matching chunk or undefined", () => {
    const firstChunk = SAMPLE_SNAPSHOT.chunks[0];
    expect(chunkById(SAMPLE_SNAPSHOT, firstChunk.id)).toBe(firstChunk);
    expect(chunkById(SAMPLE_SNAPSHOT, "nope")).toBeUndefined();
  });

  it("conceptById returns the matching concept or undefined", () => {
    const firstConcept = SAMPLE_SNAPSHOT.concepts[0];
    expect(conceptById(SAMPLE_SNAPSHOT, firstConcept.id)).toBe(firstConcept);
    expect(conceptById(SAMPLE_SNAPSHOT, "nope")).toBeUndefined();
  });

  it("chunksForDoc returns every chunk that belongs to the document", () => {
    const someDoc = SAMPLE_SNAPSHOT.documents[0];
    const chunks = chunksForDoc(SAMPLE_SNAPSHOT, someDoc.id);
    // Sample data is small but non-empty for the first doc; the
    // contract is "every returned chunk has chunk.doc === doc.id".
    expect(chunks.length).toBeGreaterThan(0);
    for (const chunk of chunks) {
      expect(chunk.doc).toBe(someDoc.id);
    }
  });

  it("chunksForConcept returns chunks linked to the concept via chunkConcept edges", () => {
    // Pick the first chunk-concept link from the sample so we know
    // there is at least one match.
    const link = SAMPLE_SNAPSHOT.chunkConcept[0];
    const [chunkId, conceptId] = link;
    const result = chunksForConcept(SAMPLE_SNAPSHOT, conceptId);
    expect(result.map((c) => c.id)).toContain(chunkId);
  });

  it("returns an empty array when nothing matches (never undefined)", () => {
    // The contract for the multi-match helpers is empty array, not
    // undefined — every consumer iterates the result without a null
    // check, so a regression that returns undefined would crash the
    // UI silently in production.
    expect(chunksForDoc(SAMPLE_SNAPSHOT, "nope")).toEqual([]);
    expect(chunksForConcept(SAMPLE_SNAPSHOT, "nope")).toEqual([]);
  });
});

describe("adaptTaxonomy — source flag flows through from the API (#249)", () => {
  it("propagates each category's source verbatim to the cluster meta", () => {
    const response: TaxonomyResponse = {
      schema_version: "v0.1",
      is_configured: true,
      source_path: "/etc/kw/taxonomy.yml",
      categories: [
        {
          id: "hr",
          label: "HR",
          description: "Operator-authored HR.",
          subcategories: [],
          source: "imposed",
        },
        {
          id: "topic-cluster-42",
          label: "Compliance memos",
          description: "Auto-deduced.",
          subcategories: [],
          source: "computed",
        },
      ],
    };
    const { clusters } = adaptTaxonomy(response);
    expect(clusters.hr.source).toBe("imposed");
    expect(clusters["topic-cluster-42"].source).toBe("computed");
  });

  it("treats a missing source field as 'computed' (forward-compat fallback)", () => {
    // Older API builds that haven't shipped the #249 ``source`` field
    // yet still flow through; we default the badge to "auto" rather
    // than mislabel an unknown cluster as operator-owned.
    const response: TaxonomyResponse = {
      schema_version: "v0.1",
      is_configured: true,
      source_path: null,
      categories: [
        {
          id: "legacy",
          label: "Legacy",
          description: "Pre-#249 server.",
          subcategories: [],
        },
      ],
    };
    const { clusters } = adaptTaxonomy(response);
    expect(clusters.legacy.source).toBe("computed");
  });
});
