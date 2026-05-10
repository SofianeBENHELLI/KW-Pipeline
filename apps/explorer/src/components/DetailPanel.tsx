/**
 * Right-rail detail panel — adapts to the selected node kind.
 *
 *   * Document → type / source / cluster / chunks / confidence,
 *     concept tags, related documents, action row.
 *   * Chunk → parent doc, location, confidence, extracted summary,
 *     related concepts.
 *   * Concept → frequency / synonyms / confidence, evidence chunks,
 *     related concepts.
 *
 * Port of `panels.jsx::DetailPanel`. The action callbacks are
 * deliberately string-tagged ("open" / "expand" / "highlight" /
 * "evidence" / "focusRoot") so the host App can route them onto
 * either local state or graph history without re-typing the contract.
 */

import React from "react";

import { confColor } from "./GraphCanvas";
import { ProjectionStatusPill } from "./ProjectionStatusPill";
import {
  CLUSTERS,
  DOC_TYPES,
  type ExplorerChunk,
  type ExplorerConcept,
  type ExplorerDocument,
  type ExplorerSnapshot,
  chunksForConcept,
  chunksForDoc,
  conceptById,
  conceptsForChunk,
  docById,
  docsForConcept,
} from "../state/explorer-data";
import { useProjectionStatus } from "../state/use-projection-status";
import { Icon, ACCENT, NAVY, NAVY2 } from "./icons";

export type DetailKind = "cluster" | "doc" | "chunk" | "concept";

export interface DetailNode {
  kind: DetailKind;
  id: string;
  doc?: ExplorerDocument;
  chunk?: ExplorerChunk;
  concept?: ExplorerConcept;
  cluster?: string;
}

export type DetailAction =
  | { kind: "open"; doc: ExplorerDocument }
  | { kind: "expand"; doc: ExplorerDocument }
  | { kind: "highlight"; chunk: ExplorerChunk }
  | { kind: "evidence"; concept: ExplorerConcept }
  | { kind: "focusRoot"; node: DetailNode };

interface Props {
  snapshot: ExplorerSnapshot;
  node: DetailNode | null;
  onAction: (action: DetailAction) => void;
  onSelectId: (id: string, kind: "doc" | "chunk" | "concept") => void;
  /**
   * Bug B — id of the chunk currently cross-highlighted with the
   * document viewer. The doc-detail chunks list and the
   * concept-evidence list use this to render a ``kx-on`` row + scroll
   * the row into view, so the user always knows which chunk the
   * orange bracket in the viewer corresponds to.
   */
  highlightChunkId?: string | null;
  /**
   * Open the lineage modal for the supplied document. When omitted,
   * the "View history" link in the Versions section is hidden — the
   * link is purely a discoverability affordance for the modal that
   * the v{N} badge already opens.
   */
  onOpenLineage?: (doc: ExplorerDocument) => void;
}

const DetailRow: React.FC<{ label: string; value: React.ReactNode; mono?: boolean }> = ({ label, value, mono }) => (
  <div className="kx-row">
    <div className="kx-row-l">{label}</div>
    <div className={"kx-row-v" + (mono ? " kx-mono" : "")}>{value}</div>
  </div>
);

const ConfBar: React.FC<{ value: number }> = ({ value }) => (
  <div className="kx-conf">
    <div className="kx-conf-track">
      <div className="kx-conf-fill" style={{ width: `${value * 100}%`, background: confColor(value) }} />
    </div>
    <span className="kx-mono" style={{ color: confColor(value) }}>
      {value.toFixed(2)}
    </span>
  </div>
);

export const DetailPanel: React.FC<Props> = ({
  snapshot,
  node,
  onAction,
  onSelectId,
  highlightChunkId,
  onOpenLineage,
}) => {
  // Bug B — when the highlighted chunk changes, scroll its row into
  // view in whichever section is rendering the chunk list (doc detail
  // chunks list, or concept evidence list). ``block: "nearest"`` keeps
  // the panel from jumping if the row is already visible.
  const activeRowRef = React.useRef<HTMLLIElement | null>(null);
  React.useEffect(() => {
    activeRowRef.current?.scrollIntoView({ block: "nearest" });
  }, [highlightChunkId]);

  // Resolve the latest VALIDATED version_id once at the top of the
  // component so the projection-status hook fires unconditionally
  // (rules of hooks). ``null`` when the selected node isn't a
  // document, isn't validated, or has no version metadata — the hook
  // short-circuits to an inert state and the pill renders nothing.
  const docForProjection = node?.kind === "doc" ? (node.doc ?? docById(snapshot, node.id)) : null;
  const latestVersionForProjection = docForProjection?.versions?.[
    (docForProjection.versions?.length ?? 0) - 1
  ];
  const projectionVersionId =
    latestVersionForProjection?.status === "VALIDATED"
      ? latestVersionForProjection.id
      : null;
  const projection = useProjectionStatus(projectionVersionId);

  if (!node) {
    return (
      <div className="kx-detail kx-detail-empty">
        <Icon name="info" size={20} stroke={NAVY2} />
        <div className="kx-detail-empty-t">Nothing selected</div>
        <div className="kx-detail-empty-s">
          Select a node to inspect its metadata, evidence and relationships.
        </div>
      </div>
    );
  }

  if (node.kind === "doc") {
    const d = node.doc ?? docById(snapshot, node.id);
    if (!d) return null;
    const dt = DOC_TYPES[d.type];
    const docChunks = chunksForDoc(snapshot, d.id);
    const concepts = [
      ...new Set(docChunks.flatMap((c) => conceptsForChunk(snapshot, c.id).map((k) => k.id))),
    ]
      .map((id) => conceptById(snapshot, id))
      .filter((x): x is ExplorerConcept => Boolean(x));
    const related = snapshot.docEdges
      .filter((e) => e.a === d.id || e.b === d.id)
      .map((e) => docById(snapshot, e.a === d.id ? e.b : e.a))
      .filter((x): x is ExplorerDocument => Boolean(x))
      .slice(0, 5);
    const versionCount = d.versionCount ?? d.versions?.length ?? 1;
    const latestVersion = d.latestVersion ?? d.versions?.[d.versions.length - 1]?.versionNumber ?? 1;
    return (
      <div className="kx-detail">
        <div className="kx-detail-head">
          <span className="kx-doc-chip" style={{ background: dt?.color ?? "#888" }}>
            {dt?.short ?? "DOC"}
          </span>
          <div>
            <div className="kx-kind">DOCUMENT</div>
            <div className="kx-detail-title">
              {d.title}
              <span className="kx-ver-badge kx-mono" title={`Latest version v${latestVersion}`}>
                v{latestVersion}
              </span>
              {versionCount > 1 && (
                <span className="kx-ver-count kx-mute">({versionCount} versions)</span>
              )}
              <ProjectionStatusPill status={projection.status} done={projection.done} />
            </div>
          </div>
        </div>
        <div className="kx-section">
          <DetailRow label="TYPE" value={dt?.label ?? d.type} />
          <DetailRow label="SOURCE" value={d.source} />
          <DetailRow label="IMPORTED" value={d.date} mono />
          <DetailRow label="CHUNKS" value={d.chunks} mono />
          <DetailRow label="CLUSTER" value={CLUSTERS[d.cluster]?.label ?? d.cluster} />
          <DetailRow label="CONFIDENCE" value={<ConfBar value={d.confidence} />} />
        </div>
        <div className="kx-section">
          <div className="kx-sec-h">MAIN CONCEPTS</div>
          <div className="kx-tags">
            {concepts.slice(0, 6).map((k) => (
              <button key={k.id} className="kx-tag" onClick={() => onSelectId(k.id, "concept")}>
                <Icon name="concept" size={10} />
                {k.name}
              </button>
            ))}
            {concepts.length === 0 && <span className="kx-mute">No concepts extracted</span>}
          </div>
        </div>
        <div className="kx-section">
          <div className="kx-sec-h">RELATED DOCUMENTS</div>
          <ul className="kx-list">
            {related.map((rd) => (
              <li key={rd.id} onClick={() => onSelectId(rd.id, "doc")}>
                <span className="kx-doc-chip kx-sm" style={{ background: DOC_TYPES[rd.type]?.color ?? "#888" }}>
                  {DOC_TYPES[rd.type]?.short ?? "DOC"}
                </span>
                <span className="kx-list-t">{rd.title}</span>
                <Icon name="chevron-right" size={12} stroke={NAVY2} />
              </li>
            ))}
            {related.length === 0 && <li className="kx-mute">No related documents</li>}
          </ul>
        </div>
        <div className="kx-section">
          {/*
            Bug B — chunks list with cross-highlight to the document
            viewer. ``kx-on`` mirrors the orange bracket in the viewer
            so the user always knows which chunk's text is highlighted.
            Clicking a row fires ``onAction({ kind: "highlight" })``
            instead of ``onSelectId(..., "chunk")`` because the latter
            would replace the doc detail panel with a chunk panel —
            keeping the doc panel mounted lets the user scrub multiple
            chunks without losing context.
          */}
          <div className="kx-sec-h">CHUNKS · {docChunks.length}</div>
          <ul className="kx-list kx-chunk-list" data-testid="kx-doc-chunks">
            {docChunks.map((c) => {
              const active = highlightChunkId === c.id;
              return (
                <li
                  key={c.id}
                  ref={active ? activeRowRef : undefined}
                  className={active ? "kx-on" : undefined}
                  aria-selected={active}
                  data-chunk-id={c.id}
                  onClick={() => onAction({ kind: "highlight", chunk: c })}
                >
                  <span className="kx-mono kx-mute kx-sm">{c.id}</span>
                  <span className="kx-list-t">{c.label}</span>
                  <Icon name="chevron-right" size={12} stroke={NAVY2} />
                </li>
              );
            })}
            {docChunks.length === 0 && <li className="kx-mute">No chunks indexed</li>}
          </ul>
        </div>
        {/*
          Versions section — surfaces every version_number in the
          family with its status + ingested_at. Earlier versions are
          read-only (no actions) per the sprint scope; the lineage
          modal (EPIC-C C.5) lands later when the
          ``/documents/{id}/lineage`` endpoint exists.
        */}
        <div className="kx-section" data-testid="kx-versions-section">
          <div className="kx-sec-h kx-versions-h">
            <span>VERSIONS · {versionCount}</span>
            {versionCount > 1 && onOpenLineage && (
              <button
                type="button"
                className="kx-link kx-versions-history"
                onClick={() => onOpenLineage(d)}
                data-testid="kx-versions-history-link"
              >
                View history
              </button>
            )}
          </div>
          <ul className="kx-list kx-version-list">
            {(d.versions ?? [{ id: d.id, versionNumber: 1, status: "UPLOADED", createdAt: d.date, filename: d.title }])
              .slice()
              .sort((a, b) => b.versionNumber - a.versionNumber)
              .map((v) => {
                const isLatest = v.versionNumber === latestVersion;
                return (
                  <li key={v.id} data-version-number={v.versionNumber}>
                    <span className={"kx-ver-badge kx-mono" + (isLatest ? " kx-ver-latest" : "")}>
                      v{v.versionNumber}
                    </span>
                    <span className="kx-list-t">{v.status}</span>
                    <span className="kx-mono kx-mute">{v.createdAt.slice(0, 10)}</span>
                  </li>
                );
              })}
          </ul>
        </div>
        <div className="kx-actions">
          <button className="kx-btn kx-btn-pri" onClick={() => onAction({ kind: "open", doc: d })}>
            <Icon name="external" size={12} />
            Open document
          </button>
          <button className="kx-btn" onClick={() => onAction({ kind: "expand", doc: d })}>
            <Icon name="expand" size={12} />
            Expand to chunks
          </button>
          <button className="kx-btn" onClick={() => onAction({ kind: "focusRoot", node: { kind: "doc", id: d.id, doc: d } })}>
            <Icon name="focus" size={12} />
            Focus from here
          </button>
        </div>
      </div>
    );
  }

  if (node.kind === "chunk") {
    const c = node.chunk ?? snapshot.chunks.find((x) => x.id === node.id);
    if (!c) return null;
    const parent = docById(snapshot, c.doc);
    const concepts = conceptsForChunk(snapshot, c.id);
    return (
      <div className="kx-detail">
        <div className="kx-detail-head">
          <div className="kx-chunk-mark">
            <Icon name="chunk" size={14} stroke={ACCENT} />
          </div>
          <div>
            <div className="kx-kind">CHUNK</div>
            <div className="kx-detail-title">{c.label}</div>
          </div>
        </div>
        <div className="kx-section">
          <DetailRow label="ID" value={c.id} mono />
          <DetailRow
            label="PARENT DOC"
            value={
              parent ? (
                <a className="kx-link" onClick={() => onSelectId(parent.id, "doc")}>
                  {parent.title}
                </a>
              ) : (
                "—"
              )
            }
          />
          <DetailRow label="LOCATION" value={`p.${c.page} · ${c.kind}`} mono />
          <DetailRow label="CONFIDENCE" value={<ConfBar value={c.confidence} />} />
        </div>
        <div className="kx-section">
          <div className="kx-sec-h">EXTRACTED SUMMARY</div>
          <div className="kx-summary">{c.summary}</div>
        </div>
        <div className="kx-section">
          <div className="kx-sec-h">RELATED CONCEPTS</div>
          <div className="kx-tags">
            {concepts.map((k) => (
              <button key={k.id} className="kx-tag" onClick={() => onSelectId(k.id, "concept")}>
                <Icon name="concept" size={10} />
                {k.name}
              </button>
            ))}
            {concepts.length === 0 && <span className="kx-mute">No concepts linked</span>}
          </div>
        </div>
        <div className="kx-actions">
          <button className="kx-btn kx-btn-pri" onClick={() => onAction({ kind: "highlight", chunk: c })}>
            <Icon name="highlight" size={12} />
            Highlight in document
          </button>
          <button
            className="kx-btn"
            onClick={() => onAction({ kind: "focusRoot", node: { kind: "chunk", id: c.id, chunk: c } })}
          >
            <Icon name="focus" size={12} />
            Focus from here
          </button>
        </div>
      </div>
    );
  }

  if (node.kind === "concept") {
    const k = node.concept ?? conceptById(snapshot, node.id);
    if (!k) return null;
    const evidence = chunksForConcept(snapshot, k.id);
    void docsForConcept(snapshot, k.id); // accessed through the evidence list rendering
    const related = snapshot.conceptEdges
      .filter(([a, b]) => a === k.id || b === k.id)
      .map(([a, b]) => (a === k.id ? b : a))
      .map((id) => conceptById(snapshot, id))
      .filter((x): x is ExplorerConcept => Boolean(x));
    return (
      <div className="kx-detail">
        <div className="kx-detail-head">
          <div className="kx-concept-mark">
            <Icon name="concept" size={14} stroke={NAVY} />
          </div>
          <div>
            <div className="kx-kind">CONCEPT</div>
            <div className="kx-detail-title">{k.name}</div>
          </div>
        </div>
        <div className="kx-section">
          <DetailRow label="TYPE" value={k.kind} />
          <DetailRow
            label="FREQUENCY"
            value={
              <span>
                <span className="kx-mono">{k.freq}</span> mentions
              </span>
            }
          />
          <DetailRow label="CONFIDENCE" value={<ConfBar value={k.confidence} />} />
          <DetailRow label="SYNONYMS" value={k.syn.join(", ") || "—"} mono />
        </div>
        <div className="kx-section">
          <div className="kx-sec-h">EVIDENCE CHUNKS · {evidence.length}</div>
          <ul className="kx-list kx-chunk-list">
            {evidence.map((ec) => {
              const active = highlightChunkId === ec.id;
              return (
                <li
                  key={ec.id}
                  ref={active ? activeRowRef : undefined}
                  className={active ? "kx-on" : undefined}
                  aria-selected={active}
                  data-chunk-id={ec.id}
                  onClick={() => onSelectId(ec.id, "chunk")}
                >
                  <span className="kx-mono kx-mute kx-sm">{ec.id}</span>
                  <span className="kx-list-t">{ec.label}</span>
                  <Icon name="chevron-right" size={12} stroke={NAVY2} />
                </li>
              );
            })}
            {evidence.length === 0 && <li className="kx-mute">No evidence chunks</li>}
          </ul>
        </div>
        <div className="kx-section">
          <div className="kx-sec-h">RELATED CONCEPTS</div>
          <div className="kx-tags">
            {related.map((r) => (
              <button key={r.id} className="kx-tag" onClick={() => onSelectId(r.id, "concept")}>
                <Icon name="concept" size={10} />
                {r.name}
              </button>
            ))}
            {related.length === 0 && <span className="kx-mute">No related concepts</span>}
          </div>
        </div>
        <div className="kx-actions">
          <button className="kx-btn kx-btn-pri" onClick={() => onAction({ kind: "evidence", concept: k })}>
            <Icon name="highlight" size={12} />
            Show evidence in source
          </button>
          <button
            className="kx-btn"
            onClick={() => onAction({ kind: "focusRoot", node: { kind: "concept", id: k.id, concept: k } })}
          >
            <Icon name="focus" size={12} />
            Focus from here
          </button>
        </div>
      </div>
    );
  }

  // Cluster — minimal panel (the design omits this, we add a stub).
  if (node.kind === "cluster") {
    const cluster = node.cluster ?? node.id;
    const meta = CLUSTERS[cluster];
    const docs = snapshot.documents.filter((d) => d.cluster === cluster);
    return (
      <div className="kx-detail">
        <div className="kx-detail-head">
          <div className="kx-concept-mark">
            <Icon name="clusters" size={14} stroke={NAVY} />
          </div>
          <div>
            <div className="kx-kind">CLUSTER</div>
            <div className="kx-detail-title">{meta?.label ?? cluster}</div>
          </div>
        </div>
        <div className="kx-section">
          <DetailRow label="DOCUMENTS" value={docs.length} mono />
          <DetailRow label="CHUNKS" value={docs.reduce((a, d) => a + d.chunks, 0)} mono />
        </div>
        <div className="kx-section">
          <div className="kx-sec-h">DOCUMENTS</div>
          <ul className="kx-list">
            {docs.slice(0, 8).map((d) => (
              <li key={d.id} onClick={() => onSelectId(d.id, "doc")}>
                <span className="kx-doc-chip kx-sm" style={{ background: DOC_TYPES[d.type]?.color ?? "#888" }}>
                  {DOC_TYPES[d.type]?.short ?? "DOC"}
                </span>
                <span className="kx-list-t">{d.title}</span>
                <Icon name="chevron-right" size={12} stroke={NAVY2} />
              </li>
            ))}
          </ul>
        </div>
      </div>
    );
  }

  return null;
};
