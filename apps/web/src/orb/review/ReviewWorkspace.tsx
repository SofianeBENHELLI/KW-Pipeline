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

import { DocHeader } from "./DocHeader";
import { DocRail, type RailSort, type RailSortColumn } from "./DocRail";
import { DocTabs, type DocTab } from "./DocTabs";
import { LinkedView } from "./LinkedView";
import "./review.css";
import "./linked.css";
import { latestStatus } from "./format";
import { useDocumentDetail } from "../hooks/useDocumentDetail";
import { useDocuments, type RailView } from "../hooks/useDocuments";
import type { ApiDocument } from "../../api/types";

const VALID_VIEWS = new Set<RailView>(["recent", "review", "validated", "failed"]);
const VALID_TABS = new Set<DocTab>(["linked", "review", "pipeline"]);

function parseView(raw: string | null): RailView {
  if (raw && VALID_VIEWS.has(raw as RailView)) return raw as RailView;
  return "recent";
}
function parseTab(raw: string | null): DocTab {
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

  return (
    <section
      className="kf-review"
      aria-label="Knowledge Forge — Review Workspace"
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
      />

      <main className="kf-main orb-scroll">
        <DocHeader document={activeDoc} />
        <DocTabs active={tab} onChange={setTab} />

        {tab === "linked" && (
          <div data-testid="kf-tab-linked">
            <LinkedView
              documentId={params.docId ?? null}
              filename={activeDoc?.original_filename}
            />
          </div>
        )}
        {tab === "review" && (
          <div className="kf-tab-placeholder" data-testid="kf-tab-review">
            <h3>Review</h3>
            <p>
              FSM action card, reviewer note, raw extraction and semantic
              markdown cards ship in PR 4 of the redesign.
            </p>
          </div>
        )}
        {tab === "pipeline" && (
          <div className="kf-tab-placeholder" data-testid="kf-tab-pipeline">
            <h3>Pipeline</h3>
            <p>
              Lifecycle history timeline ships in PR 4 of the redesign.
            </p>
          </div>
        )}

        <footer className="kf-foot orb-mono">
          <span>Documents · {docs.length}</span>
          <span aria-hidden="true">·</span>
          <span>view · {view}</span>
          <span className="kf-foot__spacer" />
          <span>j/k row · v validate · r reject</span>
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
