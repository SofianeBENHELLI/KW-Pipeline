import { useMemo, useState } from "react";

import type {
  ApiDocument,
  ApiKnowledgeGraphProjection,
  ApiSemanticDocument,
  ApiSemanticSection,
} from "../api/types";

type ObjectKind = "Topics" | "Entities" | "Chunks";

interface HoverState {
  kind: ObjectKind;
  id: string;
}

interface ChunkRow {
  id: string;
  text: string;
  page: number | null;
  section_id: string | null;
  topicIds: string[];
  entityIds: string[];
}

interface TopicRow {
  id: string;
  label: string;
  chunkIds: string[];
  keywords: string;
}

interface EntityRow {
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

/**
 * Variant-A "Linked view" — the mockup's headline interaction. Renders
 * the document as a paper-style page on the left, and a Topics /
 * Entities / Chunks side panel on the right. Hovering an object in the
 * panel highlights every text span in the doc that the object covers;
 * hovering a text span highlights the corresponding object.
 *
 * Real data wiring:
 *   • Chunks      ← semantic.sections (each section is one chunk for v0.1)
 *   • Topics      ← graph nodes of kind "topic" + their belongs_to edges
 *   • Entities    ← graph nodes of kind "entity" + their has_entity edges
 * When the graph is empty (the doc isn't validated yet) the Topics and
 * Entities tabs render an empty-state and the Chunks tab works directly
 * off the semantic document so reviewers can still scrub the structure
 * before validating.
 */
export function LinkedView({ doc, semantic, graph }: LinkedViewProps) {
  const [objKind, setObjKind] = useState<ObjectKind>("Topics");
  const [hover, setHover] = useState<HoverState | null>(null);

  const chunks = useMemo<ChunkRow[]>(() => {
    if (!semantic) return [];
    return (semantic.sections ?? []).map((section: ApiSemanticSection) => ({
      id: section.id,
      text: section.text,
      page: null,
      section_id: section.id,
      topicIds: [],
      entityIds: [],
    }));
  }, [semantic]);

  const topics = useMemo<TopicRow[]>(() => {
    if (!graph) return [];
    const out: TopicRow[] = [];
    for (const node of graph.nodes) {
      if (node.kind !== "topic") continue;
      const chunkIds = graph.edges
        .filter((edge) => edge.kind === "belongs_to" && edge.target_id === node.id)
        .map((edge) => edge.source_id);
      const keywords = Array.isArray(node.properties?.keywords)
        ? (node.properties.keywords as string[]).slice(0, 4).join(" · ")
        : typeof node.properties?.keywords === "string"
          ? String(node.properties.keywords)
          : "";
      out.push({ id: node.id, label: node.label, chunkIds, keywords });
    }
    return out;
  }, [graph]);

  const entities = useMemo<EntityRow[]>(() => {
    if (!graph) return [];
    const out: EntityRow[] = [];
    for (const node of graph.nodes) {
      if (node.kind !== "entity") continue;
      const chunkIds = graph.edges
        .filter((edge) => edge.kind === "has_entity" && edge.target_id === node.id)
        .map((edge) => edge.source_id);
      const type =
        typeof node.properties?.type === "string"
          ? (node.properties.type as string)
          : "entity";
      out.push({ id: node.id, label: node.label, chunkIds, type });
    }
    return out;
  }, [graph]);

  // Reverse lookups: which topics/entities reference a given chunk.
  const chunkRefs = useMemo(() => {
    const t: Record<string, string[]> = {};
    const e: Record<string, string[]> = {};
    for (const topic of topics) {
      for (const cid of topic.chunkIds) {
        if (!t[cid]) t[cid] = [];
        t[cid].push(topic.id);
      }
    }
    for (const entity of entities) {
      for (const cid of entity.chunkIds) {
        if (!e[cid]) e[cid] = [];
        e[cid].push(entity.id);
      }
    }
    return { t, e };
  }, [topics, entities]);

  const isChunkHighlit = (chunkId: string): boolean => {
    if (!hover) return false;
    if (hover.kind === "Chunks") return hover.id === chunkId;
    if (hover.kind === "Topics") return chunkRefs.t[chunkId]?.includes(hover.id) ?? false;
    if (hover.kind === "Entities") return chunkRefs.e[chunkId]?.includes(hover.id) ?? false;
    return false;
  };

  const isObjHighlit = (kind: ObjectKind, id: string): boolean => {
    if (!hover) return false;
    if (hover.kind === kind && hover.id === id) return true;
    if (hover.kind === "Chunks") {
      if (kind === "Topics") return chunkRefs.t[hover.id]?.includes(id) ?? false;
      if (kind === "Entities") return chunkRefs.e[hover.id]?.includes(id) ?? false;
    }
    return false;
  };

  const items: Array<
    | { kind: "Topics"; topic: TopicRow }
    | { kind: "Entities"; entity: EntityRow }
    | { kind: "Chunks"; chunk: ChunkRow }
  > =
    objKind === "Topics"
      ? topics.map((topic) => ({ kind: "Topics", topic }))
      : objKind === "Entities"
        ? entities.map((entity) => ({ kind: "Entities", entity }))
        : chunks.map((chunk) => ({ kind: "Chunks", chunk }));

  return (
    <section className="lv">
      <div className="lv-pane lv-doc">
        <div className="lv-pane-h">
          <span className="orb-section-h">Document viewer</span>
          <span className="orb-mono rwA-hint">{doc.original_filename}</span>
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
            {chunks.map((chunk) => (
              <p key={chunk.id} className="lv-p">
                <button
                  type="button"
                  className={`lv-span ${isChunkHighlit(chunk.id) ? "is-hl" : ""}`.trim()}
                  onMouseEnter={() => setHover({ kind: "Chunks", id: chunk.id })}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover({ kind: "Chunks", id: chunk.id })}
                  onBlur={() => setHover(null)}
                  style={{ background: "none", border: 0, color: "inherit", font: "inherit", padding: 0, textAlign: "left" }}
                >
                  <span className="lv-span">{chunk.text}</span>
                </button>
              </p>
            ))}
          </div>
        </div>
      </div>

      <div className="lv-pane lv-objs">
        <div className="lv-pane-h">
          <span className="orb-section-h">Knowledge objects</span>
          <div className="lv-objtabs" role="tablist">
            {(["Topics", "Entities", "Chunks"] as ObjectKind[]).map((k) => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={k === objKind}
                className={`lv-objtab ${k === objKind ? "is-on" : ""}`.trim()}
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
        <div className="lv-objlist orb-scroll" role="listbox" aria-label="Knowledge objects">
          {items.length === 0 && (
            <div className="lv-obj" style={{ cursor: "default" }}>
              <div className="lv-obj-meta">
                {objKind === "Topics" || objKind === "Entities"
                  ? `No ${objKind.toLowerCase()} yet — the knowledge layer populates after the version is VALIDATED.`
                  : "No chunks yet — run Semantic on the Pipeline tab."}
              </div>
            </div>
          )}
          {items.map((item) => {
            if (item.kind === "Topics") {
              const hl = isObjHighlit("Topics", item.topic.id);
              return (
                <button
                  type="button"
                  role="option"
                  aria-selected={hl}
                  key={item.topic.id}
                  className={`lv-obj lv-obj--topics ${hl ? "is-hl" : ""}`.trim()}
                  onMouseEnter={() => setHover({ kind: "Topics", id: item.topic.id })}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover({ kind: "Topics", id: item.topic.id })}
                  onBlur={() => setHover(null)}
                >
                  <div className="lv-obj-h">
                    <span className="lv-obj-kind lv-obj-kind--topics">TOPIC</span>
                    <span className="lv-obj-label">{item.topic.label}</span>
                    <span className="lv-obj-id">{item.topic.id.slice(0, 6)}</span>
                  </div>
                  <div className="lv-obj-meta">
                    {item.topic.keywords ? `${item.topic.keywords} · ` : ""}
                    {item.topic.chunkIds.length} chunk{item.topic.chunkIds.length === 1 ? "" : "s"}
                  </div>
                </button>
              );
            }
            if (item.kind === "Entities") {
              const hl = isObjHighlit("Entities", item.entity.id);
              return (
                <button
                  type="button"
                  role="option"
                  aria-selected={hl}
                  key={item.entity.id}
                  className={`lv-obj lv-obj--entities ${hl ? "is-hl" : ""}`.trim()}
                  onMouseEnter={() => setHover({ kind: "Entities", id: item.entity.id })}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover({ kind: "Entities", id: item.entity.id })}
                  onBlur={() => setHover(null)}
                >
                  <div className="lv-obj-h">
                    <span className="lv-obj-kind lv-obj-kind--entities">ENTITY</span>
                    <span className="lv-obj-label">{item.entity.label}</span>
                    <span className="lv-obj-id">{item.entity.id.slice(0, 6)}</span>
                  </div>
                  <div className="lv-obj-meta">
                    type · {item.entity.type} · cited in {item.entity.chunkIds.length} chunk
                    {item.entity.chunkIds.length === 1 ? "" : "s"}
                  </div>
                </button>
              );
            }
            const hl = isObjHighlit("Chunks", item.chunk.id);
            return (
              <button
                type="button"
                role="option"
                aria-selected={hl}
                key={item.chunk.id}
                className={`lv-obj lv-obj--chunks ${hl ? "is-hl" : ""}`.trim()}
                onMouseEnter={() => setHover({ kind: "Chunks", id: item.chunk.id })}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover({ kind: "Chunks", id: item.chunk.id })}
                onBlur={() => setHover(null)}
              >
                <div className="lv-obj-h">
                  <span className="lv-obj-kind lv-obj-kind--chunks">CHUNK</span>
                  <span className="lv-obj-label">{item.chunk.text.slice(0, 48)}…</span>
                  <span className="lv-obj-id">{item.chunk.id.slice(0, 6)}</span>
                </div>
                <div className="lv-obj-snip">"{item.chunk.text.slice(0, 110)}…"</div>
                <div className="lv-obj-meta">
                  {item.chunk.page ? `page ${item.chunk.page} · ` : ""}
                  {chunkRefs.t[item.chunk.id]?.length ?? 0} topic
                  {(chunkRefs.t[item.chunk.id]?.length ?? 0) === 1 ? "" : "s"} ·{" "}
                  {chunkRefs.e[item.chunk.id]?.length ?? 0} entit
                  {(chunkRefs.e[item.chunk.id]?.length ?? 0) === 1 ? "y" : "ies"}
                </div>
              </button>
            );
          })}
        </div>
        <div className="lv-foot">
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
