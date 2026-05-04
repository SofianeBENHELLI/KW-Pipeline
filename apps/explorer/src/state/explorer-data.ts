/**
 * Knowledge Explorer data model + sample corpus.
 *
 * Port of the design's `data.jsx`, with two upgrades:
 *
 *   1. Strongly-typed shapes — the design ran in raw JSX so it leaned
 *      on duck typing. The components consuming this module want
 *      narrowed types, so we author them once here.
 *   2. Adapter helpers that map a live KW-Pipeline backend response
 *      onto the same shape, so the canvas + panels render off the
 *      real corpus when one is connected and gracefully fall back to
 *      this sample when the backend is empty / unreachable.
 *
 * The component layer should never import the API client directly —
 * it asks for a `ExplorerSnapshot`, this module decides whether the
 * live or sample data fills it.
 */

import type {
  Document as ApiDocument,
  KnowledgeGraphPage,
  KnowledgeGraphProjection,
  RawExtraction,
  SemanticDocument,
} from "../api/types";

// ─── Document type registry ──────────────────────────────────────────────────

export interface DocTypeMeta {
  label: string;
  color: string;
  short: string;
}

export const DOC_TYPES: Record<string, DocTypeMeta> = {
  pdf: { label: "PDF", color: "#C2453A", short: "PDF" },
  ppt: { label: "PowerPoint", color: "#D9892C", short: "PPT" },
  doc: { label: "Word", color: "#2D5BA8", short: "DOC" },
  wiki: { label: "Wiki", color: "#6E4FB8", short: "WIKI" },
  post: { label: "SWYM Post", color: "#3F8E60", short: "POST" },
  md: { label: "Markdown", color: "#5C6573", short: "MD" },
  web: { label: "Web Page", color: "#1F8088", short: "WEB" },
};

export type DocTypeKey = keyof typeof DOC_TYPES;

export const SOURCE_SYSTEMS = [
  "SharePoint",
  "Confluence",
  "ENOVIA",
  "SWYM",
  "Local Drive",
  "Web",
] as const;

export type SourceSystem = (typeof SOURCE_SYSTEMS)[number];

// ─── Cluster registry ────────────────────────────────────────────────────────

export interface ClusterMeta {
  label: string;
  hue: number;
}

export const CLUSTERS: Record<string, ClusterMeta> = {
  hr: { label: "People & HR", hue: 200 },
  product: { label: "Product", hue: 32 },
  eng: { label: "Engineering", hue: 220 },
  legal: { label: "Legal & Risk", hue: 340 },
  finance: { label: "Finance", hue: 150 },
};

// ─── Core entities the explorer renders ──────────────────────────────────────

export interface ExplorerDocument {
  id: string;
  title: string;
  type: DocTypeKey;
  source: string;
  date: string;
  chunks: number;
  cluster: string;
  /** Initial layout x in [0..1]; canvas re-positions for focus mode. */
  x: number;
  y: number;
  confidence: number;
}

export type DocEdgeType = "reference" | "similar" | "contains" | "contradict";

export interface ExplorerDocEdge {
  a: string;
  b: string;
  type: DocEdgeType;
  weight: number;
}

export interface ExplorerChunk {
  id: string;
  doc: string;
  label: string;
  page: number;
  kind: string;
  confidence: number;
  summary: string;
}

export interface ExplorerConcept {
  id: string;
  name: string;
  kind: string;
  freq: number;
  confidence: number;
  syn: string[];
}

export type ConceptEdgeType = "related";

export type ChunkConceptLink = readonly [string, string];
export type ConceptEdge = readonly [string, string, ConceptEdgeType];

// ─── Document content (paragraph-level for the in-app viewer) ────────────────

export interface DocPage {
  n: number;
  heading: string;
  paras: string[];
}

export interface DocContent {
  title: string;
  pages: DocPage[];
  /** Map chunk id → page+paragraph anchors so the viewer can highlight. */
  chunkAnchors: Record<string, { page: number; paras: number[] }>;
}

// ─── Sample corpus (fallback — kept verbatim from data.jsx) ──────────────────

export const SAMPLE_DOCUMENTS: ExplorerDocument[] = [
  { id: "d1",  title: "Global Hybrid Work Policy v4",            type: "pdf",  source: "SharePoint", date: "2026-02-14", chunks: 84,  cluster: "hr",      x: 0.16, y: 0.22, confidence: 0.92 },
  { id: "d2",  title: "Performance Review Cycle — FY26 Guide",   type: "doc",  source: "SharePoint", date: "2026-01-28", chunks: 56,  cluster: "hr",      x: 0.10, y: 0.42, confidence: 0.89 },
  { id: "d3",  title: "Onboarding Handbook",                     type: "wiki", source: "Confluence", date: "2026-03-05", chunks: 71,  cluster: "hr",      x: 0.22, y: 0.58, confidence: 0.86 },
  { id: "d4",  title: "Atlas — PRD: Federated Search",           type: "doc",  source: "Confluence", date: "2026-02-22", chunks: 92,  cluster: "product", x: 0.40, y: 0.16, confidence: 0.93 },
  { id: "d5",  title: "Q2 Roadmap Review",                       type: "ppt",  source: "SharePoint", date: "2026-03-12", chunks: 48,  cluster: "product", x: 0.54, y: 0.12, confidence: 0.88 },
  { id: "d6",  title: "User Research — Search Findings",         type: "pdf",  source: "SharePoint", date: "2026-02-09", chunks: 63,  cluster: "product", x: 0.46, y: 0.30, confidence: 0.90 },
  { id: "d7",  title: "Product Strategy 2026",                   type: "ppt",  source: "SharePoint", date: "2026-01-15", chunks: 41,  cluster: "product", x: 0.62, y: 0.26, confidence: 0.87 },
  { id: "d8",  title: "Platform Architecture — RFC 142",         type: "md",   source: "Local Drive",date: "2026-02-27", chunks: 102, cluster: "eng",     x: 0.74, y: 0.34, confidence: 0.94 },
  { id: "d9",  title: "Vector DB Migration Plan",                type: "doc",  source: "Confluence", date: "2026-03-08", chunks: 58,  cluster: "eng",     x: 0.86, y: 0.42, confidence: 0.91 },
  { id: "d10", title: "Eng All-Hands — March",                   type: "post", source: "SWYM",       date: "2026-03-30", chunks: 18,  cluster: "eng",     x: 0.78, y: 0.54, confidence: 0.82 },
  { id: "d11", title: "Data Processing Agreement Template",      type: "pdf",  source: "ENOVIA",     date: "2026-02-19", chunks: 39,  cluster: "legal",   x: 0.34, y: 0.74, confidence: 0.93 },
  { id: "d12", title: "GDPR Compliance Memo",                    type: "doc",  source: "SharePoint", date: "2026-03-18", chunks: 47,  cluster: "legal",   x: 0.22, y: 0.84, confidence: 0.91 },
  { id: "d13", title: "AI Usage Policy",                         type: "wiki", source: "Confluence", date: "2026-03-22", chunks: 33,  cluster: "legal",   x: 0.48, y: 0.82, confidence: 0.88 },
  { id: "d14", title: "FY26 Budget Plan",                        type: "ppt",  source: "SharePoint", date: "2026-01-22", chunks: 52,  cluster: "finance", x: 0.62, y: 0.74, confidence: 0.89 },
  { id: "d15", title: "Vendor Spend Analysis Q1",                type: "pdf",  source: "SharePoint", date: "2026-03-05", chunks: 28,  cluster: "finance", x: 0.74, y: 0.82, confidence: 0.85 },
  { id: "d16", title: "EU AI Act — Reference Notes",             type: "web",  source: "Web",        date: "2026-02-11", chunks: 22,  cluster: "legal",   x: 0.06, y: 0.66, confidence: 0.80 },
];

export const SAMPLE_DOC_EDGES: ExplorerDocEdge[] = [
  { a: "d1",  b: "d2",  type: "reference",  weight: 0.7 },
  { a: "d1",  b: "d3",  type: "reference",  weight: 0.8 },
  { a: "d2",  b: "d3",  type: "similar",    weight: 0.65 },
  { a: "d4",  b: "d5",  type: "reference",  weight: 0.85 },
  { a: "d4",  b: "d6",  type: "reference",  weight: 0.9 },
  { a: "d5",  b: "d7",  type: "similar",    weight: 0.7 },
  { a: "d6",  b: "d7",  type: "reference",  weight: 0.55 },
  { a: "d4",  b: "d8",  type: "reference",  weight: 0.75 },
  { a: "d8",  b: "d9",  type: "contains",   weight: 0.85 },
  { a: "d8",  b: "d10", type: "reference",  weight: 0.5 },
  { a: "d9",  b: "d10", type: "similar",    weight: 0.55 },
  { a: "d11", b: "d12", type: "reference",  weight: 0.85 },
  { a: "d11", b: "d13", type: "similar",    weight: 0.7 },
  { a: "d12", b: "d13", type: "reference",  weight: 0.75 },
  { a: "d13", b: "d16", type: "reference",  weight: 0.7 },
  { a: "d12", b: "d16", type: "reference",  weight: 0.6 },
  { a: "d14", b: "d15", type: "contains",   weight: 0.8 },
  { a: "d14", b: "d7",  type: "reference",  weight: 0.5 },
  { a: "d4",  b: "d13", type: "reference",  weight: 0.6 },
  { a: "d8",  b: "d11", type: "reference",  weight: 0.45 },
  { a: "d6",  b: "d12", type: "contradict", weight: 0.4 },
];

export const SAMPLE_CHUNKS: ExplorerChunk[] = [
  { id: "c1.1",  doc: "d1",  label: "Office attendance baseline",                page: 4,  kind: "section",   confidence: 0.95, summary: "Employees are expected on-site at least 3 days per week, with team-defined anchor days." },
  { id: "c1.2",  doc: "d1",  label: "Remote work eligibility",                   page: 7,  kind: "paragraph", confidence: 0.91, summary: "Fully remote roles require VP approval and are reviewed annually." },
  { id: "c1.3",  doc: "d1",  label: "Equipment & stipend",                       page: 11, kind: "section",   confidence: 0.88, summary: "Home office stipend of €500 with 3-year refresh cycle." },
  { id: "c1.4",  doc: "d1",  label: "Cross-border work limits",                  page: 16, kind: "paragraph", confidence: 0.86, summary: "Working from non-home jurisdiction is capped at 30 days per calendar year." },
  { id: "c4.1",  doc: "d4",  label: "Problem statement",                         page: 2,  kind: "section",   confidence: 0.94, summary: "Knowledge workers spend ~21% of their week searching for internal information." },
  { id: "c4.2",  doc: "d4",  label: "Target metrics",                            page: 5,  kind: "section",   confidence: 0.92, summary: "Reduce mean time-to-answer by 40%; achieve 80% recall on labeled benchmark." },
  { id: "c4.3",  doc: "d4",  label: "Privacy & access controls",                 page: 12, kind: "paragraph", confidence: 0.90, summary: "Per-source ACL enforcement at query time; no cross-tenant leakage." },
  { id: "c8.1",  doc: "d8",  label: "Service topology",                          page: 3,  kind: "section",   confidence: 0.93, summary: "Three-plane separation: ingest, index, query. gRPC between planes, Kafka for backpressure." },
  { id: "c8.2",  doc: "d8",  label: "Embedding model selection",                 page: 9,  kind: "section",   confidence: 0.91, summary: "1024-dim multilingual encoder; cosine similarity; nightly re-embedding pipeline." },
  { id: "c8.3",  doc: "d8",  label: "Cost envelope",                             page: 18, kind: "paragraph", confidence: 0.86, summary: "$0.018 per 1k embeddings amortized at projected Q3 volume." },
  { id: "c12.1", doc: "d12", label: "Lawful basis — legitimate interest",        page: 3,  kind: "section",   confidence: 0.93, summary: "Internal search indexing relies on legitimate interest under Art. 6(1)(f)." },
  { id: "c12.2", doc: "d12", label: "Data minimization",                         page: 6,  kind: "section",   confidence: 0.90, summary: "Personal identifiers are stripped from chunks before vector storage where feasible." },
];

export const SAMPLE_CONCEPTS: ExplorerConcept[] = [
  { id: "k1", name: "Hybrid work",       kind: "policy",      freq: 27, confidence: 0.92, syn: ["flexible work", "remote-first"] },
  { id: "k2", name: "Federated search",  kind: "product",     freq: 31, confidence: 0.94, syn: ["enterprise search", "unified search"] },
  { id: "k3", name: "Vector embeddings", kind: "engineering", freq: 24, confidence: 0.91, syn: ["dense retrieval"] },
  { id: "k4", name: "Access control",    kind: "engineering", freq: 19, confidence: 0.90, syn: ["ACL", "permissions"] },
  { id: "k5", name: "GDPR",              kind: "regulatory",  freq: 22, confidence: 0.93, syn: ["data protection regulation"] },
  { id: "k6", name: "Time-to-answer",    kind: "metric",      freq: 14, confidence: 0.88, syn: ["TTA"] },
  { id: "k7", name: "Onboarding",        kind: "process",     freq: 12, confidence: 0.86, syn: ["new hire ramp"] },
  { id: "k8", name: "AI Act compliance", kind: "regulatory",  freq: 11, confidence: 0.85, syn: ["EU AI Act"] },
  { id: "k9", name: "Vendor spend",      kind: "finance",     freq: 9,  confidence: 0.83, syn: ["procurement spend"] },
];

export const SAMPLE_CHUNK_CONCEPT: ChunkConceptLink[] = [
  ["c1.1", "k1"], ["c1.2", "k1"], ["c1.4", "k1"],
  ["c4.1", "k2"], ["c4.2", "k2"], ["c4.2", "k6"],
  ["c8.1", "k2"], ["c8.2", "k3"],
  ["c4.3", "k4"], ["c8.1", "k4"],
  ["c12.1", "k5"], ["c12.2", "k5"], ["c12.2", "k4"],
  ["c12.1", "k8"],
];

export const SAMPLE_CONCEPT_EDGES: ConceptEdge[] = [
  ["k2", "k3", "related"],
  ["k2", "k4", "related"],
  ["k4", "k5", "related"],
  ["k5", "k8", "related"],
  ["k1", "k7", "related"],
  ["k2", "k6", "related"],
  ["k3", "k4", "related"],
];

export const SAMPLE_DOC_CONTENT: Record<string, DocContent> = {
  d1: {
    title: "Global Hybrid Work Policy v4",
    pages: [
      { n: 4,  heading: "2.1  Office attendance baseline", paras: [
        "All employees in roles classified as Hybrid are expected on-site for a minimum of three (3) working days per calendar week, averaged over a rolling four-week window.",
        "Each team shall define two team-wide anchor days during which in-person collaboration is prioritized; remaining on-site days are at the employee's discretion.",
        "Exceptions for medical, caregiving, or accessibility reasons follow the process defined in Annex B and are handled confidentially by People Operations.",
      ] },
      { n: 7,  heading: "2.4  Remote work eligibility", paras: [
        "Roles classified as Fully Remote require explicit Vice-President sponsorship and are subject to annual review against current business needs.",
        "Conversion of an existing Hybrid role to Fully Remote requires a written justification, manager support, and HRBP review.",
        "Fully Remote employees are expected to participate in scheduled in-person gatherings (offsites, planning weeks) at company expense.",
      ] },
      { n: 11, heading: "3.2  Equipment & stipend", paras: [
        "Each Hybrid or Fully Remote employee is entitled to a one-time home office stipend of €500 (or local equivalent), refreshed every three (3) years.",
        "Standard equipment provisioning includes a laptop, external monitor, keyboard, and headset, owned by the company and returned upon separation.",
      ] },
      { n: 16, heading: "4.1  Cross-border work", paras: [
        "Employees may work from a jurisdiction outside their country of employment for no more than thirty (30) calendar days per year, subject to manager approval.",
        "Stays exceeding 30 days require coordination with Tax & Mobility and may require a temporary assignment letter.",
      ] },
    ],
    chunkAnchors: {
      "c1.1": { page: 4,  paras: [0, 1, 2] },
      "c1.2": { page: 7,  paras: [0, 1, 2] },
      "c1.3": { page: 11, paras: [0, 1] },
      "c1.4": { page: 16, paras: [0, 1] },
    },
  },
  d4: {
    title: "Atlas — PRD: Federated Search",
    pages: [
      { n: 2,  heading: "1.  Problem statement", paras: [
        "Internal research conducted in Q4 indicates that knowledge workers across the organisation spend approximately 21% of their working week searching for, or recreating, information that already exists somewhere inside the company.",
        "Existing point search tools are siloed by source system (SharePoint, Confluence, ENOVIA, SWYM) and require employees to know in advance where the answer is likely to live.",
        "The result is duplicated work, inconsistent answers to the same question, and slow ramp time for new hires and cross-functional contributors.",
      ] },
      { n: 5,  heading: "2.  Target metrics", paras: [
        "Reduce median Time-To-Answer (TTA) for the Top-200 internal questions by at least 40%, measured against the FY25 baseline.",
        "Achieve 80% recall and 65% precision at rank-5 on the labeled internal benchmark maintained by the Search Quality team.",
        "Maintain a query latency P95 below 800 ms for federated results across at least four source systems.",
      ] },
      { n: 12, heading: "4.3  Privacy & access controls", paras: [
        "Access control lists are enforced at query time, on a per-source basis, using the user's effective permissions in the source system of record.",
        "Index storage is partitioned per source system; cross-tenant or cross-source result leakage is treated as a P0 incident.",
        "Personal data inside indexed chunks is subject to the data minimization principles described in §4.4.",
      ] },
    ],
    chunkAnchors: {
      "c4.1": { page: 2,  paras: [0, 1, 2] },
      "c4.2": { page: 5,  paras: [0, 1, 2] },
      "c4.3": { page: 12, paras: [0, 1, 2] },
    },
  },
  d8: {
    title: "Platform Architecture — RFC 142",
    pages: [
      { n: 3,  heading: "## Service topology", paras: [
        "The platform is decomposed along three planes: an Ingest plane responsible for source connectors and chunking, an Index plane responsible for embedding and storage, and a Query plane responsible for retrieval and fusion.",
        "Inter-plane communication uses gRPC for synchronous calls and Kafka topics for asynchronous, backpressure-tolerant work such as embedding jobs and re-indexing.",
        "Each plane is independently scalable and has its own SLOs; cross-plane SLOs are tracked at the user-visible Query level.",
      ] },
      { n: 9,  heading: "## Embedding model", paras: [
        "We standardize on a 1024-dimensional multilingual encoder for all text chunks, using cosine similarity at query time.",
        "A nightly re-embedding pipeline reprocesses chunks whose source documents changed, with a hard upper bound of 14 days between any chunk write and its corresponding embedding update.",
      ] },
      { n: 18, heading: "## Cost envelope", paras: [
        "At projected Q3 volume (≈ 2.4 B chunks), embedding cost amortizes to $0.018 per 1k chunks on the current vendor contract.",
        "Storage cost in the vector database is dominated by index overhead, not raw vectors; we estimate a 1.6× multiplier over raw float32 size.",
      ] },
    ],
    chunkAnchors: {
      "c8.1": { page: 3,  paras: [0, 1, 2] },
      "c8.2": { page: 9,  paras: [0, 1] },
      "c8.3": { page: 18, paras: [0, 1] },
    },
  },
  d12: {
    title: "GDPR Compliance Memo",
    pages: [
      { n: 3, heading: "2.  Lawful basis", paras: [
        "Indexing of internal documents for the purpose of employee-facing federated search is performed on the basis of legitimate interest under Article 6(1)(f) GDPR.",
        "A balancing test has been completed and is recorded with the DPO; the assessment concludes that employee expectations are met provided source-system access controls are honored at query time.",
      ] },
      { n: 6, heading: "3.  Data minimization", paras: [
        "Where technically feasible, personal identifiers (full names of non-public individuals, direct contact details) are removed or hashed before chunks are written to vector storage.",
        "Free-text chunks that cannot be safely minimized are excluded from federated search by source-system policy and routed only to authorized internal tools.",
      ] },
    ],
    chunkAnchors: {
      "c12.1": { page: 3, paras: [0, 1] },
      "c12.2": { page: 6, paras: [0, 1] },
    },
  },
};

// ─── Snapshot bundle the components consume ──────────────────────────────────

export interface ExplorerSnapshot {
  documents: ExplorerDocument[];
  docEdges: ExplorerDocEdge[];
  chunks: ExplorerChunk[];
  concepts: ExplorerConcept[];
  chunkConcept: ChunkConceptLink[];
  conceptEdges: ConceptEdge[];
  docContent: Record<string, DocContent>;
  /** When true, the data is the design's sample fallback. */
  isSample: boolean;
  /** Display name shown in the header (corpus name). */
  corpusLabel: string;
}

export const SAMPLE_SNAPSHOT: ExplorerSnapshot = {
  documents: SAMPLE_DOCUMENTS,
  docEdges: SAMPLE_DOC_EDGES,
  chunks: SAMPLE_CHUNKS,
  concepts: SAMPLE_CONCEPTS,
  chunkConcept: SAMPLE_CHUNK_CONCEPT,
  conceptEdges: SAMPLE_CONCEPT_EDGES,
  docContent: SAMPLE_DOC_CONTENT,
  isSample: true,
  corpusLabel: "Acme Corp HQ · sample",
};

// ─── Look-up helpers (stateless; bind a snapshot at the call site) ───────────

export function docById(snap: ExplorerSnapshot, id: string): ExplorerDocument | undefined {
  return snap.documents.find((d) => d.id === id);
}
export function chunkById(snap: ExplorerSnapshot, id: string): ExplorerChunk | undefined {
  return snap.chunks.find((c) => c.id === id);
}
export function conceptById(snap: ExplorerSnapshot, id: string): ExplorerConcept | undefined {
  return snap.concepts.find((k) => k.id === id);
}
export function conceptsForChunk(snap: ExplorerSnapshot, id: string): ExplorerConcept[] {
  return snap.chunkConcept
    .filter(([c]) => c === id)
    .map(([, k]) => conceptById(snap, k))
    .filter((x): x is ExplorerConcept => Boolean(x));
}
export function chunksForConcept(snap: ExplorerSnapshot, id: string): ExplorerChunk[] {
  return snap.chunkConcept
    .filter(([, k]) => k === id)
    .map(([c]) => chunkById(snap, c))
    .filter((x): x is ExplorerChunk => Boolean(x));
}
export function chunksForDoc(snap: ExplorerSnapshot, id: string): ExplorerChunk[] {
  return snap.chunks.filter((c) => c.doc === id);
}
export function docsForConcept(snap: ExplorerSnapshot, id: string): ExplorerDocument[] {
  const docIds = new Set(chunksForConcept(snap, id).map((c) => c.doc));
  return [...docIds].map((d) => docById(snap, d)).filter((x): x is ExplorerDocument => Boolean(x));
}

// ─── Live-data adapter ───────────────────────────────────────────────────────

/**
 * Map a single live document onto the explorer model. Cluster, type
 * and date are all best-effort guesses; the explorer treats
 * `cluster === "unknown"` gracefully when neither semantic nor graph
 * data is available yet.
 */
export function adaptDocument(
  doc: ApiDocument,
  semantic: SemanticDocument | null,
  extraction: RawExtraction | null,
  index: number,
  total: number,
): ExplorerDocument {
  const latest = doc.versions.find((v) => v.id === doc.latest_version_id) ?? doc.versions[doc.versions.length - 1];
  const ct = latest?.content_type ?? "";
  const fname = latest?.filename ?? doc.original_filename;
  const type = classifyContentType(ct, fname);
  const profileType = semantic?.document_profile.document_type;
  const cluster = profileType && profileType !== "unknown" ? profileType : "unknown";
  const chunks = extraction?.sections.length ?? semantic?.sections.length ?? 0;
  const confidence = semantic
    ? semantic.assets.length > 0
      ? semantic.assets.reduce((a, b) => a + b.confidence, 0) / semantic.assets.length
      : 0.85
    : 0.7;
  // Place documents on a deterministic ring while we don't have force-
  // directed layout — the canvas re-positions them per-cluster anyway.
  const angle = (index / Math.max(total, 1)) * Math.PI * 2;
  return {
    id: doc.id,
    title: doc.original_filename,
    type,
    // Storage URI scheme is the only source signal we have today —
    // ``memory://``, ``file://`` and ``s3://`` are the schemes the
    // pipeline emits via :class:`StorageService`. We map each one
    // to a human-readable label rather than fabricating an external
    // origin (the previous code defaulted everything else to
    // "SharePoint" which lied to operators). When the pipeline
    // grows a real source-of-record link (#89), this is the seam
    // that opens to it.
    source: storageSourceLabel(latest?.storage_uri),
    date: (latest?.created_at ?? doc.created_at).slice(0, 10),
    chunks,
    cluster,
    x: 0.5 + Math.cos(angle) * 0.3,
    y: 0.5 + Math.sin(angle) * 0.3,
    confidence,
  };
}

function storageSourceLabel(storageUri: string | undefined): string {
  if (!storageUri) return "Unknown";
  if (storageUri.startsWith("memory://")) return "In-memory";
  if (storageUri.startsWith("file://")) return "Local Drive";
  if (storageUri.startsWith("s3://")) return "S3";
  if (storageUri.startsWith("http://") || storageUri.startsWith("https://")) return "Web";
  // Unknown scheme — surface verbatim instead of inventing an origin.
  const scheme = storageUri.split("://", 1)[0];
  return scheme || "Unknown";
}

const CONTENT_TYPE_TO_DOC_KEY: Array<[RegExp, DocTypeKey]> = [
  [/^application\/pdf$/i, "pdf"],
  [/wordprocessingml|msword|application\/vnd\.ms-word/i, "doc"],
  [/presentationml|powerpoint|vnd\.ms-powerpoint/i, "ppt"],
  [/^text\/markdown|^text\/x-markdown/i, "md"],
  [/^text\/html/i, "web"],
];

const FILENAME_EXT_TO_DOC_KEY: Record<string, DocTypeKey> = {
  pdf: "pdf",
  doc: "doc",
  docx: "doc",
  ppt: "ppt",
  pptx: "ppt",
  md: "md",
  markdown: "md",
  html: "web",
  htm: "web",
  wiki: "wiki",
};

function classifyContentType(ct: string, filename: string): DocTypeKey {
  for (const [pattern, key] of CONTENT_TYPE_TO_DOC_KEY) {
    if (pattern.test(ct)) return key;
  }
  const dot = filename.lastIndexOf(".");
  if (dot >= 0 && dot < filename.length - 1) {
    const ext = filename.slice(dot + 1).toLowerCase();
    if (ext in FILENAME_EXT_TO_DOC_KEY) return FILENAME_EXT_TO_DOC_KEY[ext];
  }
  return "doc";
}

/**
 * Project the catalog-wide knowledge graph page into explorer concepts
 * + chunk↔concept + concept↔concept links + doc↔doc links.
 *
 * Backend-side, edges are emitted in lower-snake form per
 * ``app.schemas.knowledge::GraphEdgeKind``:
 *
 *   - structural: ``part_of``, ``has_chunk``, ``has_version``, ``belongs_to``
 *   - deterministic semantic (chunk↔chunk): ``related_to``,
 *     ``shares_keyword``, ``same_topic_as``
 *   - LLM-emitted: ``has_entity`` (section→entity)
 *
 * The Explorer's design renders **document** edges (a/b/type/weight),
 * not chunk-level edges, so we **aggregate** the chunk-chunk semantic
 * edges into doc-doc edges by walking each end's ``document_id``
 * property. A pair (docA, docB) gets a single edge whose weight is
 * the per-pair sum of underlying chunk-edge weights, capped at 1.0.
 * This collapses N chunk relations between two docs into one
 * legible relation in the canvas.
 *
 * Concepts come from the typed ``entity`` nodes (Phase 2 LLM
 * extraction). ``topic`` nodes — the deterministic auto-deduced
 * clusters — are surfaced as concepts too in v1, with ``kind: "topic"``
 * so the UI can later branch on it. Separating ``topic`` from
 * ``concept`` is tracked as a follow-up to ADR-017.
 */
export function adaptGraph(page: KnowledgeGraphPage | KnowledgeGraphProjection | null): {
  concepts: ExplorerConcept[];
  chunkConcept: ChunkConceptLink[];
  conceptEdges: ConceptEdge[];
  docEdges: ExplorerDocEdge[];
} {
  if (!page) return { concepts: [], chunkConcept: [], conceptEdges: [], docEdges: [] };

  const concepts: ExplorerConcept[] = page.nodes
    .filter((n) => isConceptKind(n.kind))
    .map((n) => ({
      id: n.id,
      name: n.label || n.id,
      kind: n.kind.toLowerCase(),
      // Don't fabricate a frequency — leave at 0 when the projector
      // didn't write one. The renderer can hide the ×N counter when
      // the value is 0 rather than show a misleading ×1.
      freq: numProp(n.properties, "frequency") ?? numProp(n.properties, "count") ?? 0,
      // Default 1.0 when the projector produces a deterministic
      // node (topics) — it's not a "we guessed" 0.85; leaving it
      // unset would force callers to handle ``undefined`` everywhere.
      confidence: numProp(n.properties, "confidence") ?? 1.0,
      syn: stringArrayProp(n.properties, "synonyms") ?? [],
    }));
  const conceptIds = new Set(concepts.map((c) => c.id));

  // Map every chunk node id → its document_id so we can aggregate
  // chunk-chunk edges into doc-doc edges below. Chunk nodes are
  // emitted by the projector with ``document_id`` in their
  // properties (apps/api/app/services/knowledge/projector.py).
  const chunkToDoc = new Map<string, string>();
  for (const node of page.nodes) {
    if (node.kind.toLowerCase() === "chunk") {
      const docId = stringProp(node.properties, "document_id");
      if (docId) chunkToDoc.set(node.id, docId);
    }
  }

  const chunkConcept: ChunkConceptLink[] = [];
  const conceptEdges: ConceptEdge[] = [];
  // (docA, docB, kind) → accumulated weight. Keys are normalised to
  // (min, max, kind) so an A↔B and B↔A pair collapse to one entry.
  const docEdgeAcc = new Map<string, { a: string; b: string; type: DocEdgeType; weight: number }>();

  for (const e of page.edges) {
    const aIsConcept = conceptIds.has(e.source_id);
    const bIsConcept = conceptIds.has(e.target_id);

    if (aIsConcept && bIsConcept) {
      conceptEdges.push([e.source_id, e.target_id, "related"] as const);
      continue;
    }
    if (aIsConcept || bIsConcept) {
      const chunk = aIsConcept ? e.target_id : e.source_id;
      const concept = aIsConcept ? e.source_id : e.target_id;
      chunkConcept.push([chunk, concept] as const);
      continue;
    }

    // Chunk↔chunk semantic edges — aggregate to doc-doc.
    const kindKey = mapEdgeKind(e.kind);
    if (!kindKey) continue;
    const docA = chunkToDoc.get(e.source_id);
    const docB = chunkToDoc.get(e.target_id);
    if (!docA || !docB || docA === docB) continue;
    const [lo, hi] = docA < docB ? [docA, docB] : [docB, docA];
    const accKey = `${lo}|${hi}|${kindKey}`;
    const existing = docEdgeAcc.get(accKey);
    const weight = numProp(e.properties, "weight") ?? 0.5;
    if (existing) {
      existing.weight = Math.min(existing.weight + weight, 1.0);
    } else {
      docEdgeAcc.set(accKey, { a: lo, b: hi, type: kindKey, weight });
    }
  }
  const docEdges = [...docEdgeAcc.values()];

  return { concepts, chunkConcept, conceptEdges, docEdges };
}

/**
 * Map a backend edge kind to the Explorer's coarse doc-edge type.
 * Returns ``null`` for kinds that shouldn't aggregate to doc-doc
 * (structural ``part_of`` / ``has_chunk`` / ``has_version`` /
 * ``belongs_to`` / ``has_entity``).
 */
function mapEdgeKind(kind: string): DocEdgeType | null {
  switch (kind.toLowerCase()) {
    case "same_topic_as":
      return "similar";
    case "related_to":
      return "reference";
    case "shares_keyword":
      return "reference";
    default:
      return null;
  }
}

function isConceptKind(kind: string): boolean {
  const k = kind.toLowerCase();
  return k === "topic" || k === "entity" || k === "concept";
}

function numProp(props: Record<string, unknown>, key: string): number | null {
  const v = props[key];
  return typeof v === "number" ? v : null;
}

function stringProp(props: Record<string, unknown>, key: string): string | null {
  const v = props[key];
  return typeof v === "string" && v.length > 0 ? v : null;
}

function stringArrayProp(props: Record<string, unknown>, key: string): string[] | null {
  const v = props[key];
  return Array.isArray(v) ? v.filter((s): s is string => typeof s === "string") : null;
}

/**
 * Build per-document `DocContent` — paragraph-level pages — out of a
 * raw extraction. The design uses one `kx-page` per heading + a list
 * of paragraphs; we map one `RawSection` to one page and split its
 * `text` on blank-line groups to produce paragraph rows.
 */
export function adaptDocContent(
  doc: ApiDocument,
  extraction: RawExtraction | null,
  semantic: SemanticDocument | null,
): DocContent {
  if (!extraction) {
    return {
      title: doc.original_filename,
      pages: [{ n: 1, heading: doc.original_filename, paras: ["No extraction available for this version yet."] }],
      chunkAnchors: {},
    };
  }
  const pages: DocPage[] = extraction.sections.map((section, index) => ({
    n: section.page_number ?? index + 1,
    heading: section.heading || `Section ${index + 1}`,
    paras: splitParagraphs(section.text),
  }));
  const chunkAnchors: Record<string, { page: number; paras: number[] }> = {};
  // Map each semantic section's first source_reference back to a page.
  if (semantic) {
    for (const s of semantic.sections) {
      const refId = s.source_reference_ids[0];
      if (!refId) continue;
      const ref = extraction.source_references.find((r) => r.id === refId);
      if (!ref) continue;
      chunkAnchors[s.id] = {
        page: ref.page_number ?? 1,
        paras: [Math.max(0, (ref.line_start ?? 1) - 1)],
      };
    }
  }
  return {
    title: doc.original_filename,
    pages: pages.length > 0 ? pages : [{ n: 1, heading: extraction.parser_name, paras: [extraction.text || "(empty)"] }],
    chunkAnchors,
  };
}

function splitParagraphs(text: string): string[] {
  const trimmed = text.trim();
  if (!trimmed) return [];
  const paragraphs = trimmed.split(/\n{2,}/).map((p) => p.replace(/\s+/g, " ").trim()).filter((p) => p.length > 0);
  return paragraphs.length > 0 ? paragraphs : [trimmed];
}
