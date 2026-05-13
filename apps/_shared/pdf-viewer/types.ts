/**
 * Hand-written read-models for the PDF viewer.
 *
 * Mirrors :class:`NormalizedRect` and :class:`ChunkLocation` in
 * ``apps/api/app/schemas`` (see ``extraction.py`` and
 * ``chunk_location.py``) but is hand-written so this shared module
 * does not depend on either app's API-codegen pipeline — Orbital
 * regenerates via openapi-typescript, Explorer hand-writes, and the
 * widget can grow a third path. Keeping a small local mirror here
 * lets every consumer pass typed data in without reaching across
 * app boundaries.
 *
 * The OpenAPI snapshot test on the backend side is the drift guard;
 * a wire-shape change there should land here in the same PR.
 */

export interface NormalizedRect {
  readonly page: number;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export type ChunkSource = "ai_extraction" | "parser";

export interface ChunkLocation {
  readonly chunk_id: string;
  readonly document_id: string;
  readonly document_version_id: string;
  readonly document_hash: string;
  readonly page: number;
  readonly rects: NormalizedRect[];
  readonly heading: string;
  readonly snippet: string;
  readonly summary: string | null;
  readonly topic_id: string | null;
  readonly topic_label: string | null;
  readonly source: ChunkSource;
  readonly confidence: number;
  readonly pipeline_version: string;
}

export interface ChunkLocationsResponse {
  readonly schema_version: "v0.1";
  readonly document_id: string;
  readonly document_version_id: string;
  readonly document_hash: string;
  readonly parser_version: string;
  readonly items: ChunkLocation[];
}
