/**
 * Split-pane PDF viewer with bidirectional chunk highlight sync.
 *
 * Left pane: pdfjs-dist renders every page into its own canvas; a
 * :class:`HighlightLayer` sits on top of each canvas drawing rect
 * overlays from the backend-normalised coordinates. Click a rect →
 * the selection promotes; the side panel scrolls to the matching row.
 *
 * Right pane: :class:`ChunkSidePanel` lists every chunk with filters
 * and search. Click a row → the viewer scrolls to and flashes the
 * first rect of that chunk.
 *
 * Hash gate: the viewer asserts the response's ``document_hash``
 * matches the version's SHA-256 before rendering rects. Mismatch
 * surfaces a tombstone card with the two hashes side-by-side so an
 * operator can debug which PDF the rects were computed against.
 *
 * EmbedPDF migration: the renderer adapter is intentionally narrow —
 * one effect that consumes ``pdfjs-dist`` directly. A future PR can
 * swap the adapter for EmbedPDF without changing the highlight layer,
 * side panel, or selection hook.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  ChunkSidePanel,
  HighlightLayer,
  useChunkSelection,
} from "../../../../_shared/pdf-viewer";
import type {
  ChunkLocation,
  ChunkLocationsResponse,
} from "../../../../_shared/pdf-viewer";

import { listDocumentChunks } from "../../api/client";

interface PdfChunkViewerProps {
  readonly documentId: string;
  readonly versionId: string;
  /** SHA-256 of the version's stored bytes — used as the hash gate. */
  readonly expectedHash: string;
  /** Pre-built blob URL for the raw bytes; the caller fetches /raw
   *  and creates the object URL so this component can re-render
   *  without re-downloading the PDF. */
  readonly pdfBlobUrl: string;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; response: ChunkLocationsResponse }
  | { kind: "hash_mismatch"; serverHash: string; expectedHash: string }
  | { kind: "no_rects"; parserVersion: string }
  | { kind: "error"; message: string };

const _PAGE_SCALE = 1.5;

export function PdfChunkViewer({
  documentId,
  versionId,
  expectedHash,
  pdfBlobUrl,
}: PdfChunkViewerProps) {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const selection = useChunkSelection();
  const pagesContainerRef = useRef<HTMLDivElement | null>(null);

  // ─── Fetch chunk-locations once per (document, version) ───────────────────
  useEffect(() => {
    const controller = new AbortController();
    setLoad({ kind: "loading" });
    listDocumentChunks(documentId, versionId, { signal: controller.signal })
      .then((raw) => {
        // The openapi-generated response shape is structurally identical
        // to the shared package's hand-written ``ChunkLocationsResponse``
        // (the OpenAPI snapshot test guards drift). Cast at this single
        // boundary so call sites downstream stay on the shared types.
        const response = raw as unknown as ChunkLocationsResponse;
        if (response.document_hash !== expectedHash) {
          setLoad({
            kind: "hash_mismatch",
            serverHash: response.document_hash,
            expectedHash,
          });
          return;
        }
        // Pre-0.2 parser shipped without rects; show the upgrade
        // tombstone so the reviewer knows why no highlights render.
        if (
          response.parser_version === "0.1" ||
          response.items.every((item) => item.rects.length === 0)
        ) {
          setLoad({ kind: "no_rects", parserVersion: response.parser_version });
          return;
        }
        setLoad({ kind: "ready", response });
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        const message = err instanceof Error ? err.message : String(err);
        setLoad({ kind: "error", message });
      });
    return () => controller.abort();
  }, [documentId, versionId, expectedHash]);

  const chunks = useMemo<ChunkLocation[]>(() => {
    return load.kind === "ready" ? load.response.items : [];
  }, [load]);

  // ─── pdfjs-dist render loop ───────────────────────────────────────────────
  // Render every page into its own canvas in document order. Each canvas
  // is wrapped so the overlay layer can sit absolute-positioned over it.
  const [pageCount, setPageCount] = useState<number>(0);
  useEffect(() => {
    if (load.kind !== "ready") return;
    let cancelled = false;
    let cleanupTask: (() => void) | undefined;

    // Lazy-load pdfjs-dist so the viewer bundle only pays for it when
    // a PDF tab is actually opened. The worker URL needs to resolve to
    // the matching version; we point to the package's worker entry.
    (async () => {
      const pdfjsLib = await import("pdfjs-dist");
      const workerUrl = (await import("pdfjs-dist/build/pdf.worker.min.mjs?url"))
        .default;
      // Side-effect: pdfjs-dist reads the worker src from this global.
      pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

      const loadingTask = pdfjsLib.getDocument(pdfBlobUrl);
      cleanupTask = () => loadingTask.destroy();
      const pdf = await loadingTask.promise;
      if (cancelled) {
        pdf.destroy();
        return;
      }
      setPageCount(pdf.numPages);

      const container = pagesContainerRef.current;
      if (!container) return;
      container.innerHTML = "";

      for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
        if (cancelled) break;
        const page = await pdf.getPage(pageNumber);
        const viewport = page.getViewport({ scale: _PAGE_SCALE });

        const pageWrap = document.createElement("div");
        pageWrap.className = "pdf-page-wrap";
        pageWrap.dataset.pageNumber = String(pageNumber);
        pageWrap.style.position = "relative";
        pageWrap.style.width = `${viewport.width}px`;
        pageWrap.style.height = `${viewport.height}px`;

        const canvas = document.createElement("canvas");
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.className = "pdf-page-canvas";

        pageWrap.appendChild(canvas);
        container.appendChild(pageWrap);

        const ctx = canvas.getContext("2d");
        if (ctx) {
          await page.render({ canvasContext: ctx, viewport }).promise;
        }
      }
    })().catch((err) => {
      if (cancelled) return;
      const message = err instanceof Error ? err.message : String(err);
      setLoad({ kind: "error", message });
    });

    return () => {
      cancelled = true;
      cleanupTask?.();
    };
  }, [load, pdfBlobUrl]);

  // ─── Scroll-to-selected-chunk effect ──────────────────────────────────────
  // Effect runs after layout so the freshly-rendered page wraps are
  // measurable. Scroll the matching page wrap into view and flash the
  // first rect of the selected chunk via a transient CSS class.
  useLayoutEffect(() => {
    if (!selection.selectedChunkId || load.kind !== "ready") return;
    const chunk = load.response.items.find(
      (c) => c.chunk_id === selection.selectedChunkId,
    );
    if (!chunk) return;
    const firstRect = chunk.rects[0];
    if (!firstRect) return;

    const wrap = pagesContainerRef.current?.querySelector(
      `[data-page-number="${firstRect.page}"]`,
    ) as HTMLDivElement | null;
    if (!wrap) return;

    // Approximate centre-of-rect scroll target.
    const top = wrap.offsetTop + firstRect.y * wrap.offsetHeight - 80;
    pagesContainerRef.current?.scrollTo({ top, behavior: "smooth" });
  }, [selection.selectedChunkId, load]);

  if (load.kind === "loading") {
    return <div className="pdf-viewer-loading">Loading PDF and chunk catalog…</div>;
  }
  if (load.kind === "hash_mismatch") {
    return (
      <div className="pdf-viewer-tombstone" role="alert">
        <strong>PDF hash mismatch.</strong>
        <p>
          The chunk catalog was computed against a different version of this PDF.
          Refusing to render highlights to avoid showing them at the wrong
          positions.
        </p>
        <dl>
          <dt>Expected (current version)</dt>
          <dd>
            <code>{load.expectedHash}</code>
          </dd>
          <dt>Returned by server</dt>
          <dd>
            <code>{load.serverHash}</code>
          </dd>
        </dl>
      </div>
    );
  }
  if (load.kind === "no_rects") {
    return (
      <div className="pdf-viewer-tombstone" role="status">
        <strong>Rect-level highlights unavailable for this version.</strong>
        <p>
          Parser version <code>{load.parserVersion}</code> shipped before
          line-level rects were emitted. Run{" "}
          <code>kw-rebackfill</code> on this document to upgrade — the parser
          will re-extract sections with overlay coordinates populated.
        </p>
      </div>
    );
  }
  if (load.kind === "error") {
    return (
      <div className="pdf-viewer-tombstone" role="alert">
        <strong>Could not load chunk catalog.</strong>
        <p>{load.message}</p>
      </div>
    );
  }

  return (
    <section className="pdf-chunk-viewer" aria-label="PDF chunk viewer">
      <div className="pdf-pages-pane">
        <div className="pdf-pages-scroll" ref={pagesContainerRef}>
          {/* Render hooks for the overlay layers — pdfjs canvases are
              injected as DOM siblings by the effect above. */}
          {Array.from({ length: pageCount }, (_, idx) => idx + 1).map((pageNumber) => (
            <OverlayPortal key={pageNumber} pageNumber={pageNumber}>
              <HighlightLayer
                pageNumber={pageNumber}
                chunks={chunks}
                selectedChunkId={selection.selectedChunkId}
                hoveredChunkId={selection.hoveredChunkId}
                onSelectChunk={selection.selectChunk}
                onHoverChunk={selection.hoverChunk}
              />
            </OverlayPortal>
          ))}
        </div>
      </div>
      <ChunkSidePanel
        chunks={chunks}
        selectedChunkId={selection.selectedChunkId}
        hoveredChunkId={selection.hoveredChunkId}
        onSelectChunk={selection.selectChunk}
        onHoverChunk={selection.hoverChunk}
      />
    </section>
  );
}

/**
 * Mount the overlay layer into the imperative ``.pdf-page-wrap`` node
 * the pdfjs render effect created, so the React-managed overlay sits
 * inside the same offsetParent as the canvas without React owning the
 * canvas node directly.
 */
function OverlayPortal({
  pageNumber,
  children,
}: {
  pageNumber: number;
  children: React.ReactNode;
}) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    const candidate = document.querySelector(
      `[data-page-number="${pageNumber}"]`,
    ) as HTMLElement | null;
    setHost(candidate);
  }, [pageNumber]);
  if (!host) return null;
  return createPortal(<>{children}</>, host);
}
