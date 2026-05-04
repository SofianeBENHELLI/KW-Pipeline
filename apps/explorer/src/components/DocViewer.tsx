/**
 * Document viewer pane — renders the original source as a stack of
 * "page cards" with paragraph rows. The active chunk is highlighted
 * with a warm-orange bracket on the matching paragraphs and a small
 * chunk-id tag.
 *
 * Port of the design's `panels.jsx::DocViewer`. Two upgrades over
 * the prototype:
 *
 *   * The header carries a chunk navigator (prev/next) when a chunk
 *     is highlighted — the design dropped this on the floor when no
 *     content was available; we keep the affordance because the
 *     production data may include pre-/post- chunks.
 *   * When `DOC_CONTENT[doc.id]` is missing, we fall back to a small
 *     skeleton stub identical to the design's `kx-viewer-stub`.
 */

import React from "react";

import {
  DOC_TYPES,
  type DocContent,
  type ExplorerChunk,
  type ExplorerDocument,
  type ExplorerSnapshot,
  chunkById,
} from "../state/explorer-data";
import { Icon, NAVY2 } from "./icons";

interface DocViewerProps {
  snapshot: ExplorerSnapshot;
  doc: ExplorerDocument | null;
  highlightChunkId: string | null;
  onPrevChunk: () => void;
  onNextChunk: () => void;
}

export const DocViewer: React.FC<DocViewerProps> = ({
  snapshot,
  doc,
  highlightChunkId,
  onPrevChunk,
  onNextChunk,
}) => {
  if (!doc) {
    return (
      <div className="kx-viewer-empty">
        <Icon name="doc" size={28} stroke={NAVY2} />
        <div className="kx-viewer-empty-t">No document open</div>
        <div className="kx-viewer-empty-s">
          Click a document or chunk in the graph to open the original source here.
        </div>
      </div>
    );
  }

  const content: DocContent | undefined = snapshot.docContent[doc.id];
  const dt = DOC_TYPES[doc.type];
  const anchor = highlightChunkId && content ? content.chunkAnchors[highlightChunkId] : null;
  const chunk: ExplorerChunk | null = highlightChunkId ? chunkById(snapshot, highlightChunkId) ?? null : null;

  const ref = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    if (!ref.current) return;
    const el = ref.current.querySelector(".kx-para.kx-hl") as HTMLElement | null;
    if (el) {
      const c = ref.current;
      const top = el.offsetTop - 80;
      c.scrollTo({ top, behavior: "smooth" });
    }
  }, [highlightChunkId, doc.id]);

  return (
    <div className="kx-viewer">
      <div className="kx-viewer-head">
        <div className="kx-viewer-meta">
          <span className="kx-doc-chip" style={{ background: dt?.color ?? "#888" }}>
            {dt?.short ?? "DOC"}
          </span>
          <div>
            <div className="kx-viewer-title">{doc.title}</div>
            <div className="kx-viewer-sub">
              {doc.source} · {doc.date} · {doc.chunks} chunks
            </div>
          </div>
        </div>
        {chunk && (
          <div className="kx-viewer-chunknav">
            <button onClick={onPrevChunk} title="Previous chunk" aria-label="Previous chunk">
              <Icon name="chevron-up" size={14} />
            </button>
            <div className="kx-chunkloc">
              <span className="kx-chunkloc-l">CHUNK</span>
              <span className="kx-chunkloc-v">{chunk.id}</span>
              <span className="kx-chunkloc-l">PAGE</span>
              <span className="kx-chunkloc-v">{anchor?.page ?? chunk.page}</span>
            </div>
            <button onClick={onNextChunk} title="Next chunk" aria-label="Next chunk">
              <Icon name="chevron-down" size={14} />
            </button>
          </div>
        )}
      </div>
      <div className="kx-viewer-body" ref={ref}>
        {content ? (
          content.pages.map((page) => (
            <div key={page.n} className="kx-page">
              <div className="kx-page-tab">
                <Icon name="page" size={11} stroke={NAVY2} />
                <span>p. {page.n}</span>
              </div>
              <div className="kx-page-h">{page.heading}</div>
              {page.paras.map((p, i) => {
                const hl = anchor && anchor.page === page.n && anchor.paras.includes(i);
                return (
                  <div key={i} className={"kx-para" + (hl ? " kx-hl" : "")}>
                    {hl && chunk && <span className="kx-hl-tag">{chunk.id}</span>}
                    {p}
                  </div>
                );
              })}
            </div>
          ))
        ) : (
          <div className="kx-viewer-stub">
            <div className="kx-stub-mark">[no preview content]</div>
            <p>
              The original source for this document is stored in {doc.source}. A preview will be rendered here in
              production.
            </p>
            <div className="kx-stub-skel">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} style={{ width: `${60 + (i * 7) % 40}%` }} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
