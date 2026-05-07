/**
 * KnowledgeGraphView — renders the document-scoped knowledge graph.
 *
 * Wraps `@neo4j-nvl/react`'s <InteractiveNvlWrapper>. This file is the
 * ONLY module that imports from `@neo4j-nvl/react`; consumers should go
 * through the barrel in `./index.ts` so the dep stays contained.
 *
 * Demo KG (Lane D, #148/#149/#150/#151) layered three things on top of
 * the original Phase-1a panel:
 *
 *   * Chunk + topic node colors, plus deterministic-relation edge
 *     captions (#149).
 *   * A filter toolbar (`All` / `Chunks` / `Topics` / `Entities` /
 *     `Relations` / `Source-backed`) that re-derives the filtered
 *     subgraph in a `useMemo` so the NVL canvas re-mounts cleanly
 *     (#150).
 *   * A details inspector below the canvas (#151) that lists the
 *     filtered nodes + edges and shows the v0.2 audit trail
 *     (heading, keywords, topic, score, reason, shared keywords) on
 *     click. Selecting via the inspector instead of pixel-clicking
 *     the NVL canvas keeps the panel keyboard-accessible and easy to
 *     test under jsdom (the NVL canvas mocks out as a stub).
 *
 * The five legacy states (loading / pre-validation / disabled / error
 * / loaded) are preserved exactly — only the `loaded` branch is
 * augmented.
 *
 * Refresh seam: `refreshKey` is bumped by the parent after a mutation
 * (validate, edit, …) lands. Changes to it re-issue the fetch; an in-flight
 * request from a previous `refreshKey` is dropped via the cancel flag.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { InteractiveNvlWrapper } from "@neo4j-nvl/react";
import type { NVL } from "@neo4j-nvl/base";

import { ApiError, getDocumentGraph } from "../../api/client";
import type {
  ApiGraphEdge,
  ApiGraphNode,
  ApiKnowledgeGraphProjection,
  DocumentVersionStatus,
} from "../../api/types";
import {
  EDGE_KIND_LABELS,
  FILTER_OPTIONS,
  type FilterMode,
  type FilteredProjection,
  NODE_KIND_COLORS,
  NODE_KIND_LABELS,
  filterProjection,
  toNvlNodes,
  toNvlRelationships,
} from "./types";

// `filterProjection` is also re-exported here so existing test
// imports (`from "./KnowledgeGraphView"`) keep working without
// touching every test file. Future call sites should import from
// `./types` directly.
export { filterProjection } from "./types";

// ─── Component ───────────────────────────────────────────────────────────────

interface KnowledgeGraphViewProps {
  documentId: string | null;
  documentStatus?: DocumentVersionStatus | null;
  refreshKey?: number;
}

type Selection =
  | { kind: "node"; id: string }
  | { kind: "edge"; id: string }
  | null;

export default function KnowledgeGraphView({
  documentId,
  documentStatus = null,
  refreshKey = 0,
}: KnowledgeGraphViewProps) {
  const [projection, setProjection] = useState<ApiKnowledgeGraphProjection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryAttempt, setRetryAttempt] = useState(0);
  const [filter, setFilter] = useState<FilterMode>("all");
  const [selected, setSelected] = useState<Selection>(null);
  const [search, setSearch] = useState("");
  const nvlRef = useRef<NVL | null>(null);

  useEffect(() => {
    if (documentId === null) {
      setProjection(null);
      setLoading(false);
      setError(null);
      setSelected(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    setProjection(null);

    getDocumentGraph(documentId)
      .then((data) => {
        if (!cancelled) setProjection(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setProjection(null);
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load graph.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [documentId, refreshKey, retryAttempt]);

  // Reset the selection whenever the underlying projection changes —
  // a stale id would render against missing data otherwise.
  useEffect(() => {
    setSelected(null);
  }, [projection]);

  const filtered = useMemo<FilteredProjection>(
    () =>
      projection ? filterProjection(projection, filter) : { nodes: [], edges: [] },
    [projection, filter],
  );

  // Clear the selection when its target gets filtered out (#150 acceptance).
  useEffect(() => {
    if (selected === null) return;
    const stillVisible =
      selected.kind === "node"
        ? filtered.nodes.some((n) => n.id === selected.id)
        : filtered.edges.some((e) => e.id === selected.id);
    if (!stillVisible) setSelected(null);
  }, [filtered, selected]);

  const nvlNodes = useMemo(() => toNvlNodes(filtered.nodes), [filtered]);
  const nvlRels = useMemo(() => toNvlRelationships(filtered.edges), [filtered]);

  const isEmptyPayload = projection === null || projection.nodes.length === 0;
  const isPreValidation =
    documentStatus !== null && documentStatus !== "VALIDATED";

  const selectedNode = useMemo(
    () =>
      selected !== null && selected.kind === "node"
        ? (filtered.nodes.find((n) => n.id === selected.id) ?? null)
        : null,
    [selected, filtered],
  );
  const selectedEdge = useMemo(
    () =>
      selected !== null && selected.kind === "edge"
        ? (filtered.edges.find((e) => e.id === selected.id) ?? null)
        : null,
    [selected, filtered],
  );

  // #292 — fit-to-node when an inspector item is picked. NVL's fit
  // method tweens pan + zoom; the ref is only populated once the
  // canvas mounts, so guard against the loading branches.
  useEffect(() => {
    if (selectedNode === null) return;
    nvlRef.current?.fit([selectedNode.id]);
  }, [selectedNode]);

  function zoomBy(factor: number) {
    const nvl = nvlRef.current;
    if (!nvl) return;
    const current = nvl.getScale();
    nvl.setZoom(current * factor);
  }
  function resetView() {
    nvlRef.current?.resetZoom();
  }
  function fitAll() {
    const ids = filtered.nodes.map((n) => n.id);
    if (ids.length === 0) return;
    nvlRef.current?.fit(ids);
  }
  function applySearch(term: string) {
    setSearch(term);
    if (term.trim().length === 0) return;
    const needle = term.toLowerCase();
    const hit = filtered.nodes.find(
      (n) =>
        n.label.toLowerCase().includes(needle) ||
        n.id.toLowerCase().includes(needle),
    );
    if (hit) {
      setSelected({ kind: "node", id: hit.id });
    }
  }

  return (
    <article className="panel graph-panel" aria-labelledby="graph-panel-title">
      <div className="panel-heading">
        <h3 id="graph-panel-title">Knowledge graph</h3>
        <GraphLegend />
      </div>

      {documentId === null ? (
        <p className="muted" role="status">
          Select a document to view its knowledge graph.
        </p>
      ) : loading ? (
        <p className="muted" role="status" aria-live="polite">
          Loading graph…
        </p>
      ) : error !== null ? (
        <div className="notice danger" role="alert">
          <strong>Couldn&apos;t load the knowledge graph</strong>
          <span>{error}</span>
          <button
            type="button"
            className="button"
            onClick={() => setRetryAttempt((n) => n + 1)}
          >
            Retry
          </button>
        </div>
      ) : isEmptyPayload && isPreValidation ? (
        <p className="muted" role="status">
          The knowledge graph is generated after a reviewer validates this
          document. Validate the document to see its projection here.
        </p>
      ) : isEmptyPayload && documentStatus === "VALIDATED" ? (
        <div className="notice info" role="status">
          <strong>Knowledge graph is an optional add-on</strong>
          <span>
            Enable it by starting the API with{" "}
            <code>KW_KNOWLEDGE_LAYER_ENABLED=true</code> and a Neo4j
            instance — see the Knowledge Layer wiki.
          </span>
        </div>
      ) : isEmptyPayload ? (
        <p className="muted">
          No knowledge graph projection has been generated for this document yet.
        </p>
      ) : (
        <>
          <FilterToolbar
            filter={filter}
            setFilter={setFilter}
            visibleNodeCount={filtered.nodes.length}
            visibleEdgeCount={filtered.edges.length}
          />

          <NavigationToolbar
            search={search}
            onSearchChange={applySearch}
            onZoomIn={() => zoomBy(1.25)}
            onZoomOut={() => zoomBy(0.8)}
            onReset={resetView}
            onFit={fitAll}
          />

          <div
            className="graph-canvas"
            data-testid="knowledge-graph-canvas"
            style={{ height: 480, width: "100%" }}
          >
            <InteractiveNvlWrapper
              ref={nvlRef}
              nodes={nvlNodes}
              rels={nvlRels}
              nvlOptions={{
                initialZoom: 0.75,
                allowDynamicMinZoom: true,
                minZoom: 0.05,
                maxZoom: 4,
              }}
              mouseEventCallbacks={{
                onZoom: true,
                onPan: true,
                onDrag: true,
              }}
            />
          </div>

          <DetailsInspector
            nodes={filtered.nodes}
            edges={filtered.edges}
            selected={selected}
            setSelected={setSelected}
            selectedNode={selectedNode}
            selectedEdge={selectedEdge}
          />
        </>
      )}
    </article>
  );
}

// ─── Legend ──────────────────────────────────────────────────────────────────

function GraphLegend() {
  return (
    <ul className="graph-legend" aria-label="Node kind legend">
      {(Object.keys(NODE_KIND_COLORS) as ApiGraphNode["kind"][]).map((kind) => (
        <li key={kind}>
          <span
            className="graph-legend-swatch"
            style={{ background: NODE_KIND_COLORS[kind] }}
            aria-hidden="true"
          />
          <span>{NODE_KIND_LABELS[kind]}</span>
        </li>
      ))}
    </ul>
  );
}

// ─── Filter toolbar (#150) ───────────────────────────────────────────────────

interface FilterToolbarProps {
  filter: FilterMode;
  setFilter: (mode: FilterMode) => void;
  visibleNodeCount: number;
  visibleEdgeCount: number;
}

function FilterToolbar({
  filter,
  setFilter,
  visibleNodeCount,
  visibleEdgeCount,
}: FilterToolbarProps) {
  return (
    <div
      className="graph-filter-toolbar"
      role="toolbar"
      aria-label="Knowledge graph filters"
    >
      {FILTER_OPTIONS.map(({ id, label }) => (
        <button
          key={id}
          type="button"
          className={`button ${filter === id ? "is-active" : ""}`}
          aria-pressed={filter === id}
          onClick={() => setFilter(id)}
        >
          {label}
        </button>
      ))}
      <span className="graph-filter-count" aria-live="polite">
        {visibleNodeCount} nodes · {visibleEdgeCount} edges
      </span>
    </div>
  );
}

// ─── Navigation toolbar (#292) ───────────────────────────────────────────────

interface NavigationToolbarProps {
  search: string;
  onSearchChange: (value: string) => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onReset: () => void;
  onFit: () => void;
}

/**
 * Pan/zoom helper bar. Mouse-wheel and drag work natively via the
 * NVL InteractiveNvlWrapper; these buttons are the keyboard- and
 * touch-accessible fallbacks asked for in #292. The search field
 * matches by label or id and selects the first hit, which causes the
 * canvas to fit-to-node via the existing selection effect.
 */
function NavigationToolbar({
  search,
  onSearchChange,
  onZoomIn,
  onZoomOut,
  onReset,
  onFit,
}: NavigationToolbarProps) {
  return (
    <div
      className="graph-navigation-toolbar"
      role="toolbar"
      aria-label="Knowledge graph navigation"
      data-testid="graph-navigation-toolbar"
    >
      <input
        type="search"
        className="graph-search-input"
        placeholder="Find a node…"
        aria-label="Search graph nodes"
        value={search}
        onChange={(event) => onSearchChange(event.target.value)}
      />
      <div className="graph-zoom-controls" aria-label="Zoom and fit controls">
        <button
          type="button"
          className="button"
          aria-label="Zoom in"
          onClick={onZoomIn}
        >
          +
        </button>
        <button
          type="button"
          className="button"
          aria-label="Zoom out"
          onClick={onZoomOut}
        >
          −
        </button>
        <button
          type="button"
          className="button"
          aria-label="Fit graph to viewport"
          onClick={onFit}
        >
          Fit
        </button>
        <button
          type="button"
          className="button"
          aria-label="Reset zoom and pan"
          onClick={onReset}
        >
          Reset
        </button>
      </div>
    </div>
  );
}

// ─── Details inspector (#151) ────────────────────────────────────────────────

interface DetailsInspectorProps {
  nodes: ApiGraphNode[];
  edges: ApiGraphEdge[];
  selected: Selection;
  setSelected: (selection: Selection) => void;
  selectedNode: ApiGraphNode | null;
  selectedEdge: ApiGraphEdge | null;
}

function DetailsInspector({
  nodes,
  edges,
  selected,
  setSelected,
  selectedNode,
  selectedEdge,
}: DetailsInspectorProps) {
  return (
    <section
      className="graph-inspector"
      aria-label="Knowledge graph inspector"
      data-testid="knowledge-graph-inspector"
    >
      <div className="graph-inspector-lists">
        <NodeList
          nodes={nodes}
          selected={selected}
          setSelected={setSelected}
        />
        <EdgeList
          edges={edges}
          selected={selected}
          setSelected={setSelected}
        />
      </div>
      <div className="graph-inspector-detail" data-testid="graph-inspector-detail">
        {selectedNode !== null ? (
          <NodeDetail node={selectedNode} />
        ) : selectedEdge !== null ? (
          <EdgeDetail edge={selectedEdge} />
        ) : (
          <p className="muted">
            Click a node or edge above to see its details.
          </p>
        )}
      </div>
    </section>
  );
}

function NodeList({
  nodes,
  selected,
  setSelected,
}: {
  nodes: ApiGraphNode[];
  selected: Selection;
  setSelected: (s: Selection) => void;
}) {
  if (nodes.length === 0) {
    return <p className="muted">No nodes match the current filter.</p>;
  }
  return (
    <ul
      className="graph-inspector-list"
      aria-label="Filtered nodes"
      data-testid="graph-inspector-nodes"
    >
      {nodes.map((node) => {
        const isActive = selected?.kind === "node" && selected.id === node.id;
        return (
          <li key={node.id}>
            <button
              type="button"
              className={`graph-inspector-item ${isActive ? "is-active" : ""}`}
              aria-pressed={isActive}
              onClick={() => setSelected({ kind: "node", id: node.id })}
            >
              <span
                className="graph-legend-swatch"
                style={{ background: NODE_KIND_COLORS[node.kind] ?? "#627085" }}
                aria-hidden="true"
              />
              <span className="graph-inspector-item-label">{node.label}</span>
              <span className="graph-inspector-item-kind">
                {NODE_KIND_LABELS[node.kind] ?? node.kind}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function EdgeList({
  edges,
  selected,
  setSelected,
}: {
  edges: ApiGraphEdge[];
  selected: Selection;
  setSelected: (s: Selection) => void;
}) {
  if (edges.length === 0) {
    return <p className="muted">No edges match the current filter.</p>;
  }
  return (
    <ul
      className="graph-inspector-list"
      aria-label="Filtered edges"
      data-testid="graph-inspector-edges"
    >
      {edges.map((edge) => {
        const isActive = selected?.kind === "edge" && selected.id === edge.id;
        return (
          <li key={edge.id}>
            <button
              type="button"
              className={`graph-inspector-item ${isActive ? "is-active" : ""}`}
              aria-pressed={isActive}
              onClick={() => setSelected({ kind: "edge", id: edge.id })}
            >
              <span className="graph-inspector-item-label">
                {edge.source_id} → {edge.target_id}
              </span>
              <span className="graph-inspector-item-kind">
                {EDGE_KIND_LABELS[edge.kind] ?? edge.kind}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function NodeDetail({ node }: { node: ApiGraphNode }) {
  const props = node.properties;
  const heading = stringProp(props, "heading");
  const textPreview = stringProp(props, "text_preview");
  const keywords = stringArrayProp(props, "keywords");
  const topicId = stringProp(props, "topic_id");
  const sourceCount = numberProp(props, "source_reference_count");
  const summary = stringProp(props, "summary");
  const chunkIds = stringArrayProp(props, "chunk_ids");
  const chunkCount = numberProp(props, "chunk_count");

  return (
    <dl className="graph-detail" data-testid="graph-detail-node">
      <dt>Kind</dt>
      <dd>{NODE_KIND_LABELS[node.kind] ?? node.kind}</dd>
      <dt>Label</dt>
      <dd>{node.label}</dd>
      <dt>Id</dt>
      <dd>
        <code>{node.id}</code>
      </dd>
      {heading !== null && (
        <>
          <dt>Heading</dt>
          <dd>{heading}</dd>
        </>
      )}
      {textPreview !== null && (
        <>
          <dt>Text preview</dt>
          <dd className="graph-detail-preview">{textPreview}</dd>
        </>
      )}
      {keywords.length > 0 && (
        <>
          <dt>Keywords</dt>
          <dd>{keywords.join(", ")}</dd>
        </>
      )}
      {summary !== null && (
        <>
          <dt>Summary</dt>
          <dd>{summary}</dd>
        </>
      )}
      {topicId !== null && (
        <>
          <dt>Topic</dt>
          <dd>
            <code>{topicId}</code>
          </dd>
        </>
      )}
      {chunkCount !== null && (
        <>
          <dt>Chunk count</dt>
          <dd>{chunkCount}</dd>
        </>
      )}
      {chunkIds.length > 0 && (
        <>
          <dt>Member chunks</dt>
          <dd>{chunkIds.join(", ")}</dd>
        </>
      )}
      {sourceCount !== null && (
        <>
          <dt>Source references</dt>
          <dd>{sourceCount}</dd>
        </>
      )}
    </dl>
  );
}

function EdgeDetail({ edge }: { edge: ApiGraphEdge }) {
  const props = edge.properties;
  const score = numberProp(props, "score");
  const reason = stringProp(props, "reason");
  const sharedKeywords = stringArrayProp(props, "shared_keywords");
  const sourceRefId = stringProp(props, "source_reference_id");

  return (
    <dl className="graph-detail" data-testid="graph-detail-edge">
      <dt>Relation</dt>
      <dd>{EDGE_KIND_LABELS[edge.kind] ?? edge.kind}</dd>
      <dt>From</dt>
      <dd>
        <code>{edge.source_id}</code>
      </dd>
      <dt>To</dt>
      <dd>
        <code>{edge.target_id}</code>
      </dd>
      {score !== null && (
        <>
          <dt>Score</dt>
          <dd>{score.toFixed(3)}</dd>
        </>
      )}
      {reason !== null && (
        <>
          <dt>Reason</dt>
          <dd>{reason}</dd>
        </>
      )}
      {sharedKeywords.length > 0 && (
        <>
          <dt>Shared keywords</dt>
          <dd>{sharedKeywords.join(", ")}</dd>
        </>
      )}
      {sourceRefId !== null && (
        <>
          <dt>Source reference</dt>
          <dd>
            <code>{sourceRefId}</code>
          </dd>
        </>
      )}
    </dl>
  );
}

// ─── Property accessors (typed narrowing for the dict<unknown>) ──────────────

type PropMap = ApiGraphNode["properties"];

function stringProp(props: PropMap, key: string): string | null {
  const v = props[key];
  return typeof v === "string" && v.length > 0 ? v : null;
}

function numberProp(props: PropMap, key: string): number | null {
  const v = props[key];
  return typeof v === "number" ? v : null;
}

function stringArrayProp(props: PropMap, key: string): string[] {
  const v = props[key];
  return Array.isArray(v) ? v.filter((item): item is string => typeof item === "string") : [];
}
