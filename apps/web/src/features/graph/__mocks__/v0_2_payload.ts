/**
 * v0.2 knowledge-graph mock fixture.
 *
 * Used both by component tests (KnowledgeGraphView.test.tsx) and by the
 * "use mock data" path on ``KnowledgeGraphView`` so the demo can render
 * chunk/topic graphs today, before the live API (#144) returns them.
 *
 * The shape mirrors what the v0.2 backend will produce: a small
 * document family (1 document, 1 version, 1 section, 4 chunks, 2
 * topics) with structural ``part_of`` / ``has_version`` / ``has_chunk``
 * edges, ``belongs_to`` topic-membership edges, plus a couple of
 * cross-chunk semantic relations (``shares_keyword``, ``same_topic_as``,
 * ``related_to``).
 *
 * Keep this realistic but minimal — large fixtures slow tests and
 * don't exercise additional code paths. Roughly the 15-30 node
 * ballpark called out by issue #149.
 */

import type {
  GraphEdgeV02,
  GraphNodeV02,
  KnowledgeGraphProjectionV02,
} from "../types";

// ─── Node fixtures ──────────────────────────────────────────────────────────

const DOCUMENT_NODE: GraphNodeV02 = {
  id: "doc-001",
  kind: "document",
  label: "policy.pdf",
  properties: {},
};

const VERSION_NODE: GraphNodeV02 = {
  id: "ver-001",
  kind: "version",
  label: "v1",
  properties: {},
};

const SECTION_NODE: GraphNodeV02 = {
  id: "sec-001",
  kind: "section",
  label: "Section 1 — Eligibility",
  properties: {},
};

const CHUNK_NODES: GraphNodeV02[] = [
  {
    id: "chk-001",
    kind: "chunk",
    label: "Chunk 1",
    properties: {
      index: 0,
      token_count: 142,
      section_id: "sec-001",
      keywords: ["eligibility", "applicant", "residence"],
    },
  },
  {
    id: "chk-002",
    kind: "chunk",
    label: "Chunk 2",
    properties: {
      index: 1,
      token_count: 168,
      section_id: "sec-001",
      keywords: ["eligibility", "income", "household"],
    },
  },
  {
    id: "chk-003",
    kind: "chunk",
    label: "Chunk 3",
    properties: {
      index: 2,
      token_count: 121,
      section_id: "sec-001",
      keywords: ["application", "documents", "deadline"],
    },
  },
  {
    id: "chk-004",
    kind: "chunk",
    label: "Chunk 4",
    properties: {
      index: 3,
      token_count: 97,
      section_id: "sec-001",
      keywords: ["appeal", "review", "decision"],
    },
  },
];

const TOPIC_NODES: GraphNodeV02[] = [
  {
    id: "tpc-eligibility",
    kind: "topic",
    label: "Eligibility & income",
    properties: {
      keywords: ["eligibility", "applicant", "income", "household"],
      size: 2,
      score: 0.81,
    },
  },
  {
    id: "tpc-process",
    kind: "topic",
    label: "Application process",
    properties: {
      keywords: ["application", "documents", "appeal", "review"],
      size: 2,
      score: 0.74,
    },
  },
];

// ─── Edge fixtures ──────────────────────────────────────────────────────────

const STRUCTURAL_EDGES: GraphEdgeV02[] = [
  // version -> document (has_version)
  {
    id: "e-hv-1",
    kind: "has_version",
    source_id: "doc-001",
    target_id: "ver-001",
    properties: {},
  },
  // section -> version (part_of, kept for v0.1 back-compat)
  {
    id: "e-po-1",
    kind: "part_of",
    source_id: "sec-001",
    target_id: "ver-001",
    properties: {},
  },
  // version -> chunks (has_chunk) — one per chunk
  {
    id: "e-hc-1",
    kind: "has_chunk",
    source_id: "ver-001",
    target_id: "chk-001",
    properties: {},
  },
  {
    id: "e-hc-2",
    kind: "has_chunk",
    source_id: "ver-001",
    target_id: "chk-002",
    properties: {},
  },
  {
    id: "e-hc-3",
    kind: "has_chunk",
    source_id: "ver-001",
    target_id: "chk-003",
    properties: {},
  },
  {
    id: "e-hc-4",
    kind: "has_chunk",
    source_id: "ver-001",
    target_id: "chk-004",
    properties: {},
  },
];

const TOPIC_EDGES: GraphEdgeV02[] = [
  {
    id: "e-bt-1",
    kind: "belongs_to",
    source_id: "chk-001",
    target_id: "tpc-eligibility",
    properties: { score: 0.92 },
  },
  {
    id: "e-bt-2",
    kind: "belongs_to",
    source_id: "chk-002",
    target_id: "tpc-eligibility",
    properties: { score: 0.88 },
  },
  {
    id: "e-bt-3",
    kind: "belongs_to",
    source_id: "chk-003",
    target_id: "tpc-process",
    properties: { score: 0.79 },
  },
  {
    id: "e-bt-4",
    kind: "belongs_to",
    source_id: "chk-004",
    target_id: "tpc-process",
    properties: { score: 0.71 },
  },
];

const SEMANTIC_EDGES: GraphEdgeV02[] = [
  {
    id: "e-sk-1",
    kind: "shares_keyword",
    source_id: "chk-001",
    target_id: "chk-002",
    properties: {
      weight: 0.5,
      shared_keywords: ["eligibility"],
    },
  },
  {
    id: "e-st-1",
    kind: "same_topic_as",
    source_id: "chk-003",
    target_id: "chk-004",
    properties: {
      weight: 0.74,
      topic_id: "tpc-process",
    },
  },
  {
    id: "e-rt-1",
    kind: "related_to",
    source_id: "chk-002",
    target_id: "chk-003",
    properties: { weight: 0.42 },
  },
];

// ─── Assembled projection ───────────────────────────────────────────────────

/**
 * Realistic v0.2 projection for the demo / tests.
 *
 * 1 document + 1 version + 1 section + 4 chunks + 2 topics = 9 nodes.
 * 6 structural + 4 belongs_to + 3 semantic = 13 edges.
 */
export const MOCK_V0_2_PROJECTION: KnowledgeGraphProjectionV02 = {
  document_id: "doc-001",
  version_id: "ver-001",
  schema_version: "v0.2",
  generated_at: "2026-05-01T00:00:00Z",
  nodes: [DOCUMENT_NODE, VERSION_NODE, SECTION_NODE, ...CHUNK_NODES, ...TOPIC_NODES],
  edges: [...STRUCTURAL_EDGES, ...TOPIC_EDGES, ...SEMANTIC_EDGES],
};
