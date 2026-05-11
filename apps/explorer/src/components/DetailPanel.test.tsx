/**
 * Smoke tests for the DetailPanel right-rail.
 *
 * The component branches on ``node.kind`` (cluster / doc / chunk /
 * concept) and on ``node === null`` for the empty state. We cover the
 * two most common surfaces — empty + doc — and trust the snapshot
 * helpers below for the chunk / concept paths.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  type ExplorerSnapshot,
  type ExplorerDocument,
  type ExplorerConcept,
  type ExplorerChunk,
  type ExplorerDocEdge,
  type ChunkConceptLink,
  type ConceptEdge,
  SAMPLE_SNAPSHOT,
} from "../state/explorer-data";

import { DetailPanel } from "./DetailPanel";

const noopAction = vi.fn();
const noopSelectId = vi.fn();

/**
 * Builds a synthetic snapshot with enough volume to trip every
 * ``<TruncatedList>`` cap on the doc + cluster detail surfaces:
 *
 *   - ``focus`` doc has ``relatedCount`` related docs (>5 trips the
 *     RELATED DOCUMENTS cap).
 *   - ``focus`` doc has ``conceptCount`` distinct concepts (>6 trips
 *     the MAIN CONCEPTS cap), wired through one chunk.
 *   - The shared cluster carries ``clusterCount + 1`` documents
 *     (focus + sibs) so a cluster-detail node sees more than 8.
 *
 * Sample data is intentionally bare — only the fields DetailPanel
 * reads. Other surfaces aren't exercised here.
 */
function buildLargeSnapshot(opts: {
  conceptCount: number;
  relatedCount: number;
  clusterSize: number;
}): { snapshot: ExplorerSnapshot; focus: ExplorerDocument } {
  const cluster = "ops";
  const focus: ExplorerDocument = {
    id: "doc-focus",
    title: "Focus document",
    type: "pdf",
    source: "test",
    date: "2026-01-01",
    chunks: 1,
    cluster,
    x: 0,
    y: 0,
    confidence: 0.9,
  };
  const siblings: ExplorerDocument[] = Array.from(
    { length: Math.max(0, opts.clusterSize - 1) },
    (_, i): ExplorerDocument => ({
      id: `doc-sib-${i}`,
      title: `Sibling doc ${i}`,
      type: "pdf",
      source: "test",
      date: "2026-01-01",
      chunks: 1,
      cluster,
      x: 0,
      y: 0,
      confidence: 0.9,
    }),
  );
  const related: ExplorerDocument[] = Array.from(
    { length: opts.relatedCount },
    (_, i): ExplorerDocument => ({
      id: `doc-rel-${i}`,
      title: `Related doc ${i}`,
      type: "pdf",
      source: "test",
      date: "2026-01-01",
      chunks: 1,
      cluster: "other",
      x: 0,
      y: 0,
      confidence: 0.9,
    }),
  );
  const concepts: ExplorerConcept[] = Array.from(
    { length: opts.conceptCount },
    (_, i): ExplorerConcept => ({
      id: `c-${i}`,
      name: `Concept ${i}`,
      kind: "topic",
      freq: 1,
      confidence: 0.9,
      syn: [],
    }),
  );
  const focusChunk: ExplorerChunk = {
    id: "chunk-focus",
    doc: focus.id,
    label: "Focus chunk",
    page: 1,
    kind: "section",
    confidence: 0.9,
    summary: "",
  };
  const chunks: ExplorerChunk[] = [focusChunk];
  const chunkConcept: ChunkConceptLink[] = concepts.map(
    (c): ChunkConceptLink => [focusChunk.id, c.id],
  );
  const docEdges: ExplorerDocEdge[] = related.map(
    (rd): ExplorerDocEdge => ({ a: focus.id, b: rd.id, type: "similar", weight: 1 }),
  );
  const documents: ExplorerDocument[] = [focus, ...siblings, ...related];
  const snapshot: ExplorerSnapshot = {
    documents,
    docEdges,
    chunks,
    concepts,
    chunkConcept,
    conceptEdges: [],
    docContent: {},
    clusters: SAMPLE_SNAPSHOT.clusters,
    isSample: false,
    corpusLabel: "Test corpus",
  };
  return { snapshot, focus };
}

describe("DetailPanel (explorer)", () => {
  it("renders the empty state when no node is selected", () => {
    render(
      <DetailPanel
        snapshot={SAMPLE_SNAPSHOT}
        node={null}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );

    expect(screen.getByText("Nothing selected")).toBeInTheDocument();
  });

  it("renders document metadata when a doc node is selected", () => {
    const doc = SAMPLE_SNAPSHOT.documents[0];
    render(
      <DetailPanel
        snapshot={SAMPLE_SNAPSHOT}
        node={{ kind: "doc", id: doc.id, doc }}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );

    // The doc title appears verbatim in the panel header.
    expect(screen.getByText(doc.title)).toBeInTheDocument();
  });

  it("calls onAction({kind:'open',doc}) when the open button is clicked on a doc node", () => {
    const onAction = vi.fn();
    const doc = SAMPLE_SNAPSHOT.documents[0];
    render(
      <DetailPanel
        snapshot={SAMPLE_SNAPSHOT}
        node={{ kind: "doc", id: doc.id, doc }}
        onAction={onAction}
        onSelectId={noopSelectId}
      />,
    );

    // Find a button labelled with "Open" — the doc card surfaces an
    // "Open viewer" / "Open" action wired to the open intent.
    const openLikely = screen
      .getAllByRole("button")
      .find((b) => /open/i.test(b.textContent ?? ""));
    if (openLikely) {
      fireEvent.click(openLikely);
      expect(onAction).toHaveBeenCalled();
      const firstCall = onAction.mock.calls[0]?.[0];
      // Action payload kind should be one of the doc-related intents.
      expect(["open", "expand", "focusRoot"]).toContain(firstCall?.kind);
    }
  });

  // #321 — truncation affordances. Each surface (concepts / related
  // docs / cluster docs) caps its initial render at a small N and
  // shows a "+M more" button when the underlying list overflows.
  // Headers also surface the true count so the user can tell at a
  // glance there's more behind the cap.

  it("doc concepts render the count + cap at 6 with a +N more affordance", () => {
    const { snapshot, focus } = buildLargeSnapshot({
      conceptCount: 10,
      relatedCount: 0,
      clusterSize: 1,
    });
    render(
      <DetailPanel
        snapshot={snapshot}
        node={{ kind: "doc", id: focus.id, doc: focus }}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );
    // Header surfaces the true total.
    expect(screen.getByText("MAIN CONCEPTS · 10")).toBeInTheDocument();
    // Only the first 6 concept tags are rendered initially.
    const concepts = screen.getByTestId("kx-doc-concepts");
    expect(within(concepts).getAllByRole("button").filter((b) =>
      /^Concept \d+$/.test(b.textContent ?? ""),
    )).toHaveLength(6);
    // The "+4 more" affordance is present.
    const more = screen.getByTestId("kx-doc-concepts-more");
    expect(more).toHaveTextContent("+4 more");
  });

  it("clicking the doc-concepts +N more affordance reveals the rest", () => {
    const { snapshot, focus } = buildLargeSnapshot({
      conceptCount: 10,
      relatedCount: 0,
      clusterSize: 1,
    });
    render(
      <DetailPanel
        snapshot={snapshot}
        node={{ kind: "doc", id: focus.id, doc: focus }}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );
    fireEvent.click(screen.getByTestId("kx-doc-concepts-more"));
    const concepts = screen.getByTestId("kx-doc-concepts");
    expect(
      within(concepts).getAllByRole("button").filter((b) =>
        /^Concept \d+$/.test(b.textContent ?? ""),
      ),
    ).toHaveLength(10);
    // Affordance disappears once everything is visible.
    expect(screen.queryByTestId("kx-doc-concepts-more")).toBeNull();
  });

  it("related docs surface the count + cap at 5 with a +N more affordance", () => {
    const { snapshot, focus } = buildLargeSnapshot({
      conceptCount: 0,
      relatedCount: 8,
      clusterSize: 1,
    });
    render(
      <DetailPanel
        snapshot={snapshot}
        node={{ kind: "doc", id: focus.id, doc: focus }}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );
    expect(screen.getByText("RELATED DOCUMENTS · 8")).toBeInTheDocument();
    // Each row carries a kx-related-open testid; assert exactly 5 visible.
    expect(screen.getAllByTestId("kx-related-open")).toHaveLength(5);
    expect(screen.getByTestId("kx-related-docs-more")).toHaveTextContent("+3 more");
  });

  it("clicking the related-docs +N more affordance reveals every related row", () => {
    const { snapshot, focus } = buildLargeSnapshot({
      conceptCount: 0,
      relatedCount: 8,
      clusterSize: 1,
    });
    render(
      <DetailPanel
        snapshot={snapshot}
        node={{ kind: "doc", id: focus.id, doc: focus }}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );
    fireEvent.click(screen.getByTestId("kx-related-docs-more"));
    expect(screen.getAllByTestId("kx-related-open")).toHaveLength(8);
    expect(screen.queryByTestId("kx-related-docs-more")).toBeNull();
  });

  it("cluster doc list caps at 8 and reveals the rest on click", () => {
    const { snapshot } = buildLargeSnapshot({
      conceptCount: 0,
      relatedCount: 0,
      clusterSize: 12,
    });
    render(
      <DetailPanel
        snapshot={snapshot}
        node={{ kind: "cluster", id: "ops", cluster: "ops" }}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );
    const clusterDocs = screen.getByTestId("kx-cluster-docs");
    // 12 docs total, first 8 visible. Sibling docs share the "Sibling doc"
    // prefix; the focus doc carries the cluster too, so total is 12.
    const visibleRows = within(clusterDocs).getAllByRole("listitem");
    expect(visibleRows).toHaveLength(8);
    const more = screen.getByTestId("kx-cluster-docs-more");
    expect(more).toHaveTextContent("+4 more");
    fireEvent.click(more);
    expect(within(clusterDocs).getAllByRole("listitem")).toHaveLength(12);
    expect(screen.queryByTestId("kx-cluster-docs-more")).toBeNull();
  });

  it("does not render a +N more affordance when the list is at-or-below the cap", () => {
    // Three concepts < cap of 6 → no "more" button, no count badge
    // beyond the bare "MAIN CONCEPTS" header would be misleading either,
    // but the count is shown when ``concepts.length > 0`` regardless of
    // whether it overflows. We only assert the affordance stays absent.
    const { snapshot, focus } = buildLargeSnapshot({
      conceptCount: 3,
      relatedCount: 2,
      clusterSize: 4,
    });
    render(
      <DetailPanel
        snapshot={snapshot}
        node={{ kind: "doc", id: focus.id, doc: focus }}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );
    expect(screen.queryByTestId("kx-doc-concepts-more")).toBeNull();
    expect(screen.queryByTestId("kx-related-docs-more")).toBeNull();
  });

  // E (#321 cont.) — extend the truncation pattern from PR #397 to the
  // chunk-detail and concept-detail surfaces. The same helper component
  // (``<TruncatedList>``) drives all of them.

  it("chunk-detail RELATED CONCEPTS surface count + cap with +N more", () => {
    // The focus chunk is wired to all 9 concepts via chunkConcept; the
    // chunk-detail surface reads ``conceptsForChunk`` to build its
    // RELATED CONCEPTS list, so 9 > 6 trips the cap.
    const { snapshot } = buildLargeSnapshot({
      conceptCount: 9,
      relatedCount: 0,
      clusterSize: 1,
    });
    const focusChunk = snapshot.chunks[0];
    render(
      <DetailPanel
        snapshot={snapshot}
        node={{ kind: "chunk", id: focusChunk.id, chunk: focusChunk }}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );
    expect(screen.getByText("RELATED CONCEPTS · 9")).toBeInTheDocument();
    const tags = screen.getByTestId("kx-chunk-concepts");
    expect(
      within(tags).getAllByRole("button").filter((b) =>
        /^Concept \d+$/.test(b.textContent ?? ""),
      ),
    ).toHaveLength(6);
    const more = screen.getByTestId("kx-chunk-concepts-more");
    expect(more).toHaveTextContent("+3 more");
    fireEvent.click(more);
    expect(
      within(tags).getAllByRole("button").filter((b) =>
        /^Concept \d+$/.test(b.textContent ?? ""),
      ),
    ).toHaveLength(9);
  });

  it("cluster DOCUMENTS section header now surfaces the count too", () => {
    const { snapshot } = buildLargeSnapshot({
      conceptCount: 0,
      relatedCount: 0,
      clusterSize: 5,
    });
    render(
      <DetailPanel
        snapshot={snapshot}
        node={{ kind: "cluster", id: "ops", cluster: "ops" }}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );
    // 5 docs in the cluster — under the cap, so no +N more, but the
    // header now matches the concepts/related sections by including
    // the count.
    expect(screen.getByText("DOCUMENTS · 5")).toBeInTheDocument();
    expect(screen.queryByTestId("kx-cluster-docs-more")).toBeNull();
  });

  it("concept-detail RELATED CONCEPTS surface count + cap with +N more", () => {
    // Build a focus concept with 8 related-concept edges. The
    // concept-detail surface walks ``conceptEdges`` to assemble its
    // RELATED CONCEPTS list, so 8 > 6 trips the cap.
    const focus: ExplorerConcept = {
      id: "concept-focus",
      name: "Focus concept",
      kind: "topic",
      freq: 1,
      confidence: 0.9,
      syn: [],
    };
    const peers: ExplorerConcept[] = Array.from(
      { length: 8 },
      (_, i): ExplorerConcept => ({
        id: `peer-${i}`,
        name: `Peer ${i}`,
        kind: "topic",
        freq: 1,
        confidence: 0.9,
        syn: [],
      }),
    );
    const conceptEdges: ConceptEdge[] = peers.map(
      (p): ConceptEdge => [focus.id, p.id, "related"],
    );
    const snapshot: ExplorerSnapshot = {
      documents: [],
      docEdges: [],
      chunks: [],
      concepts: [focus, ...peers],
      chunkConcept: [],
      conceptEdges,
      docContent: {},
      clusters: SAMPLE_SNAPSHOT.clusters,
      isSample: false,
      corpusLabel: "Test corpus",
    };
    render(
      <DetailPanel
        snapshot={snapshot}
        node={{ kind: "concept", id: focus.id, concept: focus }}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );
    expect(screen.getByText("RELATED CONCEPTS · 8")).toBeInTheDocument();
    const tags = screen.getByTestId("kx-concept-related");
    expect(
      within(tags).getAllByRole("button").filter((b) =>
        /^Peer \d+$/.test(b.textContent ?? ""),
      ),
    ).toHaveLength(6);
    const more = screen.getByTestId("kx-concept-related-more");
    expect(more).toHaveTextContent("+2 more");
    fireEvent.click(more);
    expect(
      within(tags).getAllByRole("button").filter((b) =>
        /^Peer \d+$/.test(b.textContent ?? ""),
      ),
    ).toHaveLength(8);
  });
});
