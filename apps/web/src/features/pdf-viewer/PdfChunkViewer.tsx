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
  /** When `true`, the viewer renders the PDF + overlays only — the
   *  built-in :class:`ChunkSidePanel` is suppressed. Set this when the
   *  consumer already has its own chunk / topic / entity navigation
   *  next to the viewer (e.g. the Knowledge Forge LinkedView's right
   *  pane) so the operator doesn't see two side panels side-by-side. */
  readonly hideBuiltInSidePanel?: boolean;
  /** External multi-chunk hover set (e.g. the Knowledge Forge
   *  LinkedView hovering a Topic that owns N chunks). When provided,
   *  every chunk whose id is in the set renders with the hover
   *  visual, in addition to whichever single rect the operator's
   *  pointer is over. ``null`` / ``undefined`` disables the override
   *  and the viewer falls back to its internal singleton hover. */
  readonly externalHoveredChunkIds?: ReadonlySet<string> | null;
  /** Symmetric multi-chunk selection set. Reserved for parity with
   *  ``externalHoveredChunkIds``; today's LinkedView uses hover only
   *  but the API is in place for a future click-to-pin Topic. */
  readonly externalSelectedChunkIds?: ReadonlySet<string> | null;
  /** Fires every time the rect-level hover changes — null on
   *  pointer-leave. Lets the consumer reflect "PDF → right pane"
   *  cross-highlight: hovering a rect lights up its parent Topic /
   *  Entity card on the right side. */
  readonly onHoverChunk?: (chunkId: string | null) => void;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; response: ChunkLocationsResponse }
  | { kind: "hash_mismatch"; serverHash: string; expectedHash: string }
  | { kind: "no_rects"; parserVersion: string }
  | { kind: "error"; message: string };

// Upper bound on the render scale — caps how much pdfjs upscales tiny
// pages on wide screens. The lower bound is whatever fits the
// container width without horizontal overflow; see
// ``_scaleForContainer`` below.
const _MAX_PAGE_SCALE = 1.5;

// Padding to subtract from the container width before computing the
// page scale so the rendered page doesn't kiss the pane border (matches
// the ``.pdf-pages-scroll`` 16px padding on each side).
const _PAGE_HORIZONTAL_PADDING = 32;

/**
 * Pick a render scale that fits the page width into the available
 * container width without overflow, never exceeding ``_MAX_PAGE_SCALE``.
 *
 * Called once per (page, container width) — the parent effect re-runs
 * when the container resizes (via :class:`ResizeObserver`) so the
 * pages re-render at the new scale.
 */
function _scaleForContainer(pageWidth: number, containerWidth: number): number {
  if (containerWidth <= 0 || pageWidth <= 0) return _MAX_PAGE_SCALE;
  const usable = Math.max(containerWidth - _PAGE_HORIZONTAL_PADDING, 200);
  return Math.min(_MAX_PAGE_SCALE, usable / pageWidth);
}

export function PdfChunkViewer({
  documentId,
  versionId,
  expectedHash,
  pdfBlobUrl,
  hideBuiltInSidePanel = false,
  externalHoveredChunkIds = null,
  externalSelectedChunkIds = null,
  onHoverChunk,
}: PdfChunkViewerProps) {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const selection = useChunkSelection();
  const pagesContainerRef = useRef<HTMLDivElement | null>(null);
  // Tracked here (not below the render effect) so the effect's dep
  // array can reference it without a TDZ — the bucketing logic that
  // updates this value lives in the ``ResizeObserver`` effect after
  // the render loop, but the declaration must precede every read.
  const [containerWidth, setContainerWidth] = useState(0);

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

      // Compute scale once per render pass from the container's current
      // client width. ``ResizeObserver`` (below) bumps ``containerWidth``
      // when the host pane resizes, which retriggers this effect and
      // re-renders every page at the new scale. Devicescale (DPR) lifts
      // the canvas backing-store resolution so the page stays crisp on
      // retina displays without inflating CSS dimensions.
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
  }, [load, pdfBlobUrl, containerWidth]);

  // ``containerWidth`` is declared above the render effect (TDZ-safe);
  // this effect's job is to keep it in sync with the live pane width.
  // ``ResizeObserver`` fires on mount and whenever the host pane
  // resizes (split-pane drag, window resize, sidebar collapse, etc.);
  // we round to 16-px buckets so a few px of layout jitter doesn't
  // churn the heavy pdfjs render loop.
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
    <section
      className={
        hideBuiltInSidePanel
          ? "pdf-chunk-viewer is-solo"
          : "pdf-chunk-viewer"
      }
      aria-label="PDF chunk viewer"
    >
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
                hoveredChunkIds={externalHoveredChunkIds}
                selectedChunkIds={externalSelectedChunkIds}
                onSelectChunk={selection.selectChunk}
                onHoverChunk={(id) => {
                  // Internal hover state still drives the singleton
                  // tooltip / focus visuals; the external callback
                  // (when present) propagates to the right-pane
                  // cross-highlight so hovering a rect lights up its
                  // Topic / Entity card.
                  selection.hoverChunk(id);
                  onHoverChunk?.(id);
                }}
              />
            </OverlayPortal>
          ))}
        </div>
      </div>
      {hideBuiltInSidePanel ? null : (
        <ChunkSidePanel
          chunks={chunks}
          selectedChunkId={selection.selectedChunkId}
          hoveredChunkId={selection.hoveredChunkId}
          onSelectChunk={selection.selectChunk}
          onHoverChunk={selection.hoverChunk}
        />
      )}
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
