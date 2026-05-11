import { useMemo, useState } from "react";

import type {
  ApiDocument,
  ApiKnowledgeGraphProjection,
  ApiSemanticDocument,
} from "../api/types";

/**
 * `LinkedView` — exact port of the mockup's `LinkedView` (orbital-review-a.jsx
 * lines 557-655). Document-as-paper on the left + Topics/Entities/Chunks
 * panel on the right. Hover an object → highlight its source spans;
 * hover a span → highlight the corresponding object(s).
 *
 * Real-data wiring (replaces the mockup's LV_SPANS/LV_TOPICS/LV_ENTITIES):
 *   • Chunks   ← semantic.sections (1 chunk per section in v0.1)
 *   • Topics   ← graph nodes of kind "topic" + their belongs_to edges
 *   • Entities ← graph nodes of kind "entity" + their has_entity edges
 */

type ObjKind = "Topics" | "Entities" | "Chunks";

interface Chunk {
  id: string;
  text: string;
  page?: number;
  topicIds: string[];
  entityIds: string[];
}
interface Topic {
  id: string;
  label: string;
  keywords: string;
  chunkIds: string[];
}
interface Entity {
  id: string;
  label: string;
  type: string;
  chunkIds: string[];
}

export interface LinkedViewProps {
  doc: ApiDocument;
  semantic: ApiSemanticDocument | null;
  graph: ApiKnowledgeGraphProjection | null;
}

export function LinkedView({ doc, semantic, graph }: LinkedViewProps) {
  const [objKind, setObjKind] = useState<ObjKind>("Topics");
  const [hover, setHover] = useState<{ kind: ObjKind; id: string } | null>(null);

  const chunks: Chunk[] = useMemo(() => {
    if (!semantic) return [];
    return (semantic.sections ?? []).map((section) => ({
      id: section.id,
      text: section.text,
      topicIds: [],
      entityIds: [],
    }));
  }, [semantic]);

  const topics: Topic[] = useMemo(() => {
    if (!graph) return [];
    const out: Topic[] = [];
    for (const node of graph.nodes) {
      if (node.kind !== "topic") continue;
      const chunkIds = graph.edges
        .filter((e) => e.kind === "belongs_to" && e.target_id === node.id)
        .map((e) => e.source_id);
      const keywords = Array.isArray(node.properties?.keywords)
        ? (node.properties.keywords as string[]).slice(0, 4).join(" · ")
        : typeof node.properties?.keywords === "string"
          ? (node.properties.keywords as string)
          : "";
      out.push({ id: node.id, label: node.label, keywords, chunkIds });
    }
    return out;
  }, [graph]);

  const entities: Entity[] = useMemo(() => {
    if (!graph) return [];
    const out: Entity[] = [];
    for (const node of graph.nodes) {
      if (node.kind !== "entity") continue;
      const chunkIds = graph.edges
        .filter((e) => e.kind === "has_entity" && e.target_id === node.id)
        .map((e) => e.source_id);
      const type = typeof node.properties?.type === "string" ? (node.properties.type as string) : "entity";
      out.push({ id: node.id, label: node.label, type, chunkIds });
    }
    return out;
  }, [graph]);

  // chunk→topics, chunk→entities reverse maps for cross-highlight
  const chunkRefs = useMemo(() => {
    const t: Record<string, string[]> = {};
    const e: Record<string, string[]> = {};
    for (const topic of topics) for (const c of topic.chunkIds) (t[c] ||= []).push(topic.id);
    for (const entity of entities) for (const c of entity.chunkIds) (e[c] ||= []).push(entity.id);
    return { t, e };
  }, [topics, entities]);

  const isChunkHighlit = (chunkId: string) => {
    if (!hover) return false;
    if (hover.kind === "Chunks") return hover.id === chunkId;
    if (hover.kind === "Topics") return chunkRefs.t[chunkId]?.includes(hover.id) ?? false;
    if (hover.kind === "Entities") return chunkRefs.e[chunkId]?.includes(hover.id) ?? false;
    return false;
  };

  const isObjHighlit = (kind: ObjKind, id: string) => {
    if (!hover) return false;
    if (hover.kind === kind && hover.id === id) return true;
    if (hover.kind === "Chunks") {
      if (kind === "Topics") return chunkRefs.t[hover.id]?.includes(id) ?? false;
      if (kind === "Entities") return chunkRefs.e[hover.id]?.includes(id) ?? false;
    }
    return false;
  };

  const items: Array<Topic | Entity | Chunk> =
    objKind === "Topics" ? topics : objKind === "Entities" ? entities : chunks;

  return (
    <section className="lv">
      <div className="lv-pane lv-doc">
        <div className="lv-pane-h">
          <span className="orb-section-h">Document viewer</span>
          <span className="orb-mono rwA-hint">
            {doc.original_filename}
          </span>
        </div>
        <div className="lv-paper orb-scroll">
          <div className="lv-page">
            <div className="lv-page-n orb-mono">version · {doc.versions.length}</div>
            <h2 className="lv-h1">{doc.original_filename}</h2>
            {chunks.length === 0 && (
              <p className="lv-p" style={{ color: "var(--orb-fg-muted)" }}>
                No semantic sections yet — run <code>Semantic</code> on the Pipeline tab.
              </p>
            )}
            {chunks.map((c) => (
              <p key={c.id} className="lv-p">
                <button
                  type="button"
                  className={`lv-span ${isChunkHighlit(c.id) ? "is-hl" : ""}`}
                  data-cid={c.id}
                  onMouseEnter={() => setHover({ kind: "Chunks", id: c.id })}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover({ kind: "Chunks", id: c.id })}
                  onBlur={() => setHover(null)}
                >
                  {c.text}
                </button>
              </p>
            ))}
          </div>
        </div>
      </div>

      <div className="lv-pane lv-objs">
        <div className="lv-pane-h">
          <span className="orb-section-h">Knowledge objects</span>
          <div className="lv-objtabs">
            {(["Topics", "Entities", "Chunks"] as ObjKind[]).map((k) => (
              <button
                key={k}
                className={`lv-objtab ${objKind === k ? "is-on" : ""}`}
                onClick={() => {
                  setObjKind(k);
                  setHover(null);
                }}
              >
                {k}
                <span className="lv-objtab-n orb-mono">
                  {k === "Topics" ? topics.length : k === "Entities" ? entities.length : chunks.length}
                </span>
              </button>
            ))}
          </div>
        </div>
        <div className="lv-objlist orb-scroll">
          {items.length === 0 && (
            <div className="lv-obj" style={{ cursor: "default" }}>
              <div className="lv-obj-meta">
                {objKind === "Topics" || objKind === "Entities"
                  ? `No ${objKind.toLowerCase()} yet — the knowledge layer populates after the version is VALIDATED.`
                  : "No chunks yet — run Semantic on the Pipeline tab."}
              </div>
            </div>
          )}
          {items.map((o) => {
            const hl = isObjHighlit(objKind, o.id);
            const kindLabel = objKind.slice(0, -1).toUpperCase();
            return (
              <button
                key={o.id}
                type="button"
                className={`lv-obj lv-obj--${objKind.toLowerCase()} ${hl ? "is-hl" : ""}`}
                onMouseEnter={() => setHover({ kind: objKind, id: o.id })}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover({ kind: objKind, id: o.id })}
                onBlur={() => setHover(null)}
              >
                <div className="lv-obj-h">
                  <span className={`lv-obj-kind lv-obj-kind--${objKind.toLowerCase()}`}>{kindLabel}</span>
                  <span className="lv-obj-label">
                    {objKind === "Chunks"
                      ? (o as Chunk).text.slice(0, 40) + "…"
                      : (o as Topic | Entity).label}
                  </span>
                  <span className="orb-mono lv-obj-id">{o.id.slice(0, 6)}</span>
                </div>
                {objKind === "Topics" && (
                  <div className="lv-obj-meta">
                    {(o as Topic).keywords ? `${(o as Topic).keywords} · ` : ""}
                    {(o as Topic).chunkIds.length} chunks
                  </div>
                )}
                {objKind === "Entities" && (
                  <div className="lv-obj-meta">
                    type · {(o as Entity).type} · cited in {(o as Entity).chunkIds.length} chunk
                    {(o as Entity).chunkIds.length === 1 ? "" : "s"}
                  </div>
                )}
                {objKind === "Chunks" && (
                  <>
                    <div className="lv-obj-snip">"{(o as Chunk).text.slice(0, 110)}…"</div>
                    <div className="lv-obj-meta">
                      {chunkRefs.t[o.id]?.length ?? 0} topics · {chunkRefs.e[o.id]?.length ?? 0} entities
                    </div>
                  </>
                )}
              </button>
            );
          })}
        </div>
        <div className="lv-foot orb-mono">
          {hover ? (
            <>
              ● cross-highlighting <b style={{ color: "var(--orb-fg)" }}>{hover.kind.slice(0, -1)}/{hover.id.slice(0, 8)}</b> ↔ document
            </>
          ) : (
            <>hover an object to highlight its source span(s)</>
          )}
        </div>
      </div>
    </section>
  );
}
