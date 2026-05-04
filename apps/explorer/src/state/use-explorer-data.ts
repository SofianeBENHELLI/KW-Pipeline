/**
 * Live-data orchestration for the Knowledge Explorer.
 *
 * Pulls the catalog + the Phase-1 graph projection + (lazily) the
 * raw extraction & semantic synthesis for visible documents, and
 * shapes everything onto the design's `ExplorerSnapshot` model. When
 * the backend is unreachable or empty, the hook returns the sample
 * corpus so the widget always has something to render.
 *
 * The hook keeps the network footprint polite:
 *   * One catalog walk per `apiBaseUrl` / `refreshTick`.
 *   * One global graph fetch (`/knowledge/graph`).
 *   * Per-document semantic + extraction fetches are throttled to a
 *     small concurrency cap and ride the same in-flight controller.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  getDocument,
  getExtraction,
  getKnowledgeGraph,
  getKnowledgeTaxonomy,
  getSemantic,
  listDocuments,
} from "../api/client";
import type {
  Document as ApiDocument,
  KnowledgeGraphPage,
  RawExtraction,
  SemanticDocument,
  TaxonomyResponse,
} from "../api/types";
import {
  CLUSTERS,
  type ClusterMeta,
  type ExplorerDocument,
  type ExplorerSnapshot,
  SAMPLE_SNAPSHOT,
  adaptDocContent,
  adaptDocument,
  adaptGraph,
  adaptTaxonomy,
  hashHueDeterministic,
} from "./explorer-data";

const CONTENT_FETCH_CONCURRENCY = 3;

export type DataMode = "loading" | "live" | "sample-fallback";

export interface ExplorerDataState {
  snapshot: ExplorerSnapshot;
  mode: DataMode;
  /** Last error surface (live mode only). */
  error: string | null;
  /** Bumps when a fresh refresh starts. */
  refreshing: boolean;
}

export function useExplorerData(apiBaseUrl: string, refreshTick: number): ExplorerDataState {
  const [state, setState] = useState<ExplorerDataState>({
    snapshot: SAMPLE_SNAPSHOT,
    mode: "loading",
    error: null,
    refreshing: true,
  });
  const inFlight = useRef<AbortController | null>(null);

  useEffect(() => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    setState((prev) => ({ ...prev, refreshing: true }));

    const run = async (): Promise<void> => {
      try {
        const page = await listDocuments({
          baseUrl: apiBaseUrl,
          limit: 100,
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (page.items.length === 0) {
          setState({
            snapshot: { ...SAMPLE_SNAPSHOT, corpusLabel: "Sample · backend empty" },
            mode: "sample-fallback",
            error: null,
            refreshing: false,
          });
          return;
        }

        // Pull the global graph projection — best-effort. A missing
        // knowledge layer is not an error for the Explorer (the
        // doc-level snapshot still renders without it).
        //
        // The route is cursor-paginated and the per-page cap is 200
        // (DEFAULT_GRAPH_PAGE_LIMIT). For corpora that fit inside one
        // page this is one request; for larger corpora we walk the
        // cursor up to a hard ceiling so we don't pin the UI thread
        // on a runaway corpus.
        let graphPage: KnowledgeGraphPage | null = null;
        try {
          graphPage = await fetchFullGraph(apiBaseUrl, controller.signal);
        } catch (err: unknown) {
          if (!isAbortError(err) && !(err instanceof ApiError && err.status === 503)) {
            // Tolerate — the explorer falls back to derived edges.
          }
        }

        // Pull the operator-imposed taxonomy (ADR-017). Missing /
        // unconfigured / 404 / 503 / parse error → null, in which
        // case every cluster falls back to ``source: "computed"``.
        // We log at warn so deployments missing the route surface in
        // the console but the UI keeps rendering.
        let taxonomy: TaxonomyResponse | null = null;
        try {
          taxonomy = await getKnowledgeTaxonomy({
            baseUrl: apiBaseUrl,
            signal: controller.signal,
          });
        } catch (err: unknown) {
          if (isAbortError(err)) return;
          if (err instanceof ApiError && (err.status === 404 || err.status === 503)) {
            console.warn(
              "[explorer] /knowledge/taxonomy unavailable; falling back to computed clusters.",
              err.status,
            );
          } else {
            console.warn(
              "[explorer] /knowledge/taxonomy fetch failed; falling back to computed clusters.",
              err,
            );
          }
        }

        // Fetch per-document semantic + extraction with a small ceiling.
        const semanticByDoc = new Map<string, SemanticDocument | null>();
        const extractionByDoc = new Map<string, RawExtraction | null>();
        const queue = page.items.slice(0, 50);
        for (let i = 0; i < queue.length; i += CONTENT_FETCH_CONCURRENCY) {
          if (controller.signal.aborted) return;
          const slice = queue.slice(i, i + CONTENT_FETCH_CONCURRENCY);
          await Promise.all(
            slice.map(async (doc) => {
              const latest = doc.versions.find((v) => v.id === doc.latest_version_id);
              if (!latest) return;
              const sem = await getSemantic(doc.id, latest.id, {
                baseUrl: apiBaseUrl,
                signal: controller.signal,
              }).catch((e: unknown) => {
                if (e instanceof ApiError && e.status === 404) return null;
                if (isAbortError(e)) throw e;
                return null;
              });
              const ext = await getExtraction(doc.id, latest.id, {
                baseUrl: apiBaseUrl,
                signal: controller.signal,
              }).catch((e: unknown) => {
                if (e instanceof ApiError && e.status === 404) return null;
                if (isAbortError(e)) throw e;
                return null;
              });
              semanticByDoc.set(doc.id, sem);
              extractionByDoc.set(doc.id, ext);
            }),
          );
        }
        if (controller.signal.aborted) return;

        const documents: ExplorerDocument[] = page.items.map((doc, i) =>
          adaptDocument(doc, semanticByDoc.get(doc.id) ?? null, extractionByDoc.get(doc.id) ?? null, i, page.items.length),
        );

        // Build the per-snapshot cluster catalogue.
        //
        //   1. If the operator authored a taxonomy, every category id
        //      becomes an "imposed" cluster. The seed CLUSTERS dict is
        //      ignored — the taxonomy is the source of truth.
        //   2. Otherwise, start from the seed CLUSTERS dict (for the
        //      stable sample-corpus colours) marked as "computed".
        //   3. Either way, walk the live documents and add a
        //      "computed" entry for any cluster id we haven't seen yet
        //      (auto-deduced from topic clustering / document profile).
        //      We never overwrite an imposed entry — an operator-named
        //      category that happens to share an id with a topic is the
        //      authoritative definition.
        //
        // Important: we no longer mutate the module-level CLUSTERS
        // dict. That used to leak across refreshes and across tests;
        // the snapshot now owns its own ``clusters`` field.
        const taxonomyAdapter = adaptTaxonomy(taxonomy);
        const clusters: Record<string, ClusterMeta> = {};
        if (Object.keys(taxonomyAdapter.clusters).length > 0) {
          // Imposed taxonomy wins — only operator-authored ids appear
          // in the rail.
          for (const [id, meta] of Object.entries(taxonomyAdapter.clusters)) {
            clusters[id] = meta;
          }
        } else {
          // Seed from the sample-corpus dict so familiar names + hues
          // survive the live transition. All seeds are "computed".
          for (const [id, meta] of Object.entries(CLUSTERS)) {
            clusters[id] = { ...meta, source: "computed" };
          }
        }
        for (const doc of documents) {
          if (!clusters[doc.cluster]) {
            clusters[doc.cluster] = {
              label: doc.cluster,
              hue: hashHueDeterministic(doc.cluster),
              source: "computed",
            };
          }
        }

        const graphAdapter = adaptGraph(graphPage);

        // Build chunks from extraction sections — one chunk per section
        // so the design's "click chunk → highlight in document" works.
        const chunks = page.items.flatMap((doc) => {
          const ext = extractionByDoc.get(doc.id);
          if (!ext) return [];
          return ext.sections.map((s, i) => ({
            id: s.id,
            doc: doc.id,
            label: s.heading || `Section ${i + 1}`,
            page: s.page_number ?? i + 1,
            kind: "section",
            confidence: 0.85,
            summary: s.text.slice(0, 240),
          }));
        });

        const docContent: Record<string, ReturnType<typeof adaptDocContent>> = {};
        for (const doc of page.items) {
          const ext = extractionByDoc.get(doc.id) ?? null;
          const sem = semanticByDoc.get(doc.id) ?? null;
          docContent[doc.id] = adaptDocContent(doc as ApiDocument, ext, sem);
        }

        const snapshot: ExplorerSnapshot = {
          documents,
          docEdges: graphAdapter.docEdges,
          chunks,
          concepts: graphAdapter.concepts,
          chunkConcept: graphAdapter.chunkConcept,
          conceptEdges: graphAdapter.conceptEdges,
          docContent,
          clusters,
          isSample: false,
          corpusLabel: `${page.items.length} documents`,
        };
        setState({ snapshot, mode: "live", error: null, refreshing: false });
      } catch (err: unknown) {
        if (isAbortError(err)) return;
        const message = err instanceof Error ? err.message : "Failed to load corpus";
        setState({
          snapshot: { ...SAMPLE_SNAPSHOT, corpusLabel: "Sample · backend offline" },
          mode: "sample-fallback",
          error: message,
          refreshing: false,
        });
      }
    };
    void run();
    return () => controller.abort();
  }, [apiBaseUrl, refreshTick]);

  return useMemo(() => state, [state]);
}

// Backend page cap is 200 (DEFAULT_GRAPH_PAGE_LIMIT in
// app.services.knowledge.graph_store) — the route refuses anything
// larger. For very large corpora we walk the cursor up to this
// many pages then stop. The Explorer surfaces this fact in the
// data-mode banner once we wire it.
const GRAPH_PAGE_LIMIT = 200;
const MAX_GRAPH_PAGES = 25; // 5,000 nodes — well above demo corpora.

async function fetchFullGraph(
  baseUrl: string,
  signal: AbortSignal,
): Promise<KnowledgeGraphPage | null> {
  const merged: KnowledgeGraphPage = {
    schema_version: "v0.2",
    nodes: [],
    edges: [],
    next_cursor: null,
  };
  let cursor: string | null = null;
  for (let page = 0; page < MAX_GRAPH_PAGES; page += 1) {
    if (signal.aborted) return null;
    const result: KnowledgeGraphPage = await getKnowledgeGraph({
      baseUrl,
      limit: GRAPH_PAGE_LIMIT,
      cursor: cursor ?? undefined,
      signal,
    });
    merged.nodes.push(...result.nodes);
    merged.edges.push(...result.edges);
    merged.schema_version = result.schema_version;
    if (!result.next_cursor) {
      merged.next_cursor = null;
      return merged;
    }
    cursor = result.next_cursor;
  }
  // Hit the page ceiling — return what we have plus the live cursor
  // so the consumer can flag the truncation in the UI.
  merged.next_cursor = cursor;
  return merged;
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

// Re-export the API helper that callers (App) still need.
export { getDocument };
