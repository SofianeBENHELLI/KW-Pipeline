/**
 * Reusable v0.2 enriched-projection fixture (#164).
 *
 * Lives outside the test file so the component test, the
 * `filterProjection` unit tests, and any future graph consumer can
 * share one source of truth. Mirrors the wire payload that
 * `KnowledgeProjector` emits after Lane B + Lane A landed (#141,
 * #142, #143, #144) — chunks, a topic cluster, an entity, and the
 * deterministic relations between them, with provenance fields
 * populated on the chunks and the `has_entity` edge.
 *
 * Keep in sync with `KnowledgeGraphProjection` from the backend; the
 * generated `ApiKnowledgeGraphProjection` type catches drift at
 * compile time.
 */
import type { ApiKnowledgeGraphProjection } from "../../../api/types";

export const v0_2_enrichedProjection: ApiKnowledgeGraphProjection = {
  document_id: "doc-001",
  version_id: "ver-001",
  schema_version: "v0.2",
  generated_at: "2026-05-02T00:00:00Z",
  nodes: [
    { id: "doc-001", kind: "document", label: "policy.txt", properties: {} },
    { id: "ver-001", kind: "version", label: "v1", properties: {} },
    {
      id: "alpha",
      kind: "chunk",
      label: "Audit plan",
      properties: {
        document_id: "doc-001",
        version_id: "ver-001",
        chunk_id: "alpha",
        section_id: "alpha",
        heading: "Audit plan",
        text_preview: "Quality audit programmes evaluate supplier performance.",
        char_count: 120,
        keywords: ["audit", "supplier", "quality"],
        topic_id: "topic-aaaa1111",
        source_reference_count: 2,
      },
    },
    {
      id: "beta",
      kind: "chunk",
      label: "Audit findings",
      properties: {
        document_id: "doc-001",
        version_id: "ver-001",
        chunk_id: "beta",
        section_id: "beta",
        heading: "Audit findings",
        text_preview: "Audit findings categorise supplier performance gaps.",
        char_count: 95,
        keywords: ["audit", "supplier", "findings"],
        topic_id: "topic-aaaa1111",
        source_reference_count: 1,
      },
    },
    {
      id: "topic-aaaa1111",
      kind: "topic",
      label: "Audit · Supplier",
      properties: {
        document_id: "doc-001",
        version_id: "ver-001",
        topic_id: "topic-aaaa1111",
        label: "Audit · Supplier",
        keywords: ["audit", "supplier", "quality"],
        summary: "Cluster of 2 related chunks discussing audit, supplier.",
        chunk_count: 2,
        chunk_ids: ["alpha", "beta"],
      },
    },
    {
      id: "entity-iso9001",
      kind: "entity",
      label: "ISO 9001",
      properties: { subject: "ISO 9001", subject_type: "STANDARD" },
    },
  ],
  edges: [
    {
      id: "ver-001->part_of->doc-001",
      source_id: "ver-001",
      target_id: "doc-001",
      kind: "part_of",
      properties: { document_id: "doc-001", version_id: "ver-001" },
    },
    {
      id: "alpha->part_of->ver-001",
      source_id: "alpha",
      target_id: "ver-001",
      kind: "part_of",
      properties: {
        document_id: "doc-001",
        version_id: "ver-001",
        chunk_id: "alpha",
      },
    },
    {
      id: "beta->part_of->ver-001",
      source_id: "beta",
      target_id: "ver-001",
      kind: "part_of",
      properties: {
        document_id: "doc-001",
        version_id: "ver-001",
        chunk_id: "beta",
      },
    },
    {
      id: "ver-001:alpha->same_topic_as->beta",
      source_id: "alpha",
      target_id: "beta",
      kind: "same_topic_as",
      properties: {
        document_id: "doc-001",
        version_id: "ver-001",
        source_chunk_id: "alpha",
        target_chunk_id: "beta",
        score: 0.42,
        reason: "Share 3 topic keywords: audit, supplier, quality.",
        shared_keywords: ["audit", "quality", "supplier"],
      },
    },
    {
      id: "ver-001:alpha->belongs_to->topic-aaaa1111",
      source_id: "alpha",
      target_id: "topic-aaaa1111",
      kind: "belongs_to",
      properties: {
        document_id: "doc-001",
        version_id: "ver-001",
        chunk_id: "alpha",
        topic_id: "topic-aaaa1111",
        score: 1.0,
      },
    },
    {
      id: "ver-001:beta->belongs_to->topic-aaaa1111",
      source_id: "beta",
      target_id: "topic-aaaa1111",
      kind: "belongs_to",
      properties: {
        document_id: "doc-001",
        version_id: "ver-001",
        chunk_id: "beta",
        topic_id: "topic-aaaa1111",
        score: 1.0,
      },
    },
    {
      id: "ver-001:alpha->has_entity->entity-iso9001",
      source_id: "alpha",
      target_id: "entity-iso9001",
      kind: "has_entity",
      properties: {
        document_id: "doc-001",
        version_id: "ver-001",
        section_id: "alpha",
        predicate: "REFERENCES",
        confidence: 0.92,
        source_reference_id: "src-1",
      },
    },
  ],
};
