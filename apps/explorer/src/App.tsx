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

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { GraphCanvas, type FocusRoot, type NodeSelection } from "./components/GraphCanvas";
import { DetailPanel, type DetailAction, type DetailNode } from "./components/DetailPanel";
import { DocViewer } from "./components/DocViewer";
import { Icon, NAVY2 } from "./components/icons";
import { getApiBaseUrl } from "./api/client";
import {
  CLUSTERS,
  DOC_TYPES,
  chunkById,
  chunksForConcept,
  chunksForDoc,
  conceptById,
  docById,
} from "./state/explorer-data";
import { useExplorerData } from "./state/use-explorer-data";

const VIEWS: Array<{ id: "corpus" | "concepts"; label: string; icon: "globe" | "concept" }> = [
  { id: "corpus", label: "Corpus Overview", icon: "globe" },
  { id: "concepts", label: "Concept Map", icon: "concept" },
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

  const [view, setView] = useState<"corpus" | "concepts">("corpus");
  const [selected, setSelected] = useState<NodeSelection | null>(null);
  const [openDocId, setOpenDocId] = useState<string | null>(null);
  const [highlightChunk, setHighlightChunk] = useState<string | null>(null);
  const [expandedClusters, setExpandedClusters] = useState<Set<string>>(() => new Set());
  const [expandedDocs, setExpandedDocs] = useState<Set<string>>(() => new Set());
  const [conceptFocus, setConceptFocus] = useState<string>("");
  const [depth, setDepth] = useState<number>(3);
  const [search, setSearch] = useState<string>("");
  const [hovered, setHovered] = useState<string | null>(null);
  const [focusRoot, setFocusRoot] = useState<FocusRoot | null>(null);
  const [history, setHistory] = useState<Array<FocusRoot | null>>([]);
  const [forward, setForward] = useState<Array<FocusRoot | null>>([]);
  const [filters, setFilters] = useState<{ types: Set<string>; sources: Set<string> }>(
    () => ({ types: new Set(Object.keys(DOC_TYPES)), sources: new Set() }),
  );
  const [tweaksOpen, setTweaksOpen] = useState(false);

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
    if (expandedClusters.size === 0) {
      const firstCluster = snapshot.documents[0]?.cluster;
      if (firstCluster) setExpandedClusters(new Set([firstCluster]));
    }
  }, [snapshot, openDocId, conceptFocus, expandedClusters.size]);

  const allClusters = useMemo(() => {
    const set = new Set<string>();
    snapshot.documents.forEach((d) => set.add(d.cluster));
    Object.keys(CLUSTERS).forEach((k) => set.add(k));
    return [...set];
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
  const expandAllClusters = useCallback(() => setExpandedClusters(new Set(allClusters)), [allClusters]);
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
      if (n.kind === "cluster") label = CLUSTERS[n.cluster ?? n.id]?.label ?? n.id;
      else if (n.kind === "doc") label = (n.doc ?? docById(snapshot, n.id))?.title ?? n.id;
      else if (n.kind === "chunk") label = (n.chunk ?? chunkById(snapshot, n.id))?.label ?? n.id;
      else if (n.kind === "concept") label = (n.concept ?? conceptById(snapshot, n.id))?.name ?? n.id;
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
    }
  }, [snapshot, selectById]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!selected) return;
    const next = `#${selected.kind}/${selected.id}`;
    if (window.location.hash === next) return;
    // history.replaceState avoids polluting the browser back-stack on
    // every selection — the focus history inside the app already covers
    // intra-corpus navigation. The hash is purely a deep-link write.
    try {
      window.history.replaceState(null, "", next);
    } catch {
      // Some hosts disable replaceState — fall back silently.
    }
  }, [selected]);

  const handleAction = useCallback(
    (action: DetailAction) => {
      if (action.kind === "focusRoot") {
        const n = action.node;
        focusFromNode({ kind: n.kind, id: n.id, doc: n.doc, chunk: n.chunk, concept: n.concept, cluster: n.cluster });
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

  const openDoc = openDocId ? docById(snapshot, openDocId) ?? null : null;
  const docChunks = openDoc ? chunksForDoc(snapshot, openDoc.id) : [];
  const navChunk = useCallback(
    (delta: number) => {
      if (!docChunks.length) return;
      const idx = highlightChunk ? docChunks.findIndex((c) => c.id === highlightChunk) : -1;
      const next = (idx + delta + docChunks.length) % docChunks.length;
      const target = docChunks[next];
      setHighlightChunk(target.id);
      setSelected({ kind: "chunk", id: target.id, chunk: target });
    },
    [docChunks, highlightChunk],
  );

  const searchResults = useMemo(() => {
    if (!search) return null;
    const q = search.toLowerCase();
    // Item #5: index extends past `title`/`name` into description-
    // adjacent fields so a query like "compliance" matches a doc by
    // its source/cluster, a chunk by its summary/kind, or a concept
    // by its synonyms/kind.
    const docMatch = (d: typeof snapshot.documents[number]): boolean =>
      d.title.toLowerCase().includes(q) ||
      d.cluster.toLowerCase().includes(q) ||
      d.source.toLowerCase().includes(q) ||
      d.type.toLowerCase().includes(q);
    const conceptMatch = (k: typeof snapshot.concepts[number]): boolean =>
      k.name.toLowerCase().includes(q) ||
      k.kind.toLowerCase().includes(q) ||
      k.syn.some((s) => s.toLowerCase().includes(q));
    const chunkMatch = (c: typeof snapshot.chunks[number]): boolean =>
      c.label.toLowerCase().includes(q) ||
      c.summary.toLowerCase().includes(q) ||
      c.kind.toLowerCase().includes(q);
    return {
      docs: snapshot.documents.filter(docMatch).slice(0, 4),
      concepts: snapshot.concepts.filter(conceptMatch).slice(0, 4),
      chunks: snapshot.chunks.filter(chunkMatch).slice(0, 4),
    };
  }, [search, snapshot]);

  const detailNode: DetailNode | null = useMemo(() => {
    if (!selected) return null;
    return {
      kind: selected.kind,
      id: selected.id,
      doc: selected.kind === "doc" ? selected.doc ?? docById(snapshot, selected.id) : undefined,
      chunk: selected.kind === "chunk" ? selected.chunk ?? chunkById(snapshot, selected.id) : undefined,
      concept: selected.kind === "concept" ? selected.concept ?? conceptById(snapshot, selected.id) : undefined,
      cluster: selected.kind === "cluster" ? selected.cluster ?? selected.id : undefined,
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
        const docs = snapshot.documents.filter((d) => d.cluster === ck);
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
  }, [view, expandedClusters, expandedDocs, allClusters, snapshot, conceptFocus]);

  const breadCrumbSelected = useMemo(() => {
    if (!detailNode) return null;
    if (detailNode.doc) return detailNode.doc.title;
    if (detailNode.chunk) return detailNode.chunk.label;
    if (detailNode.concept) return detailNode.concept.name;
    if (detailNode.cluster) return CLUSTERS[detailNode.cluster]?.label ?? detailNode.cluster;
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
        (tweaks.layoutMode === "graph" ? " kx-layout-graph" : "")
      }
    >
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
            <button className="kx-search-x" onClick={() => setSearch("")} aria-label="Clear search">
              <Icon name="x" size={11} />
            </button>
          )}
          {searchResults && (
            <div className="kx-search-pop">
              <SearchSection
                title="DOCUMENTS"
                items={searchResults.docs}
                onPick={(d) => {
                  selectById(d.id, "doc");
                  setSearch("");
                }}
                render={(d) => (
                  <>
                    <span className="kx-doc-chip kx-sm" style={{ background: DOC_TYPES[d.type]?.color ?? "#888" }}>
                      {DOC_TYPES[d.type]?.short ?? "DOC"}
                    </span>
                    {d.title}
                  </>
                )}
              />
              <SearchSection
                title="CONCEPTS"
                items={searchResults.concepts}
                onPick={(k) => {
                  selectById(k.id, "concept");
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
              {searchResults.docs.length + searchResults.concepts.length + searchResults.chunks.length === 0 && (
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
          </Section>

          <Section title="HIERARCHY">
            <div className="kx-hier">
              <div className="kx-hier-row">
                <span className="kx-hier-l">Clusters</span>
                <span className="kx-mono kx-mute">
                  {expandedClusters.size}/{allClusters.length}
                </span>
                <button className="kx-mini-btn" onClick={expandAllClusters} title="Expand all clusters">
                  <Icon name="expand" size={11} />
                </button>
                <button className="kx-mini-btn" onClick={collapseAll} title="Collapse all">
                  <Icon name="collapse" size={11} />
                </button>
              </div>
              <div className="kx-hier-row">
                <span className="kx-hier-l">Documents</span>
                <span className="kx-mono kx-mute">
                  {expandedDocs.size}/{snapshot.documents.length}
                </span>
                <button className="kx-mini-btn" onClick={expandAllDocs} title="Expand all docs to chunks">
                  <Icon name="expand" size={11} />
                </button>
                <button className="kx-mini-btn" onClick={collapseAllDocs} title="Collapse all docs">
                  <Icon name="collapse" size={11} />
                </button>
              </div>
            </div>
            <div className="kx-cluster-list">
              {allClusters.map((ck) => {
                const isExp = expandedClusters.has(ck);
                const docs = snapshot.documents.filter((d) => d.cluster === ck);
                if (docs.length === 0) return null;
                return (
                  <div key={ck} className="kx-cl-block">
                    <div className={"kx-cl-row" + (isExp ? " kx-on" : "")} onClick={() => toggleCluster(ck)}>
                      <Icon name={isExp ? "chevron-down" : "chevron-right"} size={11} />
                      <span
                        className="kx-cl-dot"
                        style={{ background: `oklch(0.78 0.06 ${CLUSTERS[ck]?.hue ?? 200})` }}
                      />
                      <span className="kx-cl-name">{CLUSTERS[ck]?.label ?? ck}</span>
                      <span className="kx-mono kx-mute">{docs.length}</span>
                    </div>
                    {isExp && (
                      <div className="kx-cl-docs">
                        {docs.map((d) => {
                          const dExp = expandedDocs.has(d.id);
                          return (
                            <div
                              key={d.id}
                              className={"kx-cl-doc" + (selected?.id === d.id ? " kx-on" : "")}
                              onClick={() => selectById(d.id, "doc")}
                            >
                              <button
                                className="kx-toggle"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  toggleDoc(d.id);
                                }}
                                title={dExp ? "Collapse chunks" : "Expand to chunks"}
                                aria-label={dExp ? "Collapse chunks" : "Expand to chunks"}
                              >
                                <Icon name={dExp ? "minus" : "plus"} size={9} />
                              </button>
                              <span
                                className="kx-doc-chip kx-sm"
                                style={{ background: DOC_TYPES[d.type]?.color ?? "#888" }}
                              >
                                {DOC_TYPES[d.type]?.short ?? "DOC"}
                              </span>
                              <span className="kx-cl-doc-t">{truncate(d.title, 22)}</span>
                              <span className="kx-mono kx-mute">{d.chunks}</span>
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

          <Section title="DOCUMENT TYPE">
            {Object.entries(DOC_TYPES).map(([k, t]) => (
              <FilterRow
                key={k}
                checked={filters.types.has(k)}
                onChange={() =>
                  setFilters((f) => {
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
                    <span className="kx-mono kx-mute">depth {DEPTH_LABEL(depth)}</span>
                  </>
                ) : (
                  <>
                    <Icon name="globe" size={11} />
                    <span className="kx-nav-label">Full corpus</span>
                  </>
                )}
              </div>
              {history.length > 0 && (
                <span className="kx-mono kx-mute kx-nav-trail">{history.length} back</span>
              )}
            </div>
            <div className="kx-bread">
              <Icon name="compass" size={12} stroke={NAVY2} />
              <span className="kx-mono">{breadCrumb}</span>
              {breadCrumbSelected && <span className="kx-bread-sep">›</span>}
              {breadCrumbSelected && <span className="kx-bread-cur">{breadCrumbSelected}</span>}
            </div>
            <div className="kx-canvas-tools">
              <button className="kx-tool-btn" onClick={expandAllClusters} title="Expand all clusters">
                <Icon name="expand" size={12} />
                Expand clusters
              </button>
              <button className="kx-tool-btn" onClick={collapseAll} title="Collapse all">
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
              {focusRoot && (
                <span className="kx-pill kx-pill-focus">
                  <Icon name="focus" size={11} />
                  Focused: {focusRoot.label}
                  <button onClick={goHome} title="Clear focus" aria-label="Clear focus">
                    <Icon name="x" size={10} />
                  </button>
                </span>
              )}
              <button className="kx-tool-btn" onClick={reset} title="Reset selection and focus">
                <Icon name="reset" size={12} />
                Reset
              </button>
            </div>
          </div>

          <div className="kx-canvas">
            <GraphCanvas
              snapshot={snapshot}
              view={view}
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
            />
            <div className="kx-readonly">
              <Icon name="shield" size={11} stroke="#3F8E60" /> READ-ONLY
            </div>
            <div className="kx-canvas-foot">
              <span className="kx-foot-l">VIEW</span> <span className="kx-mono">{view.toUpperCase()}</span>
              <span className="kx-foot-l">·</span>
              <span className="kx-foot-l">NODES</span> <span className="kx-mono">{visibleNodeCount}</span>
              <span className="kx-foot-l">·</span>
              <span className="kx-foot-l">DEPTH</span> <span className="kx-mono">{DEPTH_LABEL(depth)}</span>
            </div>
          </div>
        </section>

        {tweaks.layoutMode === "split" && tweaks.showViewer && (
          <aside className="kx-right" aria-label="Document viewer and details">
            <DocViewer
              snapshot={snapshot}
              doc={openDoc}
              highlightChunkId={highlightChunk}
              onPrevChunk={() => navChunk(-1)}
              onNextChunk={() => navChunk(1)}
            />
            <DetailPanel snapshot={snapshot} node={detailNode} onAction={handleAction} onSelectId={selectById} />
          </aside>
        )}
      </div>

      {tweaksOpen && <TweaksOverlay tweaks={tweaks} setTweak={setTweak} onClose={() => setTweaksOpen(false)} />}
    </div>
  );
}

// ─── Sub-components (kept inline because they're tightly coupled) ────────────

const Section: React.FC<{ title: string; right?: React.ReactNode; children: React.ReactNode }> = ({
  title,
  right,
  children,
}) => (
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

interface FilterRowProps {
  checked: boolean;
  onChange: () => void;
  color?: string;
  label: string;
  count: number;
}

const FilterRow: React.FC<FilterRowProps> = ({ checked, onChange, color, label, count }) => (
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
}

function SearchSection<T extends object>({ title, items, onPick, render }: SearchSectionProps<T>): React.ReactElement | null {
  if (!items.length) return null;
  return (
    <div className="kx-search-sec">
      <div className="kx-search-h">{title}</div>
      {items.map((it, i) => (
        <div key={i} className="kx-search-row" onClick={() => onPick(it)}>
          {render(it)}
        </div>
      ))}
    </div>
  );
}

// ─── Tweaks overlay (replaces the design's draggable panel) ──────────────────

interface TweaksOverlayProps {
  tweaks: Tweaks;
  setTweak: <K extends keyof Tweaks>(k: K, v: Tweaks[K]) => void;
  onClose: () => void;
}

const TweaksOverlay: React.FC<TweaksOverlayProps> = ({ tweaks, setTweak, onClose }) => (
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
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
      <div className="kx-mono kx-mute" style={{ letterSpacing: "0.12em" }}>
        TWEAKS
      </div>
      <button className="kx-icon-btn" onClick={onClose} aria-label="Close tweaks">
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
      <Toggle value={tweaks.showClusters} onChange={(v) => setTweak("showClusters", v)} />
    </TweaksRow>
    <TweaksRow label="Viewer panel">
      <Toggle value={tweaks.showViewer} onChange={(v) => setTweak("showViewer", v)} />
    </TweaksRow>
    <TweaksRow label="Confidence heatmap">
      <Toggle value={tweaks.showConfHeat} onChange={(v) => setTweak("showConfHeat", v)} />
    </TweaksRow>
  </div>
);

const TweaksRow: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 0", fontSize: 12 }}>
    <span style={{ color: "var(--ink-2)" }}>{label}</span>
    {children}
  </div>
);

interface SegmentedRadioProps {
  value: string;
  onChange: (v: string) => void;
  options: Array<{ value: string; label: string }>;
}

const SegmentedRadio: React.FC<SegmentedRadioProps> = ({ value, onChange, options }) => (
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

const Toggle: React.FC<{ value: boolean; onChange: (v: boolean) => void }> = ({ value, onChange }) => (
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
