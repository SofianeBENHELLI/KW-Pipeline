/**
 * Explorer-side PDF chunk viewer.
 *
 * Renders the original PDF with the shared rect-overlay primitives
 * (``apps/_shared/pdf-viewer``) and a per-document chunk side panel.
 * Wired into Explorer's :class:`DocViewer` whenever the open document
 * is a real backend PDF (has a concrete ``version.id`` + ``sha256``
 * pair); synthetic samples and non-PDF formats keep the existing
 * paragraph-card fallback.
 *
 * Bundler note: Explorer is webpack-built, so the pdfjs worker URL is
 * resolved via ``new URL("...", import.meta.url)`` — Orbital uses
 * Vite's ``?url`` suffix for the same purpose. The two adapters are
 * the only bundler-specific pieces; everything else lives in the
 * shared module.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  ChunkSidePanel,
  HighlightLayer,
  useChunkSelection,
} from "../../../_shared/pdf-viewer";
import type {
  ChunkLocation,
  ChunkLocationsResponse,
} from "../../../_shared/pdf-viewer";
import { listDocumentChunks, rawFileUrl } from "../api/client";

interface PdfChunkViewerProps {
  readonly documentId: string;
  readonly versionId: string;
  /** SHA-256 of the version's stored bytes — used as the hash gate. */
  readonly expectedHash: string;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; response: ChunkLocationsResponse; pdfBlobUrl: string }
  | { kind: "hash_mismatch"; serverHash: string; expectedHash: string }
  | { kind: "no_rects"; parserVersion: string }
  | { kind: "error"; message: string };

// Upper bound on the render scale — the lower bound is whatever fits
// the container width without horizontal overflow; see
// :func:`_scaleForContainer`. Same shape as Orbital's
// ``apps/web/src/features/pdf-viewer/PdfChunkViewer.tsx`` so the two
// adapters render at matching dimensions.
const _MAX_PAGE_SCALE = 1.5;
const _PAGE_HORIZONTAL_PADDING = 32;

function _scaleForContainer(pageWidth: number, containerWidth: number): number {
  if (containerWidth <= 0 || pageWidth <= 0) return _MAX_PAGE_SCALE;
  const usable = Math.max(containerWidth - _PAGE_HORIZONTAL_PADDING, 200);
  return Math.min(_MAX_PAGE_SCALE, usable / pageWidth);
}

export function PdfChunkViewer({
  documentId,
  versionId,
  expectedHash,
}: PdfChunkViewerProps) {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const selection = useChunkSelection();
  const pagesContainerRef = useRef<HTMLDivElement | null>(null);
  // Declared up-front (above the render effect that depends on it) so
  // TS doesn't flag a TDZ — the ``ResizeObserver`` effect below keeps
  // it in sync with the live container width.
  const [containerWidth, setContainerWidth] = useState(0);

  // ─── Fetch chunk-locations + the original PDF bytes in parallel ──────────
  useEffect(() => {
    const controller = new AbortController();
    setLoad({ kind: "loading" });
    let createdUrl: string | null = null;

    const chunksPromise = listDocumentChunks(documentId, versionId, {
      signal: controller.signal,
    });
    // Match the rest of Explorer's API client: no ``credentials:
    // "include"``. The backend's CORS config returns the origin
    // allowlist but not ``Access-Control-Allow-Credentials``, so a
    // credentialed fetch fails the browser's CORS gate with a
    // ``Failed to fetch`` that has no usable status code. Auth (#83)
    // will flip every fetch over together.
    const bytesPromise = fetch(rawFileUrl(documentId, versionId), {
      signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) {
        throw new Error(`Failed to load PDF bytes (HTTP ${response.status}).`);
      }
      const blob = await response.blob();
      createdUrl = URL.createObjectURL(blob);
      return createdUrl;
    });

    Promise.all([chunksPromise, bytesPromise])
      .then(([response, pdfBlobUrl]) => {
        if (response.document_hash !== expectedHash) {
          setLoad({
            kind: "hash_mismatch",
            serverHash: response.document_hash,
            expectedHash,
          });
          return;
        }
        if (
          response.parser_version === "0.1" ||
          response.items.every((item) => item.rects.length === 0)
        ) {
          setLoad({ kind: "no_rects", parserVersion: response.parser_version });
          return;
        }
        setLoad({ kind: "ready", response, pdfBlobUrl });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const message = err instanceof Error ? err.message : String(err);
        setLoad({ kind: "error", message });
      });

    return () => {
      controller.abort();
      if (createdUrl !== null) URL.revokeObjectURL(createdUrl);
    };
  }, [documentId, versionId, expectedHash]);

  const chunks = useMemo<ChunkLocation[]>(() => {
    return load.kind === "ready" ? load.response.items : [];
  }, [load]);

  // ─── pdfjs-dist render loop (webpack worker URL) ──────────────────────────
  const [pageCount, setPageCount] = useState<number>(0);
  useEffect(() => {
    if (load.kind !== "ready") return;
    let cancelled = false;
    let cleanupTask: (() => void) | undefined;

    (async () => {
      const pdfjsLib = await import("pdfjs-dist");
      // Webpack 5 resolves this expression at build time and emits a
      // companion asset URL — the parallel Vite ``?url`` import sits
      // in apps/web's PdfChunkViewer. Both produce a string URL the
      // pdfjs worker thread can fetch.
      const workerUrl = new URL(
        "pdfjs-dist/build/pdf.worker.min.mjs",
        import.meta.url,
      ).toString();
      pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

      const loadingTask = pdfjsLib.getDocument(load.pdfBlobUrl);
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

      const dpr = Math.min(window.devicePixelRatio || 1, 2);

      for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
        if (cancelled) break;
        const page = await pdf.getPage(pageNumber);
        const nativeViewport = page.getViewport({ scale: 1 });
        const scale = _scaleForContainer(nativeViewport.width, containerWidth);
        const viewport = page.getViewport({ scale });

        const pageWrap = document.createElement("div");
        pageWrap.className = "pdf-page-wrap";
        pageWrap.dataset.pageNumber = String(pageNumber);
        pageWrap.style.position = "relative";
        pageWrap.style.width = `${viewport.width}px`;
        pageWrap.style.height = `${viewport.height}px`;

        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width * dpr);
        canvas.height = Math.floor(viewport.height * dpr);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        canvas.className = "pdf-page-canvas";

        pageWrap.appendChild(canvas);
        container.appendChild(pageWrap);

        const ctx = canvas.getContext("2d");
        if (ctx) {
          const transform = dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined;
          await page.render({ canvasContext: ctx, viewport, transform }).promise;
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
  }, [load, containerWidth]);

  // ``containerWidth`` is declared up at the top of the component
  // (TDZ-safe for the render effect's dep array); this effect keeps it
  // in sync with the live container width. ``ResizeObserver`` fires on
  // mount and whenever the host pane resizes; rounding to 16-px buckets
  // keeps small layout jitter from churning the pdfjs render loop.
  useLayoutEffect(() => {
    const el = pagesContainerRef.current;
    if (!el) return;
    const apply = (raw: number) => {
      const bucketed = Math.max(0, Math.floor(raw / 16) * 16);
      setContainerWidth((prev) => (prev === bucketed ? prev : bucketed));
    };
    apply(el.clientWidth);
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) apply(entry.contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // ─── Scroll-to-selected-chunk effect ──────────────────────────────────────
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
 * the pdfjs render effect created. Same pattern Orbital uses; the
 * canvas is owned by pdfjs but the overlay is React-managed.
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
