/**
 * LinkedView — flagship of the Knowledge Forge redesign.
 *
 * Two scrollable panes side-by-side:
 *
 *   Left  (1.15fr): document viewer (page card with `<LvSpan>` chunks).
 *   Right (1fr):    Topics / Entities / Chunks card stack.
 *
 * Bidirectional cross-highlight on hover:
 *   - hovering a topic/entity highlights its source chunks in the doc
 *   - hovering a chunk in the doc highlights its parent topic + entities
 *
 * The hover state is component-local (`useState`) — never lifted to a
 * global store. The design handoff §14 calls this out explicitly:
 * "implement it as `hover: {kind, id} | null` in component-local state,
 * not via a global store (avoid re-render storms on hover)".
 */

import { useCallback, useMemo, useState } from "react";
import type { ReactElement } from "react";

import { Btn, OrbI } from "../index";
import {
  useLinkedObjects,
  type LinkedChunk,
  type LinkedEntity,
  type LinkedObjects,
  type LinkedTopic,
} from "../hooks/useLinkedObjects";
import { PdfViewerPanel } from "../../features/pdf-viewer";
import { ResizeHandle } from "./ResizeHandle";
import { useResizable } from "./useResizable";

export type ObjKind = "Topics" | "Entities" | "Chunks";

// Inner split between the document viewer (left) and the knowledge-
// objects rail (right). Value is the document viewer's width in px;
// the right column takes whatever's left over via ``1fr``. Pixels
// rather than percent so the drag-handle delta maps 1:1 to client X
// without needing the container's live width.
const _DOC_WIDTH_KEY = "kf:review:linked-doc-width";
const _DOC_WIDTH_MIN = 360;
const _DOC_WIDTH_MAX = 1400;
const _DOC_WIDTH_DEFAULT = 720;

// Coverage view: persisted operator preference. When on, the PDF
// viewer paints non-extracted page area red and extracted rects
// green so parser blind spots are visible at a glance.
const _COVERAGE_KEY = "kf:review:coverage-mode";

function _readCoverageStored(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(_COVERAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function _writeCoverageStored(value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(_COVERAGE_KEY, value ? "true" : "false");
  } catch {
    // best-effort
  }
}

// Module-level frozen empty set so ``useMemo`` returns a stable
// reference when no chunks are highlit — keeps the
// ``<HighlightLayer>`` props from churning on every render.
const _EMPTY_SET: ReadonlySet<string> = new Set();

/**
 * PDF metadata required to render the actual document bytes in the
 * left pane instead of the markdown-style extraction. Derived in
 * :class:`ReviewWorkspace` from ``activeDoc`` and passed down only
 * when the active document is a PDF with a populated SHA-256 — every
 * other content type keeps the existing per-section text article so
 * non-PDF formats (docx/pptx/text/wiki) are not regressed.
 *
 * Cross-highlight between the right pane (Topics / Entities / Chunks)
 * and the PDF rect overlays is intentionally not wired in this slice
 * — the shared :mod:`apps/_shared/pdf-viewer` overlay is singleton-
 * keyed and the right pane lights up sets of chunks, so bridging the
 * two needs a follow-up that extends the overlay's selection API.
 */
export interface LinkedViewPdf {
  readonly versionId: string;
  readonly expectedHash: string;
}

interface Hover {
  kind: ObjKind;
  id: string;
}

export interface LinkedViewProps {
  /** Document id to fetch the projection for. */
  documentId: string | null;
  /** Document filename (rendered in the viewer header). */
  filename?: string;
  /** When set, the left pane renders the actual PDF bytes via
   *  :class:`PdfViewerPanel` instead of the per-section text article.
   *  Driven by ``ReviewWorkspace`` from the active document's latest
   *  version content type + SHA-256. */
  pdf?: LinkedViewPdf | null;
  /** Optional fixture override — used by tests to skip the network. */
  fixture?: LinkedObjects;
  /** Loading override (lets tests force the loading branch). */
  loading?: boolean;
  /** Deep-link target: chunk id to highlight + scroll into view on
   *  mount. Sourced from the ``?chunk=`` URL param when a chat citation
   *  lands here (#447 follow-up). */
  initialChunkId?: string | null;
}

export function LinkedView({
  documentId,
  filename,
  pdf,
  fixture,
  loading,
  initialChunkId,
}: LinkedViewProps): ReactElement {
  const live = useLinkedObjects(fixture ? null : documentId);
  const data = fixture ?? live.data;
  const isLoading = loading ?? (!fixture && live.status === "loading");
  const isError = !fixture && live.status === "error";
  // Show the empty-state panel whenever there are no chunks to render,
  // regardless of whether the data came from the live fetch or the
  // fixture override. PR 4 will let the user kick off the projection.
  const isEmpty = !isLoading && !isError && data.chunks.length === 0;

  const [objKind, setObjKind] = useState<ObjKind>("Topics");
  const [hover, setHover] = useState<Hover | null>(null);

  // Doc / objects split — drag-resizable, persisted via localStorage.
  const docResize = useResizable({
    initial: _DOC_WIDTH_DEFAULT,
    min: _DOC_WIDTH_MIN,
    max: _DOC_WIDTH_MAX,
    storageKey: _DOC_WIDTH_KEY,
  });
  const linkedStyle = {
    "--kf-lv-doc-w": `${docResize.value}px`,
  } as React.CSSProperties;

  // Coverage-view toggle. Persisted across reloads so an operator
  // running an audit pass doesn't have to flip it every doc.
  const [coverageMode, setCoverageMode] = useState<boolean>(
    _readCoverageStored,
  );
  const toggleCoverage = useCallback(() => {
    setCoverageMode((prev) => {
      const next = !prev;
      _writeCoverageStored(next);
      return next;
    });
  }, []);

  const isChunkHighlit = (chunkId: string): boolean => {
    if (!hover) return false;
    if (hover.kind === "Chunks") return hover.id === chunkId;
    if (hover.kind === "Topics") {
      return data.topicToChunks.get(hover.id)?.has(chunkId) === true;
    }
    if (hover.kind === "Entities") {
      return data.entityToChunks.get(hover.id)?.has(chunkId) === true;
    }
    return false;
  };

  // Set of chunk ids that should highlight in the PDF overlay for the
  // current hover. Recomputed on every hover transition; the
  // ``isChunkHighlit`` function above is the row-by-row equivalent for
  // the text view, while this is the set the PDF view's
  // ``<HighlightLayer>`` needs to light up many rects at once when
  // a single Topic / Entity is hovered.
  const highlightedChunkIds = useMemo<ReadonlySet<string>>(() => {
    if (!hover) return _EMPTY_SET;
    if (hover.kind === "Chunks") return new Set([hover.id]);
    if (hover.kind === "Topics") {
      return data.topicToChunks.get(hover.id) ?? _EMPTY_SET;
    }
    if (hover.kind === "Entities") {
      return data.entityToChunks.get(hover.id) ?? _EMPTY_SET;
    }
    return _EMPTY_SET;
  }, [hover, data]);

  // Deep-link selection set. Wrap the URL-supplied ``initialChunkId``
  // in a singleton set so the shared viewer's selection prop sees a
  // stable reference and the scroll-to-rect effect lands on the cited
  // chunk on mount. ``null`` falls back to the frozen empty set so the
  // PDF panel doesn't see prop churn when no deep link is active.
  const deepLinkSelectedChunkIds = useMemo<ReadonlySet<string>>(() => {
    if (!initialChunkId) return _EMPTY_SET;
    return new Set([initialChunkId]);
  }, [initialChunkId]);

  // Bridge "PDF rect → right-pane card" highlight. The shared viewer
  // hands us the chunk id under the cursor (or ``null`` on
  // pointer-leave); we lift that into the same hover state the text
  // view uses, so the existing ``isObjHighlit`` logic lights up the
  // chunk's parent Topic + Entities on the right without per-mode
  // branching.
  const handlePdfHoverChunk = useCallback(
    (chunkId: string | null) => {
      setHover(chunkId ? { kind: "Chunks", id: chunkId } : null);
    },
    [setHover],
  );

  const isObjHighlit = (kind: ObjKind, id: string): boolean => {
    if (!hover) return false;
    if (hover.kind === kind && hover.id === id) return true;
    if (hover.kind === "Chunks") {
      if (kind === "Topics") return data.chunkToTopic.get(hover.id) === id;
      if (kind === "Entities")
        return data.chunkToEntities.get(hover.id)?.has(id) === true;
    }
    return false;
  };

  if (isError) {
    return (
      <section className="kf-lv kf-lv--state" data-testid="kf-linked-error">
        <div className="kf-lv__state">
          <h3>Couldn&apos;t load the linked objects</h3>
          <p>
            The graph projection for this document is unavailable.{" "}
            {live.error?.message ? <code>{live.error.message}</code> : null}
          </p>
          <Btn xs icon={OrbI.refresh} onClick={live.refetch}>
            Retry
          </Btn>
        </div>
      </section>
    );
  }

  if (isLoading) {
    return (
      <section className="kf-lv kf-lv--state" data-testid="kf-linked-loading">
        <div className="kf-lv__state">
          <h3>Loading linked objects…</h3>
          <p>Pulling the chunk / topic / entity projection.</p>
        </div>
      </section>
    );
  }

  // For non-PDF documents, "empty projection" short-circuits the whole
  // view — the left pane has nothing to render without sections /
  // chunks. PDFs render the actual bytes regardless of projection
  // state, so the empty-state card is only useful as a right-pane
  // hint, not a full-view replacement.
  if (isEmpty && !pdf) {
    return (
      <section className="kf-lv kf-lv--state" data-testid="kf-linked-empty">
        <div className="kf-lv__state">
          <h3>No linked objects yet</h3>
          <p>
            This document has not been semantically projected. Validate
            it on the Review tab to unlock the Linked View.
          </p>
        </div>
      </section>
    );
  }

  const items: Array<LinkedTopic | LinkedEntity | LinkedChunk> =
    objKind === "Topics"
      ? data.topics
      : objKind === "Entities"
        ? data.entities
        : data.chunks;

  return (
    <section className="kf-lv" aria-label="Linked view" style={linkedStyle}>
      {/* ── Document viewer (left) ─────────────────────────────── */}
      <div className="kf-lv__pane kf-lv__pane--doc">
        <div className="kf-lv__pane-h">
          <span className="orb-section-h">Document viewer</span>
          <span className="orb-mono kf-lv__hint">
            {filename ?? "document"}
            {pdf ? (
              <> · PDF · hash <code>{pdf.expectedHash.slice(0, 12)}…</code></>
            ) : (
              <>
                {" "}
                · {data.sections.length} section
                {data.sections.length === 1 ? "" : "s"} ·{" "}
                {data.chunks.length} chunks
              </>
            )}
          </span>
          {pdf ? (
            <label
              className="kf-lv__toggle"
              title="Paint extracted rects green, non-extracted areas red"
              data-testid="kf-lv-coverage-toggle"
            >
              <input
                type="checkbox"
                checked={coverageMode}
                onChange={toggleCoverage}
                aria-label="Show extraction coverage"
              />
              <span>Coverage</span>
            </label>
          ) : null}
        </div>
        <div
          className="kf-lv__paper orb-scroll"
          data-testid={pdf ? "kf-lv-pdf" : "kf-lv-text"}
        >
          {pdf && documentId ? (
            <PdfViewerPanel
              documentId={documentId}
              versionId={pdf.versionId}
              expectedHash={pdf.expectedHash}
              hideBuiltInSidePanel
              externalHoveredChunkIds={highlightedChunkIds}
              externalSelectedChunkIds={
                deepLinkSelectedChunkIds.size > 0
                  ? deepLinkSelectedChunkIds
                  : null
              }
              onHoverChunk={handlePdfHoverChunk}
              coverageMode={coverageMode}
            />
          ) : (
            <article className="kf-lv__page">
              <h2 className="kf-lv__page-h1">{filename ?? "Document"}</h2>
              {data.sections.map((section) => {
                const chunksInSection = section.chunkIds
                  .map((id) => data.chunks.find((c) => c.id === id))
                  .filter((c): c is LinkedChunk => Boolean(c));
                return (
                  <section
                    key={section.id || "untitled"}
                    className="kf-lv__section"
                    data-testid={`kf-lv-section-${section.id || "untitled"}`}
                  >
                    {section.heading && (
                      <h3 className="kf-lv__section-h">{section.heading}</h3>
                    )}
                    {section.page != null && (
                      <div className="kf-lv__section-page orb-mono">
                        page {section.page}
                      </div>
                    )}
                    <div className="kf-lv__page-body">
                      {chunksInSection.map((c) => (
                        <LvSpan
                          key={c.id}
                          chunk={c}
                          highlit={isChunkHighlit(c.id)}
                          onHover={(h) => setHover(h)}
                        />
                      ))}
                    </div>
                  </section>
                );
              })}
            </article>
          )}
        </div>
      </div>

      {/* ── Resize handle between doc viewer and objects rail ──── */}
      <ResizeHandle
        label="Resize document viewer"
        onPointerDown={docResize.onPointerDown}
        isDragging={docResize.isDragging}
      />

      {/* ── Knowledge objects (right) ─────────────────────────── */}
      <div className="kf-lv__pane kf-lv__pane--objs">
        <div className="kf-lv__pane-h">
          <span className="orb-section-h">Knowledge objects</span>
          <ObjKindTabs
            kind={objKind}
            counts={{
              Topics: data.topics.length,
              Entities: data.entities.length,
              Chunks: data.chunks.length,
            }}
            onChange={(k) => {
              setObjKind(k);
              setHover(null);
            }}
          />
        </div>

        <div
          className="kf-lv__objlist orb-scroll"
          role="group"
          aria-label="Knowledge object cards"
        >
          {items.length === 0 && (
            <div className="kf-lv__obj-empty">
              <p>No {objKind.toLowerCase()} extracted from this document.</p>
            </div>
          )}
          {objKind === "Topics" &&
            data.topics.map((t) => (
              <TopicCard
                key={t.id}
                topic={t}
                highlit={isObjHighlit("Topics", t.id)}
                onHover={(h) => setHover(h)}
              />
            ))}
          {objKind === "Entities" &&
            data.entities.map((e) => (
              <EntityCard
                key={e.id}
                entity={e}
                highlit={isObjHighlit("Entities", e.id)}
                onHover={(h) => setHover(h)}
              />
            ))}
          {objKind === "Chunks" &&
            data.chunks.map((c) => (
              <ChunkCard
                key={c.id}
                chunk={c}
                topicLabel={
                  c.topicId
                    ? data.topics.find((t) => t.id === c.topicId)?.label ?? null
                    : null
                }
                highlit={isObjHighlit("Chunks", c.id)}
                onHover={(h) => setHover(h)}
              />
            ))}
        </div>
        <div className="kf-lv__foot orb-mono" aria-live="polite">
          {hover ? (
            <>
              ● cross-highlighting{" "}
              <b>
                {hover.kind.slice(0, -1)}/{hover.id}
              </b>{" "}
              ↔ document
            </>
          ) : (
            <>hover an object to highlight its source span(s)</>
          )}
        </div>
      </div>
    </section>
  );
}

/* ── Sub-components ──────────────────────────────────────────── */

function ObjKindTabs({
  kind,
  counts,
  onChange,
}: {
  kind: ObjKind;
  counts: Record<ObjKind, number>;
  onChange: (k: ObjKind) => void;
}): ReactElement {
  const options: ObjKind[] = ["Topics", "Entities", "Chunks"];
  return (
    <div className="kf-lv__objtabs" role="tablist" aria-label="Object kind">
      {options.map((k) => (
        <button
          key={k}
          type="button"
          role="tab"
          aria-selected={kind === k}
          className={`kf-lv__objtab ${kind === k ? "is-on" : ""}`}
          onClick={() => onChange(k)}
        >
          {k}
          <span className="kf-lv__objtab-n orb-mono">{counts[k]}</span>
        </button>
      ))}
    </div>
  );
}

function LvSpan({
  chunk,
  highlit,
  onHover,
}: {
  chunk: LinkedChunk;
  highlit: boolean;
  onHover: (h: Hover | null) => void;
}): ReactElement {
  return (
    <span
      role="button"
      aria-pressed={highlit}
      className={`kf-lv__span ${highlit ? "is-hl" : ""}`}
      data-cid={chunk.id}
      data-testid={`kf-lv-span-${chunk.id}`}
      onMouseEnter={() => onHover({ kind: "Chunks", id: chunk.id })}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover({ kind: "Chunks", id: chunk.id })}
      onBlur={() => onHover(null)}
      onKeyDown={(e) => {
        if (e.key === "Escape") onHover(null);
      }}
      tabIndex={0}
    >
      {chunk.text}
      {" "}
    </span>
  );
}

function TopicCard({
  topic,
  highlit,
  onHover,
}: {
  topic: LinkedTopic;
  highlit: boolean;
  onHover: (h: Hover | null) => void;
}): ReactElement {
  return (
    <div
      role="button"
      className={`kf-lv__obj kf-lv__obj--topics ${highlit ? "is-hl" : ""}`}
      onMouseEnter={() => onHover({ kind: "Topics", id: topic.id })}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover({ kind: "Topics", id: topic.id })}
      onBlur={() => onHover(null)}
      tabIndex={0}
      data-testid={`kf-lv-obj-Topics-${topic.id}`}
    >
      <div className="kf-lv__obj-h">
        <span className="kf-lv__obj-kind kf-lv__obj-kind--topics">TOPIC</span>
        <span className="kf-lv__obj-label">{topic.label}</span>
        <span className="orb-mono kf-lv__obj-id">{topic.id}</span>
      </div>
      <div className="kf-lv__obj-meta">
        {topic.keywords.length > 0 && (
          <>{topic.keywords.slice(0, 6).join(" · ")} · </>
        )}
        {topic.chunkIds.length} chunks
      </div>
    </div>
  );
}

function EntityCard({
  entity,
  highlit,
  onHover,
}: {
  entity: LinkedEntity;
  highlit: boolean;
  onHover: (h: Hover | null) => void;
}): ReactElement {
  return (
    <div
      role="button"
      className={`kf-lv__obj kf-lv__obj--entities ${highlit ? "is-hl" : ""}`}
      onMouseEnter={() => onHover({ kind: "Entities", id: entity.id })}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover({ kind: "Entities", id: entity.id })}
      onBlur={() => onHover(null)}
      tabIndex={0}
      data-testid={`kf-lv-obj-Entities-${entity.id}`}
    >
      <div className="kf-lv__obj-h">
        <span className="kf-lv__obj-kind kf-lv__obj-kind--entities">ENTITY</span>
        <span className="kf-lv__obj-label">{entity.label}</span>
        <span className="orb-mono kf-lv__obj-id">{entity.id}</span>
      </div>
      <div className="kf-lv__obj-meta">
        type · {entity.type} · cited in {entity.chunkIds.length} chunk
        {entity.chunkIds.length === 1 ? "" : "s"}
      </div>
    </div>
  );
}

function ChunkCard({
  chunk,
  topicLabel,
  highlit,
  onHover,
}: {
  chunk: LinkedChunk;
  topicLabel: string | null;
  highlit: boolean;
  onHover: (h: Hover | null) => void;
}): ReactElement {
  const snip =
    chunk.text.length > 110
      ? chunk.text.slice(0, 110) + "…"
      : chunk.text;
  return (
    <div
      role="button"
      className={`kf-lv__obj kf-lv__obj--chunks ${highlit ? "is-hl" : ""}`}
      onMouseEnter={() => onHover({ kind: "Chunks", id: chunk.id })}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover({ kind: "Chunks", id: chunk.id })}
      onBlur={() => onHover(null)}
      tabIndex={0}
      data-testid={`kf-lv-obj-Chunks-${chunk.id}`}
    >
      <div className="kf-lv__obj-h">
        <span className="kf-lv__obj-kind kf-lv__obj-kind--chunks">CHUNK</span>
        <span className="kf-lv__obj-label">
          {chunk.page != null ? `page ${chunk.page}` : "chunk"}
        </span>
        <span className="orb-mono kf-lv__obj-id">{chunk.id}</span>
      </div>
      <div className="kf-lv__obj-snip orb-mono">&quot;{snip}&quot;</div>
      <div className="kf-lv__obj-meta">
        {topicLabel ? (
          <>
            topic <b>{topicLabel}</b> ·{" "}
          </>
        ) : null}
        {chunk.entityIds.length} entities
      </div>
    </div>
  );
}
