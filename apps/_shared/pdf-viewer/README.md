# apps/_shared/pdf-viewer

Shared, bundler-agnostic toolkit for the PDF chunk viewer.

## What lives here

Everything that does **not** touch the network or a specific PDF
renderer:

| File | Role |
|---|---|
| `types.ts` | Hand-written mirror of the backend's `ChunkLocation` / `NormalizedRect` Pydantic shapes. The OpenAPI snapshot test on the backend guards against drift. |
| `useChunkSelection.ts` | Small selection state machine — `selectedChunkId`, `hoveredChunkId`, `selectChunk`, `hoverChunk`, `clear`. |
| `HighlightLayer.tsx` | Absolute-positioned, CSS-percentage overlay drawn on top of one PDF page. AI / parser / selected / hovered visual variants. Knows nothing about pdfjs. |
| `ChunkSidePanel.tsx` | Virtualized chunk list (via `content-visibility: auto`) with page / source / min-confidence filters and free-text search. |
| `pdf-viewer.css` | Split-pane layout + highlight + tooltip styling. Imported automatically by `index.ts`. |

## What does NOT live here

| Concern | Why it's per-app |
|---|---|
| `pdfjs-dist` worker resolution | Vite uses `?url`, Webpack uses `new URL(..., import.meta.url)`. The renderer wrapper lives in each app. |
| Fetching `/documents/{id}/versions/{v}/chunks` | Orbital generates types via openapi-typescript; Explorer hand-writes types. Each app keeps its own typed client. |
| Fetching `/raw` bytes → blob URL | App-specific auth + base-URL conventions. |
| Vitest tests | Shared files are tested by the consuming app's test runner (Orbital's spec for `useChunkSelection` lives at `apps/web/src/features/pdf-viewer/`). |

## Consumer template

```ts
import {
  ChunkSidePanel,
  HighlightLayer,
  useChunkSelection,
} from "../../../_shared/pdf-viewer";
import type { ChunkLocation } from "../../../_shared/pdf-viewer";

function MyAppPdfViewer({ chunks }: { chunks: ChunkLocation[] }) {
  const selection = useChunkSelection();
  // app-specific pdfjs-dist render loop here, render
  // <HighlightLayer ... /> inside each page wrap, and
  // <ChunkSidePanel ... /> alongside the pages scroll.
}
```

## Wiring status (2026-05-13)

- **Orbital** (`apps/web`) — wired via
  [`apps/web/src/features/pdf-viewer/`](../../web/src/features/pdf-viewer/).
  The pure modules re-export from this shared package; the
  Orbital-specific renderer (`PdfChunkViewer`) and loader
  (`PdfViewerPanel`) stay in the app. Vite's `?url` suffix resolves
  the pdfjs worker URL.
- **Explorer** (`apps/explorer`) — wired via
  [`apps/explorer/src/components/PdfChunkViewer.tsx`](../../explorer/src/components/PdfChunkViewer.tsx)
  and consumed by
  [`apps/explorer/src/components/DocViewer.tsx`](../../explorer/src/components/DocViewer.tsx).
  Webpack 5's `new URL("...", import.meta.url)` form resolves the
  pdfjs worker URL. The branch fires only for real backend PDFs
  (non-empty `version.sha256`); synthetic samples and non-PDF
  formats keep the legacy paragraph-card fallback.
