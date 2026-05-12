/**
 * GraphInspector — 360px-wide right-side drawer that opens when a node
 * is selected.
 *
 * Per design §5.3:
 *   Header   — kind tag, label, id, close button.
 *   Meta     — properties block.
 *   Edges    — incoming + outgoing.
 *   Spans    — placeholder for "Source spans" (PR 6 ships the
 *              chrome; the spans list lights up once the graph
 *              endpoint exposes per-node source references).
 *   Action   — "Open in Review" — wires to /kf/review/:doc-id when
 *              the selected node carries a document id property.
 */

import type { ReactElement } from "react";

import { Btn, OrbI, SectionH } from "../index";
import type { ApiGraphEdge, ApiGraphNode } from "../../api/types";

export interface GraphInspectorProps {
  node: ApiGraphNode | null;
  incoming: ApiGraphEdge[];
  outgoing: ApiGraphEdge[];
  onClose: () => void;
  onOpenInReview?: (documentId: string) => void;
}

export function GraphInspector({
  node,
  incoming,
  outgoing,
  onClose,
  onOpenInReview,
}: GraphInspectorProps): ReactElement | null {
  if (!node) return null;
  const docId =
    typeof node.properties?.document_id === "string"
      ? node.properties.document_id
      : null;
  return (
    <aside
      className="kf-gv__inspector"
      aria-label={`Graph inspector for ${node.label}`}
      data-testid="kf-gv-inspector"
    >
      <header className="kf-gv__inspector-h">
        <span className={`kf-gv__inspector-kind kf-gv__inspector-kind--${node.kind}`}>
          {node.kind.toUpperCase()}
        </span>
        <div className="kf-gv__inspector-title">
          <h3 className="kf-gv__inspector-label" title={node.label}>
            {node.label}
          </h3>
          <span className="orb-mono kf-gv__inspector-id">{node.id}</span>
        </div>
        <button
          type="button"
          className="kf-gv__inspector-close"
          aria-label="Close inspector"
          onClick={onClose}
        >
          {OrbI.x}
        </button>
      </header>

      <section className="kf-gv__inspector-section">
        <SectionH>Properties</SectionH>
        <PropertiesList properties={node.properties} />
      </section>

      <section className="kf-gv__inspector-section">
        <SectionH>Edges</SectionH>
        <EdgeList label="incoming" edges={incoming} self={node.id} />
        <EdgeList label="outgoing" edges={outgoing} self={node.id} />
      </section>

      <section className="kf-gv__inspector-section">
        <SectionH>Source spans</SectionH>
        <p className="kf-gv__inspector-empty">
          Spans wire up once the graph endpoint exposes per-node source
          references. Use the Linked View on the source document to
          inspect the chunks.
        </p>
      </section>

      {docId && onOpenInReview && (
        <footer className="kf-gv__inspector-foot">
          <Btn kind="primary" icon={OrbI.ext} onClick={() => onOpenInReview(docId)}>
            Open in Review
          </Btn>
        </footer>
      )}
    </aside>
  );
}

function PropertiesList({
  properties,
}: {
  properties: ApiGraphNode["properties"];
}): ReactElement {
  const entries = Object.entries(properties ?? {});
  if (entries.length === 0) {
    return <p className="kf-gv__inspector-empty">No properties.</p>;
  }
  return (
    <dl className="kf-gv__props">
      {entries.map(([k, v]) => (
        <div key={k} className="kf-gv__props-row">
          <dt className="orb-mono">{k}</dt>
          <dd>{formatValue(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function formatValue(v: unknown): string {
  if (v == null) return "—";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function EdgeList({
  label,
  edges,
  self,
}: {
  label: string;
  edges: ApiGraphEdge[];
  self: string;
}): ReactElement {
  if (edges.length === 0) {
    return (
      <p className="kf-gv__inspector-empty">
        <span className="orb-mono kf-gv__inspector-edge-h">
          {label} (0)
        </span>
      </p>
    );
  }
  return (
    <div className="kf-gv__edges-block">
      <p className="orb-mono kf-gv__inspector-edge-h">
        {label} ({edges.length})
      </p>
      <ul className="kf-gv__edges-list">
        {edges.map((e) => {
          const other = e.source_id === self ? e.target_id : e.source_id;
          return (
            <li key={e.id} className="kf-gv__edge-row">
              <span className="kf-gv__edge-kind orb-mono">{e.kind}</span>
              <span className="orb-mono kf-gv__edge-other">{other}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
