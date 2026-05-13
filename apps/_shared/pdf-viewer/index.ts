/**
 * Public surface for the shared PDF chunk-viewer toolkit.
 *
 * Bundler-agnostic primitives only: the selection hook, the side
 * panel, the highlight overlay, the local typed shapes, and the
 * stylesheet. The pdfjs-dist render loop and the network fetch live
 * in each consuming app's wrapper — those layers are bundler-specific
 * (Vite vs Webpack worker resolution) and API-specific (Orbital's
 * openapi-fetch client vs Explorer's hand-written fetcher).
 *
 * Consumers:
 *
 *   import {
 *     ChunkSidePanel,
 *     HighlightLayer,
 *     useChunkSelection,
 *   } from "../../../_shared/pdf-viewer";
 *   import type { ChunkLocation } from "../../../_shared/pdf-viewer";
 *
 * The stylesheet imports itself on first reference so each app only
 * needs the JS import to get the visuals.
 */

import "./pdf-viewer.css";

export { ChunkSidePanel } from "./ChunkSidePanel";
export { HighlightLayer } from "./HighlightLayer";
export { useChunkSelection } from "./useChunkSelection";
export type {
  ChunkSelection,
  ChunkSelectionActions,
  ChunkSelectionState,
} from "./useChunkSelection";
export type {
  ChunkLocation,
  ChunkLocationsResponse,
  ChunkSource,
  NormalizedRect,
} from "./types";
