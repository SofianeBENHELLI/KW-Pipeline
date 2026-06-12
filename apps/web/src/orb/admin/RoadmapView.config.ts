/**
 * Roadmap gallery — static catalogue of features that are NOT yet
 * shipped (converged plan §C.3 / external plan §12.2 + §18.5).
 *
 * The gallery's job during the demo is to set expectations: every
 * card here is intentionally disabled so the audience understands
 * the surface exists in the plan but not yet on `main`. The plan
 * sections are linked into the converged plan
 * (`docs/roadmap/2026-05-17-converged-knowledge-pipeline-plan.md`)
 * which carries the actual backlog ordering.
 *
 * Add a card here when a new vision item lands on the roadmap;
 * remove it the moment the corresponding epic ships. The whole
 * gallery should drop to zero cards over the course of post-MVP.
 */

export type RoadmapCategory =
  | "cross-document"
  | "business-ontology"
  | "ingestion"
  | "operability"
  | "scale-out";

export interface RoadmapCard {
  /** Stable id used as data-testid and React key. */
  readonly id: string;
  /** Card title (sentence case, < 60 chars). */
  readonly title: string;
  /** One-sentence description — the value the feature would unlock. */
  readonly description: string;
  /** Loose grouping; the gallery clusters cards by category. */
  readonly category: RoadmapCategory;
  /** Section of the converged plan that scopes the work. */
  readonly planSection: string;
  /** Rough effort band — sets honest expectations without numbers. */
  readonly effort: "S" | "M" | "L" | "XL";
  /** Known external blocker (license, partner, ADR change). */
  readonly blockedOn?: string;
  /** Optional GitHub epic / issue reference. */
  readonly tracking?: string;
}

/** Recommended display order across the four categories. */
export const ROADMAP_CARDS: readonly RoadmapCard[] = [
  // ── Cross-document analysis (§D.3) ───────────────────────────────────
  {
    id: "cross-doc-compare",
    title: "Cross-document compare",
    description:
      "Side-by-side themes / claims / coverage for two validated documents.",
    category: "cross-document",
    planSection: "§D.3",
    effort: "M",
  },
  {
    id: "contradiction-detection",
    title: "Contradiction detection",
    description:
      "Surface claims with the same subject but conflicting predicates / objects across the corpus.",
    category: "cross-document",
    planSection: "§D.3",
    effort: "L",
  },
  {
    id: "executive-summary",
    title: "Executive summary per document",
    description:
      "One-shot LLM pass over the validated semantic document + claims producing a stakeholder-friendly synopsis.",
    category: "cross-document",
    planSection: "§D.3",
    effort: "M",
  },
  {
    id: "gap-analysis",
    title: "Taxonomy gap analysis",
    description:
      "Quantify which taxonomy categories the corpus does not yet cover and recommend ingestion priorities.",
    category: "cross-document",
    planSection: "§D.3",
    effort: "M",
    tracking: "#341",
  },

  // ── Business ontology (§D.5) ─────────────────────────────────────────
  {
    id: "business-ontology",
    title: "Business ontology archetypes",
    description:
      "BusinessDriver / KPI / Persona / Feature / Role / IPE node kinds with IMPLEMENTED_BY / MEASURED_BY / INFLUENCES edges.",
    category: "business-ontology",
    planSection: "§D.5",
    effort: "L",
  },
  {
    id: "taxonomy-graph-view",
    title: "Taxonomy graph view",
    description:
      "Render the validated taxonomy as a force-directed graph with concept-suggestion overlays.",
    category: "business-ontology",
    planSection: "§D.5",
    effort: "M",
    tracking: "#348",
  },
  {
    id: "taxonomy-version-compare",
    title: "Taxonomy version compare",
    description:
      "Side-by-side diff of two taxonomy versions with added / changed / removed concept rows.",
    category: "business-ontology",
    planSection: "§D.5",
    effort: "S",
    tracking: "#349",
  },
  {
    id: "skos-rdf-export",
    title: "Export to RDF / SKOS / JSON-LD",
    description:
      "Add SKOS, RDF Turtle, and JSON-LD serialisers to the taxonomy export path (YAML ships today).",
    category: "business-ontology",
    planSection: "§D.5 (could-have)",
    effort: "S",
    tracking: "#352",
  },

  // ── Ingestion completeness (§D.2) ────────────────────────────────────
  {
    id: "ocr-pipeline",
    title: "OCR for scanned PDFs",
    description:
      "Tesseract + OCRmyPDF enricher in the parser chain; threads the existing ocr_override_active confidence signal.",
    category: "ingestion",
    planSection: "§D.2",
    effort: "M",
    tracking: "#47",
  },
  {
    id: "image-parser",
    title: "Image-only document parser",
    description:
      "Pictures and screenshots ingested via the OCR path so the catalogue accepts more than the four shipped formats.",
    category: "ingestion",
    planSection: "§D.2",
    effort: "S",
  },
  {
    id: "tika-catchall",
    title: "Tika catch-all parser",
    description:
      "Optional Apache Tika fallback for .rtf / .epub / .html / .csv when a customer brings a non-MVP corpus.",
    category: "ingestion",
    planSection: "§D.2 (evaluation spike)",
    effort: "M",
  },

  // ── Operability (§D.1) ───────────────────────────────────────────────
  {
    id: "neo4j-degraded-mode",
    title: "Neo4j degraded mode",
    description:
      "Lazy-connect driver + structured 503 envelopes when the graph is unreachable, so an outage doesn't take ingest offline.",
    category: "operability",
    planSection: "§D.1",
    effort: "S",
  },
  {
    id: "chunk-review-pane",
    title: "Chunk-level HITL review",
    description:
      "Per-chunk accept / reject / annotate surface — the true HITL gate complementing the document-level FSM.",
    category: "operability",
    planSection: "(EPIC #306)",
    effort: "L",
    tracking: "#306",
  },
  {
    id: "iterop-adapter",
    title: "External ITEROP review",
    description:
      "Dispatch a low-confidence document to an external workflow system; receive validated payload back via webhook.",
    category: "operability",
    planSection: "(EPIC #216)",
    effort: "XL",
    blockedOn: "external integration partner",
    tracking: "#216",
  },

  // ── Scale-out (§D.6 / §D.7) ──────────────────────────────────────────
  {
    id: "neo4j-decouple-sqlite",
    title: "SQLite-backed graph store",
    description:
      "Implement the GraphStore Protocol on SQLite so Neo4j becomes optional performance optimisation, not required infra.",
    category: "scale-out",
    planSection: "§D.6",
    effort: "L",
  },
  {
    id: "postgres-catalog",
    title: "Postgres catalog migration",
    description:
      "Replace SQLite with managed Postgres when single-pod write capacity becomes the bottleneck.",
    category: "scale-out",
    planSection: "§D.7",
    effort: "L",
  },
  {
    id: "object-storage",
    title: "S3-compatible object storage",
    description:
      "Move artifact storage off the local filesystem so the API process becomes stateless and horizontally scalable.",
    category: "scale-out",
    planSection: "§D.7",
    effort: "M",
  },
];

/** Display order for the category sections in the gallery. */
export const ROADMAP_CATEGORY_ORDER: readonly RoadmapCategory[] = [
  "cross-document",
  "business-ontology",
  "ingestion",
  "operability",
  "scale-out",
];

/** Human-readable section header for each category. */
export const ROADMAP_CATEGORY_LABEL: Readonly<
  Record<RoadmapCategory, { title: string; description: string }>
> = {
  "cross-document": {
    title: "Cross-document analysis",
    description:
      "Strategic semantics across the corpus — compare, contradict, summarise.",
  },
  "business-ontology": {
    title: "Business ontology layer",
    description:
      "Domain-specific taxonomy archetypes and value-chain relationship modelling.",
  },
  ingestion: {
    title: "Ingestion completeness",
    description:
      "Coverage for non-textual PDFs and out-of-MVP file formats.",
  },
  operability: {
    title: "Operability + HITL depth",
    description:
      "Graceful degradation, finer-grained review surfaces, external workflow integration.",
  },
  "scale-out": {
    title: "Scale-out",
    description:
      "Storage and infrastructure changes that fall out of single-pod operation.",
  },
};

export const ROADMAP_EFFORT_LABEL: Readonly<Record<RoadmapCard["effort"], string>> =
  {
    S: "small · days",
    M: "medium · 1–2 weeks",
    L: "large · 3–5 weeks",
    XL: "epic · multi-sprint",
  };
