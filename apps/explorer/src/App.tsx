/**
 * 3DX Knowledge Explorer — main shell.
 *
 * Port of the design's `app.jsx` onto the production widget stack:
 *
 *   * Header (52px): brand mark + corpus context, view tabs (Corpus
 *     Overview / Concept Map), centered search with type-ahead, and
 *     icon-button cluster on the right.
 *   * Three-column main grid (280 / 1fr / 440 in split mode, or
 *     280 / 1fr in graph-only mode): hierarchy + filters + legend on
 *     the left, GraphCanvas in the middle (with browser-style nav
 *     bar + canvas tools), document viewer + detail panel on the
 *     right.
 *   * Tweaks: theme (light/dark), density, layout (split / graph
 *     only), overlays (cluster groups, viewer panel, confidence
 *     heatmap). The design's draggable Tweaks panel is replaced by
 *     a small inline overlay button so the tile stays self-contained
 *     inside the dashboard host.
 *
 * Live data flows in via `useExplorerData`, which returns either a
 * live snapshot or the design's sample corpus when the backend is
 * unreachable / empty. The shell never special-cases the source —
 * everything below this layer reads the same `ExplorerSnapshot`.
 */

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { SessionExpiredBanner, useSessionGuard } from "../../_shared/auth";
import {
  GraphCanvas,
  type FocusRoot,
  type NodeSelection,
} from "./components/GraphCanvas";
import {
  DetailPanel,
  type DetailAction,
  type DetailNode,
} from "./components/DetailPanel";
import { ChunkListPanel } from "./components/ChunkListPanel";
import { Catalog, VersionBadges } from "./components/Catalog";
import { Icon, NAVY2 } from "./components/icons";
import { SearchResults, type SearchHit } from "./components/SearchResults";
import { SettingsModal } from "./components/SettingsModal";
import { LineageModal } from "./components/LineageModal";
import { TruncatedList } from "./components/TruncatedList";
import { RelationEvidenceDrawer } from "./components/RelationEvidenceDrawer";
import {
  clearSessionTrigger,
  getApiBaseUrl,
  getKnowledgeTaxonomy,
  setSessionTrigger,
} from "./api/client";
import { useExploreSearch } from "./state/use-explore-search";
import { useSearchFilters } from "./state/use-search-filters";
import {
  taxonomyExportFilename,
  taxonomyResponseToYaml,
  triggerYamlDownload,
} from "./state/taxonomy-export";
import type { Document as ApiDocument } from "./api/types";
import {
  CLUSTERS,
  DOC_TYPES,
  chunkById,
  chunksForConcept,
  chunksForDoc,
  conceptById,
  docById,
  filterSnapshot,
  type ExplorerDocument,
} from "./state/explorer-data";
import { useExplorerData } from "./state/use-explorer-data";

type ViewId = "corpus" | "concepts" | "catalog";

const VIEWS: Array<{
  id: ViewId;
  label: string;
  icon: "globe" | "concept" | "doc";
}> = [
  { id: "corpus", label: "Corpus Overview", icon: "globe" },
  { id: "concepts", label: "Concept Map", icon: "concept" },
  { id: "catalog", label: "Catalog", icon: "doc" },
];

const DEPTHS = [1, 2, 3, 4, 5, 10, 99] as const;
const DEPTH_LABEL = (d: number): string => (d === 99 ? "∞" : String(d));

interface Tweaks {
  theme: "light" | "dark";
  density: "sparse" | "normal" | "dense";
  showClusters: boolean;
  showViewer: boolean;
  layoutMode: "split" | "graph";
  showConfHeat: boolean;
}

const DEFAULT_TWEAKS: Tweaks = {
  theme: "light",
  density: "normal",
  showClusters: true,
  showViewer: true,
  layoutMode: "split",
  showConfHeat: false,
};

export default function App(): React.ReactElement {
  const [tweaks, setTweaks] = useState<Tweaks>(DEFAULT_TWEAKS);
  const setTweak = useCallback(<K extends keyof Tweaks>(k: K, v: Tweaks[K]) => {
    setTweaks((prev) => ({ ...prev, [k]: v }));
  }, []);

  const [apiBaseUrl] = useState<string>(() => getApiBaseUrl());
  const [refreshTick, setRefreshTick] = useState(0);
  const data = useExplorerData(apiBaseUrl, refreshTick);
  const snapshot = data.snapshot;

  // Session-expired wiring (#83 slice 3 / ADR-019 §5). Provider lives
  // at the explorer's root in index.tsx; we register the 401 trigger
  // here so any read endpoint hitting /knowledge/** or /documents/**
  // flips the shared banner.
  const session = useSessionGuard();
  useEffect(() => {
    setSessionTrigger(session.trigger);
    return () => {
      clearSessionTrigger();
    };
  }, [session.trigger]);

  // Dev stub: ``KW_AUTH_MODE=dev`` (default per #245) never returns
  // 401, so a real banner is unreachable in normal demo flows. Loading
  // the explorer with ``#force-session-expired`` flips it once for
  // visual review. Removed when bearer mode becomes the default.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.location.hash === "#force-session-expired") {
      session.trigger();
    }
  }, [session]);

  const handleSignInAgain = useCallback(() => {
    if (typeof window !== "undefined") window.location.reload();
  }, []);

  const [view, setView] = useState<ViewId>("corpus");
  const [selected, setSelected] = useState<NodeSelection | null>(null);
  const [openDocId, setOpenDocId] = useState<string | null>(null);
  const [highlightChunk, setHighlightChunk] = useState<string | null>(null);
  const [expandedClusters, setExpandedClusters] = useState<Set<string>>(
    () => new Set(),
  );
  const [expandedDocs, setExpandedDocs] = useState<Set<string>>(
    () => new Set(),
  );
  const [conceptFocus, setConceptFocus] = useState<string>("");
  const [depth, setDepth] = useState<number>(3);
  const [search, setSearch] = useState<string>("");
  // #319 — server-backed grouped semantic search. The legacy local
  // typeahead (``searchResults`` below) stays as a fallback when the
  // backend reports the route disabled (KW_VECTOR_SEARCH_DISABLED).
  // #320 partial — both filter knobs persist via the widget store.
  const {
    validatedOnly,
    scoreThreshold,
    hideDemo,
    setValidatedOnly,
    setScoreThreshold,
    setHideDemo,
  } = useSearchFilters();
  const exploreSnapshot = useExploreSearch(search, { apiBaseUrl });
  const [hovered, setHovered] = useState<string | null>(null);
  const [focusRoot, setFocusRoot] = useState<FocusRoot | null>(null);
  const [history, setHistory] = useState<Array<FocusRoot | null>>([]);
  const [forward, setForward] = useState<Array<FocusRoot | null>>([]);
  // Two-stage filter state: ``draftFilters`` tracks unconfirmed
  // checkbox toggles in the DOCUMENT TYPE rail; ``filters`` is the
  // applied state actually consumed by the cluster rail and graph.
  // The Apply button copies draft → applied. Selection alone never
  // affects the visible corpus until the operator clicks Apply.
  const [filters, setFilters] = useState<{
    types: Set<string>;
    sources: Set<string>;
  }>(() => ({ types: new Set(Object.keys(DOC_TYPES)), sources: new Set() }));
  const [draftFilters, setDraftFilters] = useState<{
    types: Set<string>;
    sources: Set<string>;
  }>(() => ({ types: new Set(Object.keys(DOC_TYPES)), sources: new Set() }));
  const filtersDirty = useMemo(() => {
    if (draftFilters.types.size !== filters.types.size) return true;
    for (const t of draftFilters.types) if (!filters.types.has(t)) return true;
    if (draftFilters.sources.size !== filters.sources.size) return true;
    for (const s of draftFilters.sources)
      if (!filters.sources.has(s)) return true;
    return false;
  }, [draftFilters, filters]);
  const applyFilters = useCallback(() => {
    setFilters({
      types: new Set(draftFilters.types),
      sources: new Set(draftFilters.sources),
    });
  }, [draftFilters]);
  const resetDraftFilters = useCallback(() => {
    setDraftFilters({
      types: new Set(filters.types),
      sources: new Set(filters.sources),
    });
  }, [filters]);
  // Sources known to the corpus — derived from live data, alphabetised
  // so the chip order is stable across refreshes. Used by the SOURCE
  // section to render one chip per source.
  const knownSources = useMemo(() => {
    const set = new Set<string>();
    for (const d of snapshot.documents) set.add(d.source);
    return [...set].sort((a, b) => a.localeCompare(b));
  }, [snapshot]);
  // Single source-of-truth doc filter. ``filters.sources`` empty means
  // "no source filter" (all sources pass) — that's the convention for
  // a dynamic chip set; ``filters.types`` is the inverse (every entry
  // in DOC_TYPES is opt-out so the empty set means "hide everything").
  // Demo/operator separation (Explorer Sprint 1). ``hideDemo`` is the
  // operator's persisted choice; ``null`` resolves to the auto rule:
  // hide demo rows only when they coexist with operator documents, so
  // a production corpus never silently mixes in fixture data while a
  // pure-demo environment stays visible right after "Load demo".
  const demoDocCount = useMemo(
    () => snapshot.documents.filter((d) => d.origin === "demo").length,
    [snapshot],
  );
  const operatorDocCount = snapshot.documents.length - demoDocCount;
  const effectiveHideDemo =
    hideDemo ?? (demoDocCount > 0 && operatorDocCount > 0);
  const docPassesFilters = useCallback(
    (d: ExplorerDocument) => {
      if (effectiveHideDemo && d.origin === "demo") return false;
      if (!filters.types.has(d.type)) return false;
      if (filters.sources.size > 0 && !filters.sources.has(d.source))
        return false;
      return true;
    },
    [filters, effectiveHideDemo],
  );
  // Snapshot projected through the active filter — fed to ``GraphCanvas``
  // so the graph view honours the DOCUMENT TYPE / SOURCE checkboxes
  // (issue #296). Raw ``snapshot`` is kept for the side panels (DocViewer,
  // DetailPanel), the source-chip rail, and lookup helpers — they need
  // the full corpus context to resolve a selected node by id.
  const filteredSnapshot = useMemo(
    () => filterSnapshot(snapshot, docPassesFilters),
    [snapshot, docPassesFilters],
  );
  const [tweaksOpen, setTweaksOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Version-history modal — lifted to App level so a click on any
  // v{N} badge (cluster rail, catalog row) or the "View history" link
  // in the DetailPanel "VERSIONS" section can mount the same modal.
  // Null when closed; carries the full ExplorerDocument so the modal
  // can render directly from ``doc.versions`` without re-fetching.
  const [lineageDocument, setLineageDocument] =
    useState<ExplorerDocument | null>(null);
  const closeLineage = useCallback(() => setLineageDocument(null), []);

  // #318 partial — when the user clicks a doc-to-doc edge in the
  // graph canvas, open the relation evidence drawer for that pair.
  // The DetailPanel keeps its own drawer for the "Related Documents"
  // list affordance; the two are independent.
  const [graphEdgeEvidence, setGraphEdgeEvidence] = useState<{
    sourceId: string;
    sourceTitle: string;
    targetId: string;
    targetTitle: string;
  } | null>(null);
  const closeGraphEdgeEvidence = useCallback(
    () => setGraphEdgeEvidence(null),
    [],
  );

  // Keep selection / open-doc / concept-focus in sync with the data
  // refresh — the sample → live transition can rename ids out from
  // under us.
  useEffect(() => {
    if (snapshot.documents.length === 0) return;
    if (openDocId === null || !docById(snapshot, openDocId)) {
      setOpenDocId(snapshot.documents[0].id);
    }
    if (conceptFocus === "" || !conceptById(snapshot, conceptFocus)) {
      setConceptFocus(snapshot.concepts[0]?.id ?? "");
    }
  }, [snapshot, openDocId, conceptFocus]);

  // Bug A — Seed `expandedClusters` with the first cluster only on
  // initial corpus load. The previous implementation lived in the
  // sync effect above and re-fired whenever ``expandedClusters.size``
  // dropped back to 0 (i.e. every time the user toggled off the
  // last expanded row). Result: the first cluster ("People & HR" in
  // the sample) appeared "stuck on" — clicks looked like a no-op
  // because the auto-init re-added it on the very next render. We
  // gate the seed with a ref so it only runs once per corpus, and
  // an empty ``expandedClusters`` afterwards is honoured as the
  // user's intent rather than overwritten.
  const clusterSeedDoneRef = useRef(false);
  useEffect(() => {
    if (clusterSeedDoneRef.current) return;
    if (snapshot.documents.length === 0) return;
    const firstCluster = snapshot.documents[0]?.cluster;
    if (firstCluster) setExpandedClusters(new Set([firstCluster]));
    clusterSeedDoneRef.current = true;
  }, [snapshot]);

  const allClusters = useMemo(() => {
    // Cluster id sourcing, in priority order:
    //   1. Every id mentioned by a live document.
    //   2. Every id in the snapshot's runtime ``clusters`` catalogue
    //      (taxonomy ids surface here even when no doc has been
    //      classified to them yet — operators want to see their
    //      categories listed even if they're empty).
    //
    // Empty *computed* clusters are filtered out so the rail never
    // shows phantom seeds against an empty corpus; *imposed*
    // (operator-authored) ones are kept even when empty so the
    // operator's tree is always visible. This is the single
    // source-of-truth for both the rail render and the
    // <expanded>/<total> counter in HIERARCHY.
    const set = new Set<string>();
    snapshot.documents.forEach((d) => set.add(d.cluster));
    Object.keys(snapshot.clusters).forEach((k) => set.add(k));
    return [...set].filter((ck) => {
      const hasDocs = snapshot.documents.some((d) => d.cluster === ck);
      const isImposed = snapshot.clusters[ck]?.source === "imposed";
      return hasDocs || isImposed;
    });
  }, [snapshot]);

  const toggleCluster = useCallback((key: string) => {
    setExpandedClusters((s) => {
      const ns = new Set(s);
      if (ns.has(key)) ns.delete(key);
      else ns.add(key);
      return ns;
    });
  }, []);
  const toggleDoc = useCallback(
    (id: string) => {
      setExpandedDocs((s) => {
        const ns = new Set(s);
        if (ns.has(id)) ns.delete(id);
        else ns.add(id);
        return ns;
      });
      const d = docById(snapshot, id);
      if (d) {
        setExpandedClusters((s) => {
          const ns = new Set(s);
          ns.add(d.cluster);
          return ns;
        });
      }
    },
    [snapshot],
  );
  const expandAllClusters = useCallback(
    () => setExpandedClusters(new Set(allClusters)),
    [allClusters],
  );
  const collapseAll = useCallback(() => {
    setExpandedClusters(new Set());
    setExpandedDocs(new Set());
  }, []);
  const expandAllDocs = useCallback(() => {
    setExpandedClusters(new Set(allClusters));
    setExpandedDocs(new Set(snapshot.documents.map((d) => d.id)));
  }, [allClusters, snapshot]);
  const collapseAllDocs = useCallback(() => setExpandedDocs(new Set()), []);

  const handleSelect = useCallback(
    (n: NodeSelection) => {
      if (n.kind === "cluster") {
        setSelected(n);
      } else if (n.kind === "doc") {
        setSelected(n);
        if (n.doc) setOpenDocId(n.doc.id);
        else setOpenDocId(n.id);
        setHighlightChunk(null);
      } else if (n.kind === "chunk") {
        setSelected(n);
        if (n.chunk) {
          setOpenDocId(n.chunk.doc);
          setHighlightChunk(n.chunk.id);
        }
      } else if (n.kind === "concept") {
        setSelected(n);
        if (view === "concepts" && n.concept) setConceptFocus(n.concept.id);
      }
    },
    [view],
  );

  const selectById = useCallback(
    (id: string, kind: "doc" | "chunk" | "concept") => {
      if (kind === "doc") {
        const d = docById(snapshot, id);
        if (d) handleSelect({ kind: "doc", id, doc: d });
      } else if (kind === "chunk") {
        const c = chunkById(snapshot, id);
        if (c) handleSelect({ kind: "chunk", id, chunk: c });
      } else if (kind === "concept") {
        const k = conceptById(snapshot, id);
        if (k) {
          handleSelect({ kind: "concept", id, concept: k });
          if (view !== "concepts") setView("concepts");
          setConceptFocus(id);
        }
      }
    },
    [snapshot, handleSelect, view],
  );

  const focusFromNode = useCallback(
    (n: NodeSelection) => {
      let label = "";
      if (n.kind === "cluster")
        label = CLUSTERS[n.cluster ?? n.id]?.label ?? n.id;
      else if (n.kind === "doc")
        label = (n.doc ?? docById(snapshot, n.id))?.title ?? n.id;
      else if (n.kind === "chunk")
        label = (n.chunk ?? chunkById(snapshot, n.id))?.label ?? n.id;
      else if (n.kind === "concept")
        label = (n.concept ?? conceptById(snapshot, n.id))?.name ?? n.id;
      const next: FocusRoot = {
        kind: n.kind,
        id: n.kind === "cluster" ? (n.cluster ?? n.id) : n.id,
        label,
      };
      setHistory((h) => [...h, focusRoot]);
      setForward([]);
      setFocusRoot(next);
    },
    [focusRoot, snapshot],
  );

  const goBack = useCallback(() => {
    setHistory((h) => {
      if (!h.length) return h;
      const prev = h[h.length - 1];
      setForward((f) => [focusRoot, ...f]);
      setFocusRoot(prev);
      return h.slice(0, -1);
    });
  }, [focusRoot]);

  const goForward = useCallback(() => {
    setForward((f) => {
      if (!f.length) return f;
      const nxt = f[0];
      setHistory((h) => [...h, focusRoot]);
      setFocusRoot(nxt);
      return f.slice(1);
    });
  }, [focusRoot]);

  const goHome = useCallback(() => {
    if (focusRoot) setHistory((h) => [...h, focusRoot]);
    setForward([]);
    setFocusRoot(null);
  }, [focusRoot]);

  const focusFromNodeRef = useRef(focusFromNode);
  focusFromNodeRef.current = focusFromNode;
  useEffect(() => {
    const onFocus = (e: Event) => {
      const detail = (e as CustomEvent<NodeSelection>).detail;
      focusFromNodeRef.current(detail);
    };
    window.addEventListener("kx-focus-root", onFocus);
    return () => window.removeEventListener("kx-focus-root", onFocus);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
      if (e.altKey && e.key === "ArrowLeft") {
        e.preventDefault();
        goBack();
      }
      if (e.altKey && e.key === "ArrowRight") {
        e.preventDefault();
        goForward();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goBack, goForward]);

  // ── Item #5 — URL hash deep linking ─────────────────────────────────
  // The hash format is `#<kind>/<id>` (e.g. `#doc/d4`, `#concept/k2`,
  // `#chunk/c4.1`). On mount, parse the hash and select the matching
  // entity. On selection change, write the hash back so the URL is
  // shareable and "Back" in the host browser tab restores the focus.
  const hashAppliedRef = useRef(false);
  useEffect(() => {
    if (hashAppliedRef.current) return;
    if (snapshot.documents.length === 0) return;
    const raw = typeof window !== "undefined" ? window.location.hash : "";
    if (!raw || raw.length < 2) {
      hashAppliedRef.current = true;
      return;
    }
    const [kind, ...rest] = raw.slice(1).split("/");
    const id = rest.join("/");
    if (!kind || !id) {
      hashAppliedRef.current = true;
      return;
    }
    if (kind === "doc" && docById(snapshot, id)) {
      selectById(id, "doc");
      hashAppliedRef.current = true;
    } else if (kind === "chunk" && chunkById(snapshot, id)) {
      selectById(id, "chunk");
      hashAppliedRef.current = true;
    } else if (kind === "concept" && conceptById(snapshot, id)) {
      selectById(id, "concept");
      hashAppliedRef.current = true;
    } else if (kind === "catalog") {
      // Catalog deep-link — open the Catalog tab. If an id is
      // provided AND the doc exists in the snapshot, also select it
      // so the DetailPanel renders the matching row's metadata. The
      // catalog component itself handles "doc not in snapshot but
      // returned by /documents" by updating the selection through
      // its own click handler when the user lands on the row.
      setView("catalog");
      if (id && docById(snapshot, id)) selectById(id, "doc");
      hashAppliedRef.current = true;
    }
  }, [snapshot, selectById]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    // Catalog tab — write ``#catalog/<doc_id>`` (or just ``#catalog``
    // when nothing is selected) so refreshing the page returns to the
    // tab. This mirrors the doc/chunk/concept hash format below.
    let next: string;
    if (view === "catalog") {
      const docId = selected?.kind === "doc" ? selected.id : "";
      next = docId ? `#catalog/${docId}` : "#catalog";
    } else if (selected) {
      next = `#${selected.kind}/${selected.id}`;
    } else {
      return;
    }
    if (window.location.hash === next) return;
    // history.replaceState avoids polluting the browser back-stack on
    // every selection — the focus history inside the app already covers
    // intra-corpus navigation. The hash is purely a deep-link write.
    try {
      window.history.replaceState(null, "", next);
    } catch {
      // Some hosts disable replaceState — fall back silently.
    }
  }, [selected, view]);

  const handleAction = useCallback(
    (action: DetailAction) => {
      if (action.kind === "focusRoot") {
        const n = action.node;
        focusFromNode({
          kind: n.kind,
          id: n.id,
          doc: n.doc,
          chunk: n.chunk,
          concept: n.concept,
          cluster: n.cluster,
        });
        return;
      }
      if (action.kind === "expand") {
        setExpandedDocs((s) => {
          const ns = new Set(s);
          ns.add(action.doc.id);
          return ns;
        });
        setExpandedClusters((s) => {
          const ns = new Set(s);
          ns.add(action.doc.cluster);
          return ns;
        });
      } else if (action.kind === "open") {
        setOpenDocId(action.doc.id);
        setHighlightChunk(null);
      } else if (action.kind === "highlight") {
        setOpenDocId(action.chunk.doc);
        setHighlightChunk(action.chunk.id);
      } else if (action.kind === "evidence") {
        const evidence = chunksForConcept(snapshot, action.concept.id);
        if (evidence[0]) {
          setOpenDocId(evidence[0].doc);
          setHighlightChunk(evidence[0].id);
        }
      }
    },
    [focusFromNode, snapshot],
  );

  const openDoc = openDocId ? (docById(snapshot, openDocId) ?? null) : null;
  // ``navChunk`` (prev/next within the open doc's chunks) was wired
  // to the old DocViewer's chunk navigator. The new ChunkListPanel
  // exposes the chunks as a clickable list directly, so the explicit
  // navigator and its ``docChunks`` derived value are gone too.
  // ``chunksForDoc`` is still imported for ``ChunkListPanel``'s own
  // internal use.

  const searchResults = useMemo(() => {
    if (!search) return null;
    const q = search.toLowerCase();
    // Item #5: index extends past `title`/`name` into description-
    // adjacent fields so a query like "compliance" matches a doc by
    // its source/cluster, a chunk by its summary/kind, or a concept
    // by its synonyms/kind.
    const docMatch = (d: (typeof snapshot.documents)[number]): boolean =>
      d.title.toLowerCase().includes(q) ||
      d.cluster.toLowerCase().includes(q) ||
      d.source.toLowerCase().includes(q) ||
      d.type.toLowerCase().includes(q);
    const conceptMatch = (k: (typeof snapshot.concepts)[number]): boolean =>
      k.name.toLowerCase().includes(q) ||
      k.kind.toLowerCase().includes(q) ||
      k.syn.some((s) => s.toLowerCase().includes(q));
    const chunkMatch = (c: (typeof snapshot.chunks)[number]): boolean =>
      c.label.toLowerCase().includes(q) ||
      c.summary.toLowerCase().includes(q) ||
      c.kind.toLowerCase().includes(q);
    // #321 — keep the full filtered arrays so the local-fallback
    // popover can surface the true match count in each header and
    // the ``<TruncatedList>`` below can offer "+N more" instead of
    // silently capping at four. The cap is applied at render time,
    // not here.
    return {
      docs: snapshot.documents.filter(docMatch),
      concepts: snapshot.concepts.filter(conceptMatch),
      chunks: snapshot.chunks.filter(chunkMatch),
    };
  }, [search, snapshot]);

  const detailNode: DetailNode | null = useMemo(() => {
    if (!selected) return null;
    return {
      kind: selected.kind,
      id: selected.id,
      doc:
        selected.kind === "doc"
          ? (selected.doc ?? docById(snapshot, selected.id))
          : undefined,
      chunk:
        selected.kind === "chunk"
          ? (selected.chunk ?? chunkById(snapshot, selected.id))
          : undefined,
      concept:
        selected.kind === "concept"
          ? (selected.concept ?? conceptById(snapshot, selected.id))
          : undefined,
      cluster:
        selected.kind === "cluster"
          ? (selected.cluster ?? selected.id)
          : undefined,
    };
  }, [selected, snapshot]);

  const stats = useMemo(
    () => ({
      docs: snapshot.documents.length,
      chunks: snapshot.documents.reduce((a, d) => a + d.chunks, 0),
      concepts: snapshot.concepts.length,
      edges: snapshot.docEdges.length,
    }),
    [snapshot],
  );

  const visibleNodeCount = useMemo<number | "—">(() => {
    if (view !== "corpus") return "—";
    let n = 0;
    allClusters.forEach((ck) => {
      if (expandedClusters.has(ck)) {
        const docs = snapshot.documents
          .filter((d) => d.cluster === ck)
          .filter(docPassesFilters);
        n += docs.length;
        docs.forEach((d) => {
          if (expandedDocs.has(d.id)) n += chunksForDoc(snapshot, d.id).length;
        });
      } else {
        n += 1;
      }
    });
    return n;
  }, [view, expandedClusters, expandedDocs, allClusters, snapshot]);

  const breadCrumb = useMemo(() => {
    if (view === "corpus") {
      return `${expandedClusters.size}/${allClusters.length} CLUSTERS · ${expandedDocs.size}/${snapshot.documents.length} DOCS EXPANDED`;
    }
    return `CONCEPT · ${conceptById(snapshot, conceptFocus)?.name ?? "—"}`;
  }, [
    view,
    expandedClusters,
    expandedDocs,
    allClusters,
    snapshot,
    conceptFocus,
  ]);

  const breadCrumbSelected = useMemo(() => {
    if (!detailNode) return null;
    if (detailNode.doc) return detailNode.doc.title;
    if (detailNode.chunk) return detailNode.chunk.label;
    if (detailNode.concept) return detailNode.concept.name;
    if (detailNode.cluster)
      return CLUSTERS[detailNode.cluster]?.label ?? detailNode.cluster;
    return null;
  }, [detailNode]);

  const reset = useCallback(() => {
    setSelected(null);
    setSearch("");
    setFocusRoot(null);
    setHistory([]);
    setForward([]);
  }, []);

  const refresh = useCallback(() => setRefreshTick((n) => n + 1), []);

  return (
    <div
      className={
        "kx-app" +
        " kx-theme-" +
        tweaks.theme +
        " kx-density-" +
        tweaks.density +
        (tweaks.layoutMode === "graph" ? " kx-layout-graph" : "") +
        (!tweaks.showViewer ? " kx-no-viewer" : "")
      }
    >
      <SessionExpiredBanner
        visible={session.expired}
        onSignIn={handleSignInAgain}
        className="kx-session-expired"
      />
      <header className="kx-header">
        <div className="kx-brand">
          <div className="kx-brand-mark">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke={tweaks.theme === "dark" ? "#9CC0F0" : "#0E2A4A"}
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <circle cx="6" cy="7" r="2" />
              <circle cx="18" cy="7" r="2" />
              <circle cx="12" cy="17" r="2" />
              <path d="M8 8l8 0M8 8l3 8M16 8l-3 8" />
            </svg>
          </div>
          <div className="kx-brand-t">
            <div className="kx-brand-name">KNOWLEDGE EXPLORER</div>
            <div className="kx-brand-sub">
              3DEXPERIENCE Widget · Corpus: <b>{snapshot.corpusLabel}</b>
            </div>
          </div>
        </div>

        <div className="kx-views" role="tablist" aria-label="Explorer views">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              className={"kx-vbtn" + (view === v.id ? " kx-on" : "")}
              onClick={() => setView(v.id)}
              role="tab"
              aria-selected={view === v.id}
            >
              <Icon name={v.icon} size={14} />
              {v.label}
            </button>
          ))}
        </div>

        <div className="kx-search-wrap">
          <Icon name="search" size={14} stroke="#5C7AA8" />
          <input
            className="kx-search"
            placeholder="Search documents, chunks, concepts…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search corpus"
          />
          {search && (
            <button
              className="kx-search-x"
              onClick={() => setSearch("")}
              aria-label="Clear search"
            >
              <Icon name="x" size={11} />
            </button>
          )}
          {/* #319 — server-backed multi-kind grouped search.
              Replaces the legacy local typeahead as the primary
              affordance. The local ``searchResults`` memo still
              powers the disabled-fallback popover below so users
              don't lose all search when Phase 3 is off. */}
          {exploreSnapshot.state !== "disabled" && (
            <SearchResults
              snapshot={exploreSnapshot}
              validatedOnly={validatedOnly}
              onToggleValidated={setValidatedOnly}
              scoreThreshold={scoreThreshold}
              onChangeScoreThreshold={setScoreThreshold}
              onPick={(hit: SearchHit) => {
                if (hit.kind === "doc" && hit.documentId) {
                  selectById(hit.documentId, "doc");
                } else if (hit.kind === "chunk") {
                  selectById(hit.id, "chunk");
                } else if (hit.kind === "topic") {
                  // Topic hits open the strongest contributing chunk
                  // so the user lands on source evidence — concept
                  // ≠ topic in the seeded model, so we route through
                  // the chunk path (which highlights the paragraph
                  // in DocViewer) rather than the concept path.
                  // Topics without evidence chunks fall back to the
                  // parent document; topics with neither are a soft
                  // no-op (rare — embedding-only matches).
                  if (hit.chunkId) {
                    selectById(hit.chunkId, "chunk");
                  } else if (hit.documentId) {
                    selectById(hit.documentId, "doc");
                  }
                }
                setSearch("");
              }}
            />
          )}
          {exploreSnapshot.state === "disabled" && searchResults && (
            <div className="kx-search-pop" data-testid="kx-search-pop-local">
              <div className="kx-search-toolbar">
                <span className="kx-mute">
                  Vector search disabled — showing local matches.
                </span>
                {/* #321 — surface the same operator remediation hint
                    the server-backed SearchResults disabled state shows,
                    so an operator who lands on this fallback knows what
                    to flip to wire the real semantic search instead of
                    assuming local-only is the design. */}
                <span
                  className="kx-mute kx-search-disabled-hint"
                  data-testid="kx-search-disabled-hint"
                >
                  Set <code>KW_KNOWLEDGE_LAYER_ENABLED=true</code> and{" "}
                  <code>VOYAGE_API_KEY</code> to enable.
                </span>
              </div>
              <SearchSection
                title="DOCUMENTS"
                items={searchResults.docs}
                initialCount={4}
                testIdPrefix="kx-search-local-docs"
                onPick={(d) => {
                  selectById(d.id, "doc");
                  setSearch("");
                }}
                render={(d) => (
                  <>
                    <span
                      className="kx-doc-chip kx-sm"
                      style={{ background: DOC_TYPES[d.type]?.color ?? "#888" }}
                    >
                      {DOC_TYPES[d.type]?.short ?? "DOC"}
                    </span>
                    {d.title}
                  </>
                )}
              />
              <SearchSection
                title="CONCEPTS"
                items={searchResults.concepts}
                initialCount={4}
                testIdPrefix="kx-search-local-concepts"
                onPick={(k) => {
                  selectById(k.id, "concept");
                  // Mirror PR #396's topic→evidence-chunk routing
                  // for the server-backed SearchResults: when a
                  // concept is picked, also surface the first
                  // chunk that mentions it in DocViewer so the
                  // user lands on source evidence in addition to
                  // the concept-bubble surface. The same pattern
                  // already drives the DetailPanel "evidence"
                  // intent above (action.kind === "evidence").
                  const evidence = chunksForConcept(snapshot, k.id)[0];
                  if (evidence) {
                    setOpenDocId(evidence.doc);
                    setHighlightChunk(evidence.id);
                  }
                  setSearch("");
                }}
                render={(k) => (
                  <>
                    <Icon name="concept" size={10} />
                    {k.name} <span className="kx-mute kx-mono">×{k.freq}</span>
                  </>
                )}
              />
              <SearchSection
                title="CHUNKS"
                items={searchResults.chunks}
                initialCount={4}
                testIdPrefix="kx-search-local-chunks"
                onPick={(c) => {
                  selectById(c.id, "chunk");
                  setSearch("");
                }}
                render={(c) => (
                  <>
                    <Icon name="chunk" size={10} />
                    <span className="kx-mono">{c.id}</span> {c.label}
                  </>
                )}
              />
              {searchResults.docs.length +
                searchResults.concepts.length +
                searchResults.chunks.length ===
                0 && (
                <div className="kx-search-empty">
                  No matches for &quot;<b>{search}</b>&quot;
                </div>
              )}
            </div>
          )}
        </div>

        <div className="kx-head-right">
          <button
            className="kx-icon-btn"
            title="Refresh corpus"
            onClick={refresh}
            aria-label="Refresh corpus"
            aria-busy={data.refreshing}
          >
            <Icon name="reset" size={15} />
          </button>
          <button
            className="kx-icon-btn"
            title="Tweaks"
            onClick={() => setTweaksOpen((o) => !o)}
            aria-label="Tweaks"
            aria-expanded={tweaksOpen}
          >
            <Icon name="settings" size={15} />
          </button>
          <button
            className="kx-icon-btn"
            title="Knowledge Forge settings"
            onClick={() => setSettingsOpen(true)}
            aria-label="Knowledge Forge settings"
            data-testid="explorer-settings-launcher"
          >
            <Icon name="info" size={15} />
          </button>
          <div className="kx-user" aria-hidden="true">
            EM
          </div>
        </div>
      </header>

      <div className="kx-main">
        <aside className="kx-left" aria-label="Filters and hierarchy">
          <Section title="CORPUS">
            <div className="kx-stats">
              <Stat n={stats.docs} l="Documents" />
              <Stat n={stats.chunks} l="Chunks" />
              <Stat n={stats.concepts} l="Concepts" />
              <Stat n={stats.edges} l="Relations" />
            </div>
            {data.mode === "sample-fallback" && (
              <div className="kx-warn" title={data.error ?? ""}>
                <Icon name="warn" size={11} /> Sample data — backend unreachable
              </div>
            )}
            {data.mode === "empty" && (
              <div className="kx-empty-banner">
                <Icon name="info" size={11} /> No documents yet. Upload one via
                Orbital to populate the corpus.
              </div>
            )}
            {/* #321 — knowledge-graph cursor walk hit its page
                ceiling, so the graph + relation views are
                rendering only the first slice of nodes/edges. The
                catalog rail (documents) is unaffected — it has its
                own pagination via the Catalog component. Tell the
                operator so they don't trust the graph view as
                complete on a large corpus. */}
            {snapshot.graphTruncated && (
              <div
                className="kx-empty-banner"
                data-testid="kx-graph-truncated-banner"
                title="Refine clusters or use search to focus on specific documents"
              >
                <Icon name="info" size={11} /> Graph truncated — showing first
                ~5,000 nodes
              </div>
            )}
            {/* Sprint 1 — demo/operator separation. Only rendered
                when demo rows exist; the chip states what is hidden
                or shown and flips the persisted preference. */}
            {demoDocCount > 0 && (
              <button
                type="button"
                className={
                  "kx-demo-visibility" +
                  (effectiveHideDemo ? " kx-demo-visibility--hidden" : "")
                }
                data-testid="kx-demo-visibility-toggle"
                onClick={() => setHideDemo(!effectiveHideDemo)}
                title={
                  effectiveHideDemo
                    ? `${demoDocCount} demo document(s) hidden — click to show them alongside operator data`
                    : `${demoDocCount} demo document(s) visible — click to hide them`
                }
              >
                <Icon name={effectiveHideDemo ? "info" : "warn"} size={11} />{" "}
                Demo data ·{" "}
                {effectiveHideDemo
                  ? `hidden (${demoDocCount})`
                  : `shown (${demoDocCount})`}
              </button>
            )}
          </Section>

          <Section title="HIERARCHY">
            <div className="kx-hier">
              <div className="kx-hier-row">
                <span className="kx-hier-l">Clusters</span>
                <span className="kx-mono kx-mute">
                  {expandedClusters.size}/{allClusters.length}
                </span>
                <button
                  className="kx-mini-btn"
                  onClick={expandAllClusters}
                  title="Expand all clusters"
                >
                  <Icon name="expand" size={11} />
                </button>
                <button
                  className="kx-mini-btn"
                  onClick={collapseAll}
                  title="Collapse all"
                >
                  <Icon name="collapse" size={11} />
                </button>
              </div>
              <div className="kx-hier-row">
                <span className="kx-hier-l">Documents</span>
                <span className="kx-mono kx-mute">
                  {expandedDocs.size}/{snapshot.documents.length}
                </span>
                <button
                  className="kx-mini-btn"
                  onClick={expandAllDocs}
                  title="Expand all docs to chunks"
                >
                  <Icon name="expand" size={11} />
                </button>
                <button
                  className="kx-mini-btn"
                  onClick={collapseAllDocs}
                  title="Collapse all docs"
                >
                  <Icon name="collapse" size={11} />
                </button>
              </div>
            </div>
            <div className="kx-cluster-list">
              {allClusters.map((ck) => {
                const isExp = expandedClusters.has(ck);
                const docs = snapshot.documents
                  .filter((d) => d.cluster === ck)
                  .filter(docPassesFilters);
                // Prefer the snapshot's runtime catalogue (which carries
                // the live ``source`` flag from the taxonomy fetch);
                // fall back to the seed CLUSTERS dict for label/hue if
                // the runtime catalogue doesn't know this id (e.g. a
                // document classified to a stale category that's not
                // in the current taxonomy).
                const meta = snapshot.clusters[ck] ?? CLUSTERS[ck];
                const source = meta?.source ?? "computed";
                // Hide computed clusters that the corpus doesn't
                // actually populate — they'd be visual noise. But
                // *imposed* (operator-authored) categories are kept
                // even when empty: the operator wants to see their
                // tree, and "no docs classified to this category"
                // is itself useful information.
                if (docs.length === 0 && source !== "imposed") return null;
                return (
                  <div key={ck} className="kx-cl-block">
                    <div
                      className={"kx-cl-row" + (isExp ? " kx-on" : "")}
                      onClick={() => toggleCluster(ck)}
                    >
                      <Icon
                        name={isExp ? "chevron-down" : "chevron-right"}
                        size={11}
                      />
                      <span
                        className="kx-cl-dot"
                        style={{
                          background: `oklch(0.78 0.06 ${meta?.hue ?? 200})`,
                        }}
                      />
                      <span className="kx-cl-name">{meta?.label ?? ck}</span>
                      <ClusterSourceBadge source={source} />
                      <span className="kx-mono kx-mute">{docs.length}</span>
                    </div>
                    {isExp && (
                      <div className="kx-cl-docs">
                        {docs.map((d) => {
                          const dExp = expandedDocs.has(d.id);
                          return (
                            <div
                              key={d.id}
                              className={
                                "kx-cl-doc" +
                                (selected?.id === d.id ? " kx-on" : "")
                              }
                              onClick={() => selectById(d.id, "doc")}
                            >
                              <button
                                className="kx-toggle"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  toggleDoc(d.id);
                                }}
                                title={
                                  dExp ? "Collapse chunks" : "Expand to chunks"
                                }
                                aria-label={
                                  dExp ? "Collapse chunks" : "Expand to chunks"
                                }
                              >
                                <Icon name={dExp ? "minus" : "plus"} size={9} />
                              </button>
                              <span
                                className="kx-doc-chip kx-sm"
                                style={{
                                  background:
                                    DOC_TYPES[d.type]?.color ?? "#888",
                                }}
                              >
                                {DOC_TYPES[d.type]?.short ?? "DOC"}
                              </span>
                              <span className="kx-cl-doc-t">
                                {truncate(d.title, 22)}
                              </span>
                              <VersionBadges
                                versionCount={d.versionCount ?? 1}
                                latest={d.latestVersion ?? 1}
                                onOpenLineage={() => setLineageDocument(d)}
                              />
                              <span className="kx-mono kx-mute">
                                {d.chunks}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </Section>

          <Section
            title="GRAPH DEPTH"
            right={
              <span className="kx-mono kx-mute">
                {DEPTH_LABEL(depth)} {depth === 99 ? "" : "lvl"}
              </span>
            }
          >
            <div className="kx-depth">
              {DEPTHS.map((d) => (
                <button
                  key={d}
                  className={"kx-depth-btn" + (depth === d ? " kx-on" : "")}
                  onClick={() => setDepth(d)}
                >
                  {DEPTH_LABEL(d)}
                </button>
              ))}
            </div>
          </Section>

          <Section title="TAXONOMY">
            <TaxonomyImporter apiBaseUrl={apiBaseUrl} />
          </Section>

          <Section title="DOCUMENT TYPE">
            {Object.entries(DOC_TYPES).map(([k, t]) => (
              <FilterRow
                key={k}
                checked={draftFilters.types.has(k)}
                onChange={() =>
                  setDraftFilters((f) => {
                    const ns = new Set(f.types);
                    if (ns.has(k)) ns.delete(k);
                    else ns.add(k);
                    return { ...f, types: ns };
                  })
                }
                color={t.color}
                label={t.label}
                count={snapshot.documents.filter((d) => d.type === k).length}
              />
            ))}
          </Section>

          <Section
            title="SOURCE"
            right={
              draftFilters.sources.size > 0 ? (
                <button
                  type="button"
                  className="kx-mini-btn"
                  onClick={() =>
                    setDraftFilters((f) => ({ ...f, sources: new Set() }))
                  }
                  title="Clear all source filters (show every source)"
                  aria-label="Clear source filters"
                >
                  ×
                </button>
              ) : null
            }
          >
            {knownSources.length === 0 ? (
              <p className="kx-tax-status" style={{ marginTop: 0 }}>
                No sources known yet — they appear here once documents are
                uploaded.
              </p>
            ) : (
              knownSources.map((src) => (
                <FilterRow
                  key={src}
                  checked={draftFilters.sources.has(src)}
                  onChange={() =>
                    setDraftFilters((f) => {
                      const ns = new Set(f.sources);
                      if (ns.has(src)) ns.delete(src);
                      else ns.add(src);
                      return { ...f, sources: ns };
                    })
                  }
                  label={src}
                  count={
                    snapshot.documents.filter((d) => d.source === src).length
                  }
                />
              ))
            )}
            <div className="kx-filter-actions">
              <button
                type="button"
                className={"kx-filter-apply" + (filtersDirty ? " kx-on" : "")}
                disabled={!filtersDirty}
                onClick={applyFilters}
                title={filtersDirty ? "Apply selection" : "No changes to apply"}
              >
                Apply Filter
              </button>
              <button
                type="button"
                className="kx-filter-reset"
                disabled={!filtersDirty}
                onClick={resetDraftFilters}
                title="Revert pending changes"
              >
                Reset
              </button>
            </div>
            <p className="kx-filter-hint">
              {draftFilters.sources.size === 0
                ? "No source selected — every source is shown."
                : `${draftFilters.sources.size} source${draftFilters.sources.size === 1 ? "" : "s"} selected (others hidden).`}
            </p>
          </Section>

          <Section title="LEGEND">
            <div className="kx-legend">
              <div className="kx-leg-row">
                <span className="kx-leg-shape kx-leg-cluster" />
                Cluster
              </div>
              <div className="kx-leg-row">
                <span className="kx-leg-shape kx-leg-doc" />
                Document
              </div>
              <div className="kx-leg-row">
                <span className="kx-leg-shape kx-leg-chunk" />
                Chunk
              </div>
              <div className="kx-leg-row">
                <span className="kx-leg-shape kx-leg-concept" />
                Concept
              </div>
            </div>
          </Section>
        </aside>

        <section className="kx-center">
          <div className="kx-canvas-bar">
            <div className="kx-navbar">
              <button
                className="kx-nav-btn"
                disabled={!history.length}
                onClick={goBack}
                title="Back"
                aria-label="Back"
              >
                <Icon name="chevron-left" size={14} />
              </button>
              <button
                className="kx-nav-btn"
                disabled={!forward.length}
                onClick={goForward}
                title="Forward"
                aria-label="Forward"
              >
                <Icon name="chevron-right" size={14} />
              </button>
              <button
                className="kx-nav-btn"
                onClick={goHome}
                title="Home (full corpus)"
                aria-label="Home"
              >
                <Icon name="home" size={14} />
              </button>
              <div className="kx-nav-addr">
                {focusRoot ? (
                  <>
                    <Icon name="focus" size={11} />
                    <span className="kx-nav-label">{focusRoot.label}</span>
                    <span className="kx-mono kx-mute">
                      depth {DEPTH_LABEL(depth)}
                    </span>
                  </>
                ) : (
                  <>
                    <Icon name="globe" size={11} />
                    <span className="kx-nav-label">Full corpus</span>
                  </>
                )}
              </div>
              {history.length > 0 && (
                <span className="kx-mono kx-mute kx-nav-trail">
                  {history.length} back
                </span>
              )}
            </div>
            <div className="kx-bread">
              <Icon name="compass" size={12} stroke={NAVY2} />
              <span className="kx-mono">{breadCrumb}</span>
              {breadCrumbSelected && <span className="kx-bread-sep">›</span>}
              {breadCrumbSelected && (
                <span className="kx-bread-cur">{breadCrumbSelected}</span>
              )}
            </div>
            <div className="kx-canvas-tools">
              <button
                className="kx-tool-btn"
                onClick={expandAllClusters}
                title="Expand all clusters"
              >
                <Icon name="expand" size={12} />
                Expand clusters
              </button>
              <button
                className="kx-tool-btn"
                onClick={collapseAll}
                title="Collapse all"
              >
                <Icon name="collapse" size={12} />
                Collapse all
              </button>
              <button
                className="kx-tool-btn"
                onClick={() => setTweak("showConfHeat", !tweaks.showConfHeat)}
                aria-pressed={tweaks.showConfHeat}
              >
                <Icon name="warn" size={12} />
                Confidence
              </button>
              {/*
                Bug C — side-panel toggle is now first-class on the
                main toolbar. It used to live in the Tweaks overlay
                (gear menu), where users couldn't find it. The state
                key (`tweaks.showViewer`) is unchanged so existing
                consumers keep working.
              */}
              <button
                className="kx-tool-btn"
                onClick={() => setTweak("showViewer", !tweaks.showViewer)}
                aria-pressed={tweaks.showViewer}
                title="Toggle side panel"
                aria-label="Toggle side panel"
                data-testid="kx-toggle-side-panel"
              >
                <Icon name="layers" size={12} />
                Side panel
              </button>
              {focusRoot && (
                <span className="kx-pill kx-pill-focus">
                  <Icon name="focus" size={11} />
                  Focused: {focusRoot.label}
                  <button
                    onClick={goHome}
                    title="Clear focus"
                    aria-label="Clear focus"
                  >
                    <Icon name="x" size={10} />
                  </button>
                </span>
              )}
              <button
                className="kx-tool-btn"
                onClick={reset}
                title="Reset selection and focus"
              >
                <Icon name="reset" size={12} />
                Reset
              </button>
            </div>
          </div>

          <div className="kx-canvas">
            {view === "catalog" ? (
              <Catalog
                apiBaseUrl={apiBaseUrl}
                refreshTick={refreshTick}
                hideDemo={effectiveHideDemo}
                selectedId={selected?.kind === "doc" ? selected.id : null}
                focusedDocumentId={
                  focusRoot?.kind === "doc" ? focusRoot.id : null
                }
                onFocusDocument={(apiDoc: ApiDocument) => {
                  // Scope the catalog (and the graph) to this single
                  // document via the existing focusRoot mechanism so
                  // the "Focused: <doc>" chip + back/home navigation
                  // already wired in App.tsx remain the only way out.
                  const known = docById(snapshot, apiDoc.id);
                  focusFromNode({
                    kind: "doc",
                    id: apiDoc.id,
                    doc: known ?? undefined,
                  });
                }}
                onOpenLineage={(apiDoc: ApiDocument) => {
                  // Prefer the explorer snapshot copy so we get a
                  // fully-fledged ExplorerDocument (cluster, hue,
                  // confidence, …); fall back to a thin projection
                  // built straight from the API row so the modal can
                  // still render its versions list when the doc isn't
                  // in the snapshot (e.g. paginated past page 1).
                  const known = docById(snapshot, apiDoc.id);
                  if (known) {
                    setLineageDocument(known);
                    return;
                  }
                  setLineageDocument(toLineageOnlyDocument(apiDoc));
                }}
                onSelectDocument={(apiDoc: ApiDocument) => {
                  // Try to resolve via the snapshot first so we get the
                  // existing ExplorerDocument shape (cluster, hue, etc.).
                  // If the catalog returned a doc the snapshot doesn't
                  // know about (e.g. paginated past the first page), we
                  // still surface a minimal selection so the DetailPanel
                  // renders the doc's title + status from the API row.
                  const known = docById(snapshot, apiDoc.id);
                  if (known) {
                    handleSelect({ kind: "doc", id: apiDoc.id, doc: known });
                  } else {
                    setSelected({ kind: "doc", id: apiDoc.id });
                    setOpenDocId(apiDoc.id);
                    setHighlightChunk(null);
                  }
                }}
              />
            ) : (
              <GraphCanvas
                snapshot={filteredSnapshot}
                view={view === "corpus" ? "corpus" : "concepts"}
                selectedId={selected?.id ?? null}
                conceptFocus={conceptFocus}
                onSelect={handleSelect}
                onToggleCluster={toggleCluster}
                onToggleDoc={toggleDoc}
                expandedClusters={expandedClusters}
                expandedDocs={expandedDocs}
                showClusters={tweaks.showClusters && view === "corpus"}
                showConfHeat={tweaks.showConfHeat}
                theme={tweaks.theme}
                depth={depth}
                hoveredId={hovered}
                onHover={setHovered}
                search={search}
                focusRoot={focusRoot}
                onEdgeClick={(sourceId, targetId) => {
                  // Resolve titles from the unfiltered snapshot so the
                  // drawer header reads correctly even when the user
                  // has narrowed the cluster rail.
                  const src = docById(snapshot, sourceId);
                  const tgt = docById(snapshot, targetId);
                  if (!src || !tgt) return;
                  setGraphEdgeEvidence({
                    sourceId,
                    sourceTitle: src.title,
                    targetId,
                    targetTitle: tgt.title,
                  });
                }}
              />
            )}
            <div className="kx-readonly">
              <Icon name="shield" size={11} stroke="#3F8E60" /> READ-ONLY
            </div>
            <div className="kx-canvas-foot">
              <span className="kx-foot-l">VIEW</span>{" "}
              <span className="kx-mono">{view.toUpperCase()}</span>
              <span className="kx-foot-l">·</span>
              <span className="kx-foot-l">NODES</span>{" "}
              <span className="kx-mono">{visibleNodeCount}</span>
              <span className="kx-foot-l">·</span>
              <span className="kx-foot-l">DEPTH</span>{" "}
              <span className="kx-mono">{DEPTH_LABEL(depth)}</span>
            </div>
          </div>
        </section>

        {tweaks.layoutMode === "split" && tweaks.showViewer && (
          <aside className="kx-right" aria-label="Document viewer and details">
            <ChunkListPanel
              snapshot={snapshot}
              doc={openDoc}
              highlightChunkId={highlightChunk}
              hoveredChunkId={hovered}
              // Match the original DocViewer paragraph-click
              // semantics: clicking a chunk pins it via
              // ``highlightChunk`` so the row + the DetailPanel
              // chunks-list both light up, but the document stays
              // the active selection (DetailPanel keeps showing
              // the doc-view, not the chunk-view). The graph's
              // bidirectional cross-highlight rides on
              // ``hoveredId`` instead — see ``onHoverChunk`` below.
              onSelectChunk={setHighlightChunk}
              onHoverChunk={setHovered}
            />
            <DetailPanel
              snapshot={snapshot}
              node={detailNode}
              onAction={handleAction}
              onSelectId={selectById}
              highlightChunkId={highlightChunk}
              onOpenLineage={setLineageDocument}
            />
          </aside>
        )}
      </div>

      {tweaksOpen && (
        <TweaksOverlay
          tweaks={tweaks}
          setTweak={setTweak}
          onClose={() => setTweaksOpen(false)}
        />
      )}
      <SettingsModal
        apiBaseUrl={apiBaseUrl}
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        bumpRefresh={refresh}
      />
      {lineageDocument && (
        <LineageModal document={lineageDocument} onClose={closeLineage} />
      )}
      {graphEdgeEvidence && (
        <RelationEvidenceDrawer
          sourceDocumentId={graphEdgeEvidence.sourceId}
          sourceTitle={graphEdgeEvidence.sourceTitle}
          targetDocumentId={graphEdgeEvidence.targetId}
          targetTitle={graphEdgeEvidence.targetTitle}
          onClose={closeGraphEdgeEvidence}
        />
      )}
    </div>
  );
}

/**
 * Build a minimal ``ExplorerDocument`` shape from a catalog API row so
 * the lineage modal can render its versions list even when the doc
 * isn't part of the snapshot (e.g. catalog paginated past the first
 * page). The modal only consumes ``title`` + ``versions`` + the
 * ``versionCount`` / ``latestVersion`` shorthand, so we don't need to
 * fabricate cluster / x / y / confidence values that wouldn't be
 * shown.
 */
function toLineageOnlyDocument(apiDoc: ApiDocument): ExplorerDocument {
  const latest =
    apiDoc.versions.find((v) => v.id === apiDoc.latest_version_id) ??
    apiDoc.versions[apiDoc.versions.length - 1];
  return {
    id: apiDoc.id,
    title: apiDoc.original_filename,
    type: "doc",
    source: "—",
    date: (latest?.created_at ?? apiDoc.created_at).slice(0, 10),
    chunks: 0,
    cluster: "unknown",
    x: 0,
    y: 0,
    confidence: 0,
    versionCount: apiDoc.versions.length,
    latestVersion: latest?.version_number ?? 1,
    versions: apiDoc.versions.map((v) => ({
      id: v.id,
      versionNumber: v.version_number,
      status: v.status,
      createdAt: v.created_at,
      filename: v.filename,
      sha256: v.sha256,
      duplicateOfVersionId: v.duplicate_of_version_id,
    })),
  };
}

// ─── Sub-components (kept inline because they're tightly coupled) ────────────

const Section: React.FC<{
  title: string;
  right?: React.ReactNode;
  children: React.ReactNode;
}> = ({ title, right, children }) => (
  <div className="kx-sec">
    <div className="kx-sec-t">
      <span>{title}</span>
      {right}
    </div>
    <div className="kx-sec-b">{children}</div>
  </div>
);

const Stat: React.FC<{ n: number; l: string }> = ({ n, l }) => (
  <div className="kx-stat">
    <div className="kx-stat-n">{n}</div>
    <div className="kx-stat-l">{l}</div>
  </div>
);

/**
 * Tiny "auto" / "imposed" badge next to a cluster row in the left
 * rail. Surfaces ADR-017's hybrid taxonomy provenance:
 *
 *   * ``auto`` — auto-deduced from topic clustering, gray italic.
 *   * ``imposed`` — operator-authored YAML category, brand-coloured.
 *
 * Tooltip explains the source so the affordance is discoverable
 * without opening a modal.
 */
const ClusterSourceBadge: React.FC<{ source: "computed" | "imposed" }> = ({
  source,
}) => {
  const isImposed = source === "imposed";
  return (
    <span
      className={
        "kx-cl-src" + (isImposed ? " kx-cl-src-imposed" : " kx-cl-src-auto")
      }
      title={
        isImposed
          ? "Imposed by operator (YAML taxonomy)"
          : "Auto-deduced from topic clustering"
      }
      data-testid={isImposed ? "kx-cl-src-imposed" : "kx-cl-src-auto"}
      aria-label={
        isImposed ? "imposed taxonomy category" : "auto-deduced cluster"
      }
    >
      {isImposed ? "imposed" : "auto"}
    </span>
  );
};

/**
 * Operator-facing taxonomy importer (left rail).
 *
 * Accepts the four format families that cover ~all knowledge-org
 * taxonomy interchange in the wild:
 *
 *   * SKOS (W3C) in RDF/XML (.rdf, .xml) or Turtle (.ttl) — the
 *     canonical web standard for thesauri / classification schemes.
 *   * SKOS in JSON-LD (.jsonld, .json) — same data model, JSON shape.
 *   * Plain CSV (.csv) — pragmatic fallback (id, parent_id, label).
 *   * YAML (.yaml, .yml) — what KW-Pipeline already speaks natively
 *     via ``KW_TAXONOMY_PATH`` (ADR-017).
 *
 * The backend POST endpoint is not yet wired (read-only via the YAML
 * file today). The importer currently surfaces the parsed file's
 * basics so the operator can sanity-check before the import endpoint
 * lands. Once a server-side import route exists this component swaps
 * its alert for a fetch.
 */
const TAXONOMY_ACCEPT = ".yaml,.yml,.csv,.ttl,.rdf,.xml,.json,.jsonld";

interface TaxonomyImporterProps {
  apiBaseUrl: string;
}

const TaxonomyImporter: React.FC<TaxonomyImporterProps> = ({ apiBaseUrl }) => {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [status, setStatus] = useState<string>("");
  const [exporting, setExporting] = useState<boolean>(false);

  const onPick = useCallback(() => {
    inputRef.current?.click();
  }, []);

  const onFile = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    const standard =
      ext === "ttl" || ext === "rdf"
        ? "SKOS / RDF"
        : ext === "jsonld" || ext === "json"
          ? "SKOS / JSON-LD"
          : ext === "csv"
            ? "CSV"
            : ext === "yaml" || ext === "yml"
              ? "YAML"
              : "unknown";
    setStatus(
      `Selected: ${file.name} (${(file.size / 1024).toFixed(1)} KiB, ${standard}). ` +
        `Backend import endpoint not yet wired — copy the file to KW_TAXONOMY_PATH on the API host for now.`,
    );
    // Reset so picking the same file twice still fires onChange.
    if (inputRef.current) inputRef.current.value = "";
  }, []);

  // Export the merged hybrid taxonomy (imposed + computed) as YAML
  // matching the loader's accepted format. Issue #298 (scope a) — no
  // backend changes; reads ``GET /knowledge/taxonomy``, serializes to
  // YAML, and triggers a browser download. The ``source`` field on
  // each category is preserved so the operator can see at-a-glance
  // which entries were operator-authored vs auto-deduced.
  const onExport = useCallback(async () => {
    setExporting(true);
    setStatus("");
    try {
      const response = await getKnowledgeTaxonomy({ baseUrl: apiBaseUrl });
      const yamlText = taxonomyResponseToYaml(response);
      triggerYamlDownload(yamlText, taxonomyExportFilename());
      setStatus(
        `Exported ${response.categories.length} top-level categories to YAML.`,
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setStatus(`Export failed: ${message}`);
    } finally {
      setExporting(false);
    }
  }, [apiBaseUrl]);

  return (
    <div className="kx-tax-importer">
      <p className="kx-tax-help">
        Import an external taxonomy. Supported standards: SKOS (RDF/XML, Turtle,
        JSON-LD), CSV, YAML. Export rounds the current merged taxonomy back to
        YAML for offline editing.
      </p>
      <div className="kx-tax-actions">
        <button type="button" className="kx-tax-btn" onClick={onPick}>
          Import taxonomy…
        </button>
        <button
          type="button"
          className="kx-tax-btn"
          onClick={onExport}
          disabled={exporting}
          aria-label="Export taxonomy as YAML"
          data-testid="kx-tax-export-btn"
        >
          {exporting ? "Exporting…" : "Export taxonomy"}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept={TAXONOMY_ACCEPT}
          onChange={onFile}
          style={{ display: "none" }}
          aria-label="Import taxonomy file"
        />
      </div>
      {status && <p className="kx-tax-status">{status}</p>}
    </div>
  );
};

interface FilterRowProps {
  checked: boolean;
  onChange: () => void;
  color?: string;
  label: string;
  count: number;
}

const FilterRow: React.FC<FilterRowProps> = ({
  checked,
  onChange,
  color,
  label,
  count,
}) => (
  <label className="kx-filter">
    <span className={"kx-check" + (checked ? " kx-on" : "")} onClick={onChange}>
      {checked && <Icon name="check" size={9} stroke="white" />}
    </span>
    {color && <span className="kx-swatch" style={{ background: color }} />}
    <span className="kx-filter-l">{label}</span>
    <span className="kx-mono kx-mute">{count}</span>
  </label>
);

interface SearchSectionProps<T> {
  title: string;
  items: T[];
  onPick: (item: T) => void;
  render: (item: T) => React.ReactNode;
  /**
   * #321 — when set, the section caps its initial render at this
   * many rows and shows a ``+N more`` button (via ``<TruncatedList>``)
   * for the remainder. The header also surfaces the true total in
   * ``TITLE · N`` form so the user knows how many matches exist
   * even before expanding. Omit to render the full list with a
   * bare title (the pre-#321 behaviour).
   */
  initialCount?: number;
  /** Optional testid prefix for the "+N more" button. */
  testIdPrefix?: string;
}

function SearchSection<T extends object>({
  title,
  items,
  onPick,
  render,
  initialCount,
  testIdPrefix,
}: SearchSectionProps<T>): React.ReactElement | null {
  if (!items.length) return null;
  const renderRow = (it: T, i: number): React.ReactNode => (
    <div key={i} className="kx-search-row" onClick={() => onPick(it)}>
      {render(it)}
    </div>
  );
  return (
    <div className="kx-search-sec">
      <div className="kx-search-h">
        {title}
        {initialCount !== undefined && ` · ${items.length}`}
      </div>
      {initialCount === undefined ? (
        items.map(renderRow)
      ) : (
        <TruncatedList
          items={items}
          initialCount={initialCount}
          renderItem={renderRow}
          testIdPrefix={testIdPrefix}
        />
      )}
    </div>
  );
}

// ─── Tweaks overlay (replaces the design's draggable panel) ──────────────────

interface TweaksOverlayProps {
  tweaks: Tweaks;
  setTweak: <K extends keyof Tweaks>(k: K, v: Tweaks[K]) => void;
  onClose: () => void;
}

const TweaksOverlay: React.FC<TweaksOverlayProps> = ({
  tweaks,
  setTweak,
  onClose,
}) => (
  <div
    role="dialog"
    aria-label="Tweaks"
    style={{
      position: "fixed",
      right: 16,
      bottom: 16,
      width: 280,
      background: "var(--bg)",
      border: "1px solid var(--line)",
      borderRadius: 8,
      boxShadow: "0 12px 40px rgba(0,0,0,0.18)",
      zIndex: 50,
      padding: 14,
    }}
  >
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: 10,
      }}
    >
      <div className="kx-mono kx-mute" style={{ letterSpacing: "0.12em" }}>
        TWEAKS
      </div>
      <button
        className="kx-icon-btn"
        onClick={onClose}
        aria-label="Close tweaks"
      >
        <Icon name="x" size={12} />
      </button>
    </div>
    <TweaksRow label="Theme">
      <SegmentedRadio
        value={tweaks.theme}
        onChange={(v) => setTweak("theme", v as Tweaks["theme"])}
        options={[
          { value: "light", label: "Light" },
          { value: "dark", label: "Dark" },
        ]}
      />
    </TweaksRow>
    <TweaksRow label="Density">
      <SegmentedRadio
        value={tweaks.density}
        onChange={(v) => setTweak("density", v as Tweaks["density"])}
        options={[
          { value: "sparse", label: "Sparse" },
          { value: "normal", label: "Normal" },
          { value: "dense", label: "Dense" },
        ]}
      />
    </TweaksRow>
    <TweaksRow label="Layout">
      <SegmentedRadio
        value={tweaks.layoutMode}
        onChange={(v) => setTweak("layoutMode", v as Tweaks["layoutMode"])}
        options={[
          { value: "split", label: "Split" },
          { value: "graph", label: "Graph only" },
        ]}
      />
    </TweaksRow>
    <TweaksRow label="Cluster halos">
      <Toggle
        value={tweaks.showClusters}
        onChange={(v) => setTweak("showClusters", v)}
      />
    </TweaksRow>
    {/*
      Bug C — "Viewer panel" used to live here. It's now a first-class
      toolbar button (data-testid="kx-toggle-side-panel"). The
      `tweaks.showViewer` state key is intentionally unchanged so this
      relocation is UI-only.
    */}
    <TweaksRow label="Confidence heatmap">
      <Toggle
        value={tweaks.showConfHeat}
        onChange={(v) => setTweak("showConfHeat", v)}
      />
    </TweaksRow>
  </div>
);

const TweaksRow: React.FC<{ label: string; children: React.ReactNode }> = ({
  label,
  children,
}) => (
  <div
    style={{
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      padding: "6px 0",
      fontSize: 12,
    }}
  >
    <span style={{ color: "var(--ink-2)" }}>{label}</span>
    {children}
  </div>
);

interface SegmentedRadioProps {
  value: string;
  onChange: (v: string) => void;
  options: Array<{ value: string; label: string }>;
}

const SegmentedRadio: React.FC<SegmentedRadioProps> = ({
  value,
  onChange,
  options,
}) => (
  <div className="kx-views" style={{ padding: 1 }}>
    {options.map((o) => (
      <button
        key={o.value}
        className={"kx-vbtn" + (value === o.value ? " kx-on" : "")}
        onClick={() => onChange(o.value)}
        style={{ fontSize: 11, padding: "4px 8px" }}
      >
        {o.label}
      </button>
    ))}
  </div>
);

const Toggle: React.FC<{ value: boolean; onChange: (v: boolean) => void }> = ({
  value,
  onChange,
}) => (
  <button
    className={"kx-check" + (value ? " kx-on" : "")}
    onClick={() => onChange(!value)}
    aria-pressed={value}
    style={{ width: 28, height: 16, borderRadius: 9 }}
  >
    {value && <Icon name="check" size={10} stroke="white" />}
  </button>
);

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
