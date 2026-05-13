/**
 * Drop-in panel for the Orbital ReviewWorkspace that lazily fetches
 * the raw PDF bytes, materialises a blob URL, and hands it to the
 * :class:`PdfChunkViewer`.
 *
 * Lifecycle:
 * - Mount: ``fetch('/documents/{id}/versions/{v}/raw')`` → blob → URL
 * - Unmount: ``URL.revokeObjectURL`` so the browser releases the bytes
 * - Re-render: only re-fetches when ``(documentId, versionId)`` change
 *
 * The viewer itself is React.lazy'd because pdfjs-dist + its worker
 * are heavy and most reviewer sessions never open a PDF tab.
 */

import { Suspense, lazy, useEffect, useState } from "react";

import { getApiBaseUrl } from "../../api/client";

const _PdfChunkViewer = lazy(() =>
  import("./PdfChunkViewer").then((mod) => ({ default: mod.PdfChunkViewer })),
);

interface PdfViewerPanelProps {
  readonly documentId: string;
  readonly versionId: string;
  readonly expectedHash: string;
  /** Forwarded to :class:`PdfChunkViewer`; suppresses the built-in
   *  side panel when the consumer already has its own chunk
   *  navigation alongside the viewer. */
  readonly hideBuiltInSidePanel?: boolean;
}

type BlobState =
  | { kind: "loading" }
  | { kind: "ready"; url: string }
  | { kind: "error"; message: string };

export function PdfViewerPanel({
  documentId,
  versionId,
  expectedHash,
  hideBuiltInSidePanel = false,
}: PdfViewerPanelProps) {
  const [state, setState] = useState<BlobState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    let createdUrl: string | null = null;

    const baseUrl = getApiBaseUrl().replace(/\/$/, "");
    fetch(
      `${baseUrl}/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/raw`,
      { signal: controller.signal, credentials: "include" },
    )
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(
            `Failed to load PDF bytes (HTTP ${response.status}).`,
          );
        }
        const blob = await response.blob();
        createdUrl = URL.createObjectURL(blob);
        setState({ kind: "ready", url: createdUrl });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const message =
          err instanceof Error ? err.message : "Failed to load PDF bytes.";
        setState({ kind: "error", message });
      });

    return () => {
      controller.abort();
      if (createdUrl !== null) URL.revokeObjectURL(createdUrl);
    };
  }, [documentId, versionId]);

  if (state.kind === "loading") {
    return <p className="muted" role="status">Loading PDF…</p>;
  }
  if (state.kind === "error") {
    return (
      <div className="notice danger" role="alert">
        <strong>PDF failed to load.</strong>
        <span>{state.message}</span>
      </div>
    );
  }

  return (
    <Suspense
      fallback={<p className="muted" role="status">Loading viewer…</p>}
    >
      <_PdfChunkViewer
        documentId={documentId}
        versionId={versionId}
        expectedHash={expectedHash}
        pdfBlobUrl={state.url}
        hideBuiltInSidePanel={hideBuiltInSidePanel}
      />
    </Suspense>
  );
}
