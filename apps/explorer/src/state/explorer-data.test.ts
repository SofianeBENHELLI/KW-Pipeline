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
  filterSnapshot,
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

describe("filterSnapshot — projects through a doc predicate (#296)", () => {
  it("drops documents that fail the predicate and every dependent edge/chunk", () => {
    const keepType = SAMPLE_SNAPSHOT.documents[0].type;
    const projected = filterSnapshot(SAMPLE_SNAPSHOT, (d) => d.type === keepType);
    const keptIds = new Set(projected.documents.map((d) => d.id));

    // Documents: only the predicate-passing ones survive.
    expect(projected.documents.length).toBeGreaterThan(0);
    expect(projected.documents.length).toBeLessThanOrEqual(SAMPLE_SNAPSHOT.documents.length);
    for (const d of projected.documents) expect(d.type).toBe(keepType);

    // Chunks: every kept chunk's parent doc must be in the kept set.
    for (const c of projected.chunks) expect(keptIds.has(c.doc)).toBe(true);

    // Doc-edges: both endpoints must be in the kept set.
    for (const e of projected.docEdges) {
      expect(keptIds.has(e.a)).toBe(true);
      expect(keptIds.has(e.b)).toBe(true);
    }

    // Chunk-concept links: every kept link's chunk must still exist.
    const keptChunkIds = new Set(projected.chunks.map((c) => c.id));
    for (const [cid] of projected.chunkConcept) expect(keptChunkIds.has(cid)).toBe(true);
  });

  it("passes context (concepts, conceptEdges, clusters, docContent) through unchanged", () => {
    // An "include nothing" predicate is the strongest assertion that
    // global context is decoupled from the per-document filter.
    const projected = filterSnapshot(SAMPLE_SNAPSHOT, () => false);
    expect(projected.documents).toEqual([]);
    expect(projected.chunks).toEqual([]);
    expect(projected.docEdges).toEqual([]);
    expect(projected.chunkConcept).toEqual([]);
    expect(projected.concepts).toBe(SAMPLE_SNAPSHOT.concepts);
    expect(projected.conceptEdges).toBe(SAMPLE_SNAPSHOT.conceptEdges);
    expect(projected.clusters).toBe(SAMPLE_SNAPSHOT.clusters);
    expect(projected.docContent).toBe(SAMPLE_SNAPSHOT.docContent);
    expect(projected.isSample).toBe(SAMPLE_SNAPSHOT.isSample);
    expect(projected.corpusLabel).toBe(SAMPLE_SNAPSHOT.corpusLabel);
  });

  it("is a no-op when the predicate keeps every document", () => {
    const projected = filterSnapshot(SAMPLE_SNAPSHOT, () => true);
    expect(projected.documents).toEqual(SAMPLE_SNAPSHOT.documents);
    expect(projected.chunks).toEqual(SAMPLE_SNAPSHOT.chunks);
    expect(projected.docEdges).toEqual(SAMPLE_SNAPSHOT.docEdges);
    expect(projected.chunkConcept).toEqual(SAMPLE_SNAPSHOT.chunkConcept);
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
