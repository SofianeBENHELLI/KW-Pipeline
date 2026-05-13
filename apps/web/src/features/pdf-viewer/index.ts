/**
 * Orbital-facing surface for the PDF chunk viewer feature.
 *
 * The pure pieces — selection hook, side panel, highlight overlay,
 * types, and stylesheet — live in
 * ``apps/_shared/pdf-viewer/`` and are re-exported here so call sites
 * inside ``apps/web`` keep using a stable internal path. The
 * Orbital-specific renderer (``PdfChunkViewer``) and loader
 * (``PdfViewerPanel``) are app-local because they bind to apps/web's
 * openapi-fetch client and Vite's worker-URL resolution.
 */

export {
  ChunkSidePanel,
  HighlightLayer,
  useChunkSelection,
} from "../../../../_shared/pdf-viewer";
export type {
  ChunkLocation,
  ChunkLocationsResponse,
  ChunkSelection,
  ChunkSelectionActions,
  ChunkSelectionState,
  ChunkSource,
  NormalizedRect,
} from "../../../../_shared/pdf-viewer";

export { PdfChunkViewer } from "./PdfChunkViewer";
export { PdfViewerPanel } from "./PdfViewerPanel";
