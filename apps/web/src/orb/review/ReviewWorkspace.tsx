/**
 * ReviewWorkspace — page shell for `/kf/review` and `/kf/review/:docId`.
 *
 * Two-column grid: 380px rail | 1fr main pane. The rail owns document
 * picking + filtering + batch selection; the main pane owns the
 * currently-selected document's chrome (breadcrumbs / title / status /
 * scopes / projection pill) and a tab strip routing to Linked View
 * (PR 3) or the Review/Pipeline cards (PR 4).
 *
 * URL contract:
 *   /kf/review              → rail visible, main pane shows the empty
 *                             "pick a document" header.
 *   /kf/review/:docId       → main pane fetches + renders that doc.
 *
 * Filters live in `URLSearchParams` so links are shareable:
 *   ?view=review|recent|validated|failed
 *   ?q=<filename substring>
 *   ?tab=linked|review|pipeline
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactElement } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { BatchBanner } from "./BatchBanner";
import { DocHeader } from "./DocHeader";
import { DocRail, type RailSort, type RailSortColumn } from "./DocRail";
import { DocTabs, type DocTab } from "./DocTabs";
import { LinkedView, type LinkedViewPdf } from "./LinkedView";
import { PipelineTab } from "./PipelineTab";
import { ResizeHandle } from "./ResizeHandle";
import { ReviewTab } from "./ReviewTab";
import "./review.css";
import "./linked.css";
import "./fsm.css";
import { latestStatus } from "./format";
import { useBatchPipeline } from "../hooks/useBatchPipeline";
import { useDocumentDetail } from "../hooks/useDocumentDetail";
import { useDocuments, type RailView } from "../hooks/useDocuments";
import { useResizable } from "./useResizable";
import type { ApiDocument } from "../../api/types";

// ── Rail resize/collapse persistence ────────────────────────────────
// Keys are namespaced (``kf:`` prefix) so they cannot collide with
// other localStorage consumers in the host shell.
const _RAIL_WIDTH_KEY = "kf:review:rail-width";
const _RAIL_COLLAPSED_KEY = "kf:review:rail-collapsed";
const _RAIL_MIN_WIDTH = 240;
const _RAIL_MAX_WIDTH = 640;
const _RAIL_DEFAULT_WIDTH = 380;
/**
 * Keyboard shortcut that toggles rail collapse. ``[`` matches the
 * existing single-key shortcut style elsewhere in the page footer
 * (``j/k row · v validate · r reject``).
 */
const _RAIL_TOGGLE_KEY = "[";

function _readBooleanStored(key: string, fallback: boolean): boolean {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return fallback;
    return raw === "true";
  } catch {
    return fallback;
  }
}

function _writeBooleanStored(key: string, value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value ? "true" : "false");
  } catch {
    // Best-effort.
  }
}

const _PDF_CONTENT_TYPE = "application/pdf";

/**
 * Pick the version row the PDF viewer should render in the left pane.
 *
 * Returns ``null`` when the document is not a PDF, has no versions
 * with a populated SHA-256, or hasn't been hashed yet — the LinkedView
 * branch then falls back to the existing per-section text article.
 *
 * Prefers the latest-numbered version with a non-empty hash so a
 * mid-pipeline upload (status ``UPLOADED`` / ``HASHED`` but not yet
 * extracted) still renders against its own bytes rather than an older
 * sibling's.
 */
function _pdfMetaFor(doc: ApiDocument | null): LinkedViewPdf | null {
  if (doc === null) return null;
  const candidate = [...doc.versions]
    .sort((a, b) => b.version_number - a.version_number)
    .find(
      (v) =>
        v.content_type === _PDF_CONTENT_TYPE &&
        typeof v.sha256 === "string" &&
        v.sha256.length > 0,
    );
  if (!candidate) return null;
  return { versionId: candidate.id, expectedHash: candidate.sha256 };
}

const VALID_VIEWS = new Set<RailView>(["recent", "review", "validated", "failed"]);
const VALID_TABS = new Set<DocTab>(["linked", "pipeline"]);

function parseView(raw: string | null): RailView {
  if (raw && VALID_VIEWS.has(raw as RailView)) return raw as RailView;
  return "recent";
}
function parseTab(raw: string | null): DocTab {
  // Legacy alias: the three-tab interim shipped `?tab=review` pointing
  // at the FSM card. The two-tab cutover combined it with the
  // lifecycle-history surface under `?tab=pipeline`. Preserve the
  // alias so any saved links keep landing on the right body.
  if (raw === "review") return "pipeline";
  if (raw && VALID_TABS.has(raw as DocTab)) return raw as DocTab;
  return "linked";
}

export interface ReviewWorkspaceProps {
  /** Optional fixture override — used by tests to skip the hooks. */
  fixtureDocs?: ApiDocument[];
}

export function ReviewWorkspace({
  fixtureDocs,
}: ReviewWorkspaceProps = {}): ReactElement {
  const params = useParams<{ docId?: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const view = parseView(searchParams.get("view"));
  const query = searchParams.get("q") ?? "";
  const tab = parseTab(searchParams.get("tab"));

  // Local UI state — not in the URL because they aren't shareable signals.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<RailSort>({ col: "uploaded", dir: "desc" });

  // ── Rail width / collapse ─────────────────────────────────────────
  // Two layout knobs that persist across reloads via ``localStorage``:
  //
  //   * ``railResize.value``  → live rail width in pixels (drag-driven).
  //   * ``railCollapsed``     → boolean toggled via the rail-head button
  //                             or the ``[`` keyboard shortcut. While
  //                             true, the rail column collapses to a
  //                             1-px sliver and the toggle moves into
  //                             the main pane so the operator can
  //                             expand it again.
  const railResize = useResizable({
    initial: _RAIL_DEFAULT_WIDTH,
    min: _RAIL_MIN_WIDTH,
    max: _RAIL_MAX_WIDTH,
    storageKey: _RAIL_WIDTH_KEY,
  });
  const [railCollapsed, setRailCollapsed] = useState<boolean>(() =>
    _readBooleanStored(_RAIL_COLLAPSED_KEY, false),
  );
  const toggleRail = useCallback(() => {
    setRailCollapsed((prev) => {
      const next = !prev;
      _writeBooleanStored(_RAIL_COLLAPSED_KEY, next);
      return next;
    });
  }, []);

  // Global ``[`` shortcut. Skipped when focus is in an editable target
  // so the operator can still type ``[`` inside the rail's search box
  // or any text input.
  useEffect(() => {
    function handler(event: KeyboardEvent) {
      if (event.key !== _RAIL_TOGGLE_KEY) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable) {
          return;
        }
      }
      event.preventDefault();
      toggleRail();
    }
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [toggleRail]);

  // Fetch the catalog page. Tests can short-circuit by providing
  // `fixtureDocs`; we fall through to the real hook otherwise.
  const live = useDocuments({ view, q: query });
  const docs = fixtureDocs ?? live.items;
  const loading = !fixtureDocs && live.status === "loading";
  const errorMessage =
    !fixtureDocs && live.status === "error"
      ? (live.error?.message ?? "Failed to load documents")
      : null;

  // Detail for the active doc.
  const detail = useDocumentDetail(params.docId ?? null);

  // Batch pipeline (rail's "Run pipeline" button → see runBatch below).
  const batch = useBatchPipeline();

  const setQuery = useCallback(
    (q: string) => {
      const next = new URLSearchParams(searchParams);
      if (q.trim().length === 0) {
        next.delete("q");
      } else {
        next.set("q", q);
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const setView = useCallback(
    (v: RailView) => {
      const next = new URLSearchParams(searchParams);
      if (v === "recent") next.delete("view");
      else next.set("view", v);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const setTab = useCallback(
    (t: DocTab) => {
      const next = new URLSearchParams(searchParams);
      if (t === "linked") next.delete("tab");
      else next.set("tab", t);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const onSelectDoc = useCallback(
    (docId: string) => {
      const search = searchParams.toString();
      const target = `/kf/review/${docId}${search ? `?${search}` : ""}`;
      navigate(target, { replace: false });
    },
    [navigate, searchParams],
  );

  const toggleSelect = useCallback((docId: string) => {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(docId)) n.delete(docId);
      else n.add(docId);
      return n;
    });
  }, []);

  const clearSelection = useCallback(() => setSelected(new Set()), []);

  const toggleSort = useCallback((col: RailSortColumn) => {
    setSort((s) =>
      s.col === col
        ? { col, dir: s.dir === "asc" ? "desc" : "asc" }
        : { col, dir: col === "filename" ? "asc" : "desc" },
    );
  }, []);

  // Sorted view of the catalog — server doesn't expose sort params, so
  // we sort the loaded page client-side. Acceptable for the rail's
  // current per-view page size of 50.
  const sortedDocs = useMemo(() => sortDocs(docs, sort), [docs, sort]);

  // If the URL points at a doc that isn't in the current list yet
  // (deep-link flow), still let the detail hook drive the main pane.
  // We simply don't highlight it in the rail until the catalog catches
  // up.
  const activeDoc =
    detail.document ??
    docs.find((d) => d.id === params.docId) ??
    null;

  // Pull selected rows that are no longer in the current view out of
  // the batch set so the count stays honest.
  useEffect(() => {
    if (selected.size === 0) return;
    const visible = new Set(docs.map((d) => d.id));
    let stale = false;
    selected.forEach((id) => {
      if (!visible.has(id)) stale = true;
    });
    if (!stale) return;
    setSelected((s) => {
      const next = new Set<string>();
      docs.forEach((d) => {
        if (s.has(d.id)) next.add(d.id);
      });
      return next;
    });
  }, [docs, selected]);

  // CSS custom property feeds the grid template — keeps the rail
  // column reactive to drag without per-frame React renders inside
  // ``DocRail`` (which is heavy when the catalog is large).
  const reviewStyle = {
    "--kf-rail-w": railCollapsed ? "0px" : `${railResize.value}px`,
  } as React.CSSProperties;

  return (
    <section
      className={
        railCollapsed
          ? "kf-review is-rail-collapsed"
          : "kf-review"
      }
      aria-label="Knowledge Forge — Review Workspace"
      style={reviewStyle}
    >
      <div
        className="kf-rail-slot"
        // The slot wraps the rail so we can collapse the whole column
        // with a single CSS rule (``aria-hidden`` + display:none) and
        // still keep DocRail's internal state alive in React for a
        // snap-back on expand.
        aria-hidden={railCollapsed}
      >
        <DocRail
          view={view}
          onView={setView}
          query={query}
          onQuery={setQuery}
          documents={sortedDocs}
          loading={loading}
          errorMessage={errorMessage}
          activeDocId={params.docId ?? null}
          onSelect={onSelectDoc}
          selected={selected}
          onToggleSelect={toggleSelect}
          onClearSelection={clearSelection}
          sort={sort}
          onToggleSort={toggleSort}
          onCollapse={toggleRail}
          onRunBatch={() => {
            const ids = selected;
            const picked = sortedDocs.filter((d) => ids.has(d.id));
            if (picked.length === 0) return;
            batch.run(picked).then(() => {
              // Refresh the catalog page so status badges + FSM gates
              // reflect the post-batch reality. Don't await — fire and
              // forget so the banner stays visible.
              live.refetch();
              detail.refetch();
            });
          }}
        />
      </div>

      <ResizeHandle
        label="Resize document rail"
        onPointerDown={railResize.onPointerDown}
        isDragging={railResize.isDragging}
        disabled={railCollapsed}
      />

      <main className="kf-main orb-scroll">
        {railCollapsed ? (
          /* When the rail is hidden, expose a thin "expand" chevron
           * pinned to the left edge of the main pane so the operator
           * can bring the rail back without remembering the keyboard
           * shortcut. The button itself sits inside ``.kf-main``
           * which is the leftmost visible column when the rail is
           * collapsed — semantically still "at the rail's edge". */
          <button
            type="button"
            className="kf-rail-expand"
            onClick={toggleRail}
            aria-label="Expand document rail ([)"
            title="Expand rail ([)"
            data-testid="kf-rail-expand"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <polyline points="13 17 18 12 13 7" />
              <polyline points="6 17 11 12 6 7" />
            </svg>
          </button>
        ) : null}
        <DocHeader document={activeDoc} />
        <DocTabs active={tab} onChange={setTab} />

        {tab === "linked" && (
          <div
            className="kf-tab-body kf-tab-body--linked"
            data-testid="kf-tab-linked"
          >
            <LinkedView
              documentId={params.docId ?? null}
              filename={activeDoc?.original_filename}
              pdf={_pdfMetaFor(activeDoc)}
            />
          </div>
        )}
        {tab === "pipeline" && (
          <div
            className="kf-tab-body kf-tab-body--pipeline orb-scroll"
            data-testid="kf-tab-pipeline"
          >
            {/* Per design §3.5: a single "Pipeline & FSM" tab that
                combines the FSM action card, document detail, version
                list, raw extraction, and semantic markdown. The
                lifecycle-history timeline that the three-tab interim
                shipped at `?tab=pipeline` collapses into the Versions
                card here. */}
            <ReviewTab
              document={activeDoc}
              onAfterTransition={() => {
                detail.refetch();
                live.refetch();
              }}
            />
            <PipelineTab document={activeDoc} />
          </div>
        )}

        <BatchBanner snapshot={batch.snapshot} onDismiss={batch.dismiss} />

        <footer className="kf-foot orb-mono">
          <span>Documents · {docs.length}</span>
          <span aria-hidden="true">·</span>
          <span>view · {view}</span>
          <span className="kf-foot__spacer" />
          <span>[ rail · j/k row · v validate · r reject</span>
        </footer>
      </main>
    </section>
  );
}

/** Sort the rail's loaded page client-side. */
export function sortDocs(
  list: ApiDocument[],
  sort: RailSort,
): ApiDocument[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  return [...list].sort((a, b) => {
    if (sort.col === "filename") {
      return (
        sign *
        a.original_filename
          .toLowerCase()
          .localeCompare(b.original_filename.toLowerCase())
      );
    }
    if (sort.col === "status") {
      return sign * latestStatus(a).localeCompare(latestStatus(b));
    }
    // Default: uploaded — fall back to created_at when versions are
    // missing.
    const av =
      a.versions[a.versions.length - 1]?.created_at ?? a.created_at ?? "";
    const bv =
      b.versions[b.versions.length - 1]?.created_at ?? b.created_at ?? "";
    return sign * av.localeCompare(bv);
  });
}
