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

import { useState } from "react";
import type { ReactElement } from "react";

import { Btn, OrbI } from "../index";
import {
  useLinkedObjects,
  type LinkedChunk,
  type LinkedEntity,
  type LinkedObjects,
  type LinkedTopic,
} from "../hooks/useLinkedObjects";

export type ObjKind = "Topics" | "Entities" | "Chunks";

interface Hover {
  kind: ObjKind;
  id: string;
}

export interface LinkedViewProps {
  /** Document id to fetch the projection for. */
  documentId: string | null;
  /** Document filename (rendered in the viewer header). */
  filename?: string;
  /** Optional fixture override — used by tests to skip the network. */
  fixture?: LinkedObjects;
  /** Loading override (lets tests force the loading branch). */
  loading?: boolean;
}

export function LinkedView({
  documentId,
  filename,
  fixture,
  loading,
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

  if (isEmpty) {
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
    <section className="kf-lv" aria-label="Linked view">
      {/* ── Document viewer (left) ─────────────────────────────── */}
      <div className="kf-lv__pane kf-lv__pane--doc">
        <div className="kf-lv__pane-h">
          <span className="orb-section-h">Document viewer</span>
          <span className="orb-mono kf-lv__hint">
            {filename ?? "document"} · {data.sections.length} section
            {data.sections.length === 1 ? "" : "s"} ·{" "}
            {data.chunks.length} chunks
          </span>
        </div>
        <div className="kf-lv__paper orb-scroll">
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
        </div>
      </div>

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
