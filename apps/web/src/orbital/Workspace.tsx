import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  extractVersion,
  generateSemantic,
  getDocument,
  getDocumentGraph,
  getExtraction,
  getMarkdown,
  getSemantic,
  listDocuments,
  rejectVersion,
  validateVersion,
} from "../api/client";
import type {
  ApiDocument,
  ApiKnowledgeGraphProjection,
  ApiRawExtraction,
  ApiSemanticDocument,
} from "../api/types";
import { latestVersion } from "../domain/document";

import { Btn, Icon, Kbd, MetaRow, ScopeChip, StatusBadge } from "./atoms";
import { runBatch, type BatchEntry, type BatchFailure } from "./batch";
import { LinkedView } from "./LinkedView";
import { PurgeDialog } from "./PurgeDialog";

/**
 * Variant-A `ReviewWorkspaceA` from the mockup, ported verbatim to TSX
 * and wired to the real backend. Layout matches the mockup 1:1:
 *
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │ topbar: brand · nav (Review/Graph/Search/Chat/Admin) · chip · cog · avatar │
 *   ├────────────────────┬──────────────────────────────────────────┤
 *   │ rail 380px         │ main canvas                              │
 *   │  - search + /      │  - breadcrumbs                           │
 *   │  - 2×2 saved views │  - dochead (title + meta + actions)      │
 *   │  - batchbar        │  - doctabs (Linked default / Pipeline)   │
 *   │  - sortable list   │  - tab content                           │
 *   └────────────────────┴──────────────────────────────────────────┘
 */

type ViewId = "recent" | "review" | "validated" | "failed";
const VIEWS: { id: ViewId; label: string; statuses: string[] }[] = [
  { id: "recent",    label: "Recent",    statuses: [] },
  { id: "review",    label: "Review",    statuses: ["NEEDS_REVIEW"] },
  { id: "validated", label: "Validated", statuses: ["VALIDATED"] },
  { id: "failed",    label: "Failed",    statuses: ["FAILED"] },
];

type SortCol = "filename" | "uploaded" | "status";
type SortDir = "asc" | "desc";

const EXTRACTABLE = new Set(["STORED", "EXTRACTED", "FAILED"]);
const SEMANTICABLE = new Set(["EXTRACTED", "SEMANTIC_READY", "NEEDS_REVIEW"]);
const REVIEWABLE = new Set(["NEEDS_REVIEW", "SEMANTIC_READY"]);

type FsmAction = "extract" | "semantic" | "validate" | "reject";

export interface WorkspaceProps {
  initialDocumentId: string;
  onBackToCatalog: () => void;
}

export function Workspace({ initialDocumentId, onBackToCatalog }: WorkspaceProps) {
  const [view, setView] = useState<ViewId>("recent");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [documents, setDocuments] = useState<ApiDocument[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [docId, setDocId] = useState<string>(initialDocumentId);
  const [doc, setDoc] = useState<ApiDocument | null>(null);
  const [raw, setRaw] = useState<ApiRawExtraction | null>(null);
  const [semantic, setSemantic] = useState<ApiSemanticDocument | null>(null);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [graph, setGraph] = useState<ApiKnowledgeGraphProjection | null>(null);
  const [docLoading, setDocLoading] = useState(true);
  const [docError, setDocError] = useState<string | null>(null);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchProgress, setBatchProgress] = useState<Record<string, BatchEntry>>({});
  const [batchFailures, setBatchFailures] = useState<BatchFailure[]>([]);
  const [batchRunning, setBatchRunning] = useState(false);

  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<FsmAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [tab, setTab] = useState<"linked" | "pipeline">("linked");
  const [sort, setSort] = useState<{ col: SortCol; dir: SortDir }>({ col: "uploaded", dir: "desc" });
  const [extractTab, setExtractTab] = useState<"extraction.json" | "page-spans" | "tables">("extraction.json");
  const [mdTab, setMdTab] = useState<"preview" | "source" | "diff">("preview");
  const [purgeOpen, setPurgeOpen] = useState(false);

  const inFlightRef = useRef<Set<FsmAction>>(new Set());
  const docAbortRef = useRef<AbortController | null>(null);
  const listAbortRef = useRef<AbortController | null>(null);

  /* ───── debounce filename filter ───── */
  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => window.clearTimeout(id);
  }, [q]);

  /* ───── load the document list for the rail ───── */
  const refreshList = useCallback(async () => {
    listAbortRef.current?.abort();
    const controller = new AbortController();
    listAbortRef.current = controller;
    setListLoading(true);
    try {
      const response = await listDocuments({
        status: VIEWS.find((v) => v.id === view)!.statuses,
        q: debouncedQ,
        limit: 50,
      });
      if (!controller.signal.aborted) setDocuments(response.items ?? []);
    } catch {
      // silent — main canvas surfaces its own errors
    } finally {
      if (!controller.signal.aborted) setListLoading(false);
    }
  }, [view, debouncedQ]);

  useEffect(() => {
    refreshList();
    return () => listAbortRef.current?.abort();
  }, [refreshList]);

  /* ───── load the selected doc ───── */
  const fetchDoc = useCallback(async () => {
    docAbortRef.current?.abort();
    const controller = new AbortController();
    docAbortRef.current = controller;
    setDocLoading(true);
    setDocError(null);
    try {
      const fresh = await getDocument(docId);
      if (controller.signal.aborted) return;
      setDoc(fresh);
      const version = latestVersion(fresh);
      if (!version) {
        setRaw(null);
        setSemantic(null);
        setMarkdown(null);
        setGraph(null);
        return;
      }
      const [rawRes, semRes, mdRes, graphRes] = await Promise.allSettled([
        getExtraction(docId, version.id, { signal: controller.signal }),
        getSemantic(docId, version.id, { signal: controller.signal }),
        getMarkdown(docId, version.id),
        getDocumentGraph(docId),
      ]);
      if (controller.signal.aborted) return;
      setRaw(rawRes.status === "fulfilled" ? rawRes.value : null);
      setSemantic(semRes.status === "fulfilled" ? semRes.value : null);
      setMarkdown(mdRes.status === "fulfilled" ? mdRes.value : null);
      setGraph(graphRes.status === "fulfilled" ? graphRes.value : null);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      setDocError(message);
      setDoc(null);
    } finally {
      if (!controller.signal.aborted) setDocLoading(false);
    }
  }, [docId]);

  useEffect(() => {
    fetchDoc();
    return () => docAbortRef.current?.abort();
  }, [fetchDoc]);

  /* ───── FSM transitions ───── */
  const runAction = async (action: FsmAction) => {
    if (!doc) return;
    const version = latestVersion(doc);
    if (!version) return;
    if (inFlightRef.current.has(action)) return;
    inFlightRef.current.add(action);
    setBusy(action);
    setActionError(null);
    try {
      switch (action) {
        case "extract":
          await extractVersion(doc.id, version.id);
          break;
        case "semantic":
          await generateSemantic(doc.id, version.id);
          break;
        case "validate":
          await validateVersion(doc.id, version.id, note || undefined);
          break;
        case "reject":
          await rejectVersion(doc.id, version.id, note || undefined);
          break;
      }
      setBusy(null);
      void fetchDoc();
      void refreshList();
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      setActionError(message);
      setBusy(null);
    } finally {
      inFlightRef.current.delete(action);
    }
  };

  const status = doc ? latestVersion(doc)?.status : undefined;
  const canExtract = !!status && EXTRACTABLE.has(status);
  const canSemantic = !!status && SEMANTICABLE.has(status);
  const canValidate = !!status && REVIEWABLE.has(status);
  const canReject = !!status && REVIEWABLE.has(status);

  /* ───── batch run from rail ───── */
  const toggleSel = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const onRunBatch = async () => {
    if (batchRunning || selected.size === 0) return;
    const targets = documents.filter((d) => selected.has(d.id));
    if (targets.length === 0) return;
    setBatchRunning(true);
    setBatchFailures([]);
    try {
      const { progress, failures } = await runBatch(targets, (next) => {
        setBatchProgress((prev) => (typeof next === "function" ? next(prev) : next));
      });
      setBatchProgress(progress);
      setBatchFailures(failures);
      setSelected((current) => {
        const out = new Set<string>();
        for (const id of current) if (progress[id]?.stage === "failed") out.add(id);
        return out;
      });
      await refreshList();
    } finally {
      setBatchRunning(false);
    }
  };

  /* ───── sort ───── */
  const toggleSort = (col: SortCol) =>
    setSort((s) =>
      s.col === col ? { col, dir: s.dir === "asc" ? "desc" : "asc" } : { col, dir: col === "filename" ? "asc" : "desc" },
    );
  const sortArrow = (col: SortCol) => (sort.col !== col ? "" : sort.dir === "asc" ? " ↑" : " ↓");

  const sortedList = useMemo(() => {
    const cmp = (a: ApiDocument, b: ApiDocument): number => {
      let av: string | number = "";
      let bv: string | number = "";
      switch (sort.col) {
        case "filename":
          av = a.original_filename.toLowerCase();
          bv = b.original_filename.toLowerCase();
          break;
        case "uploaded":
          av = a.created_at ?? "";
          bv = b.created_at ?? "";
          break;
        case "status":
          av = (latestVersion(a)?.status ?? "").toLowerCase();
          bv = (latestVersion(b)?.status ?? "").toLowerCase();
          break;
      }
      if (av < bv) return -1;
      if (av > bv) return 1;
      return 0;
    };
    return [...documents].sort((a, b) => (sort.dir === "asc" ? cmp(a, b) : -cmp(a, b)));
  }, [documents, sort]);

  const formatUploaded = (iso: string | null | undefined) => {
    if (!iso) return { day: "—", time: "" };
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return { day: iso, time: "" };
    return { day: date.toISOString().slice(0, 10), time: date.toISOString().slice(11, 16) };
  };
  const formatBytes = (bytes: number | null | undefined) => {
    if (bytes == null) return "—";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };
  const scopeVar = (kind: string) =>
    kind === "project" ? "var(--orb-ok)" : kind === "swym_community" ? "var(--orb-purple)" : "var(--orb-info)";

  /* ───── rail ───── */
  const Rail = (
    <aside className="rwA-rail">
      <div className="rwA-railhead">
        <div className="rwA-search">
          <span className="rwA-search-i">
            <Icon name="search" />
          </span>
          <input
            className="rwA-search-i-input"
            placeholder="Filter filename…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Filter documents by filename"
          />
          <span style={{ marginLeft: "auto" }}>
            <Kbd>/</Kbd>
          </span>
        </div>
        <div className="rwA-views">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              className={`rwA-view ${view === v.id ? "is-active" : ""}`}
              onClick={() => setView(v.id)}
            >
              <span>{v.label}</span>
              <span className="rwA-view-count">{documents.length.toLocaleString()}</span>
            </button>
          ))}
        </div>
      </div>

      {selected.size > 0 && (
        <div className="rwA-batchbar">
          <div className="rwA-batchbar-l">
            <span className="orb-mono" style={{ fontSize: 11, color: "var(--orb-fg-muted)" }}>
              {selected.size} selected
            </span>
            <button type="button" className="rwA-link" onClick={() => setSelected(new Set())} disabled={batchRunning}>
              clear
            </button>
          </div>
          <Btn xs kind="primary" icon={<Icon name="bolt" />} onClick={onRunBatch} disabled={batchRunning}>
            {batchRunning ? "Running…" : "Run pipeline"}
          </Btn>
        </div>
      )}

      <div className="rwA-listhead">
        <span style={{ width: 20 }}></span>
        <button
          className={`rwA-sortbtn ${sort.col === "filename" ? "is-on" : ""}`}
          style={{ flex: 1 }}
          onClick={() => toggleSort("filename")}
        >
          FILENAME{sortArrow("filename")}
        </button>
        <button
          className={`rwA-sortbtn ${sort.col === "uploaded" ? "is-on" : ""}`}
          style={{ width: 96 }}
          onClick={() => toggleSort("uploaded")}
        >
          UPLOADED{sortArrow("uploaded")}
        </button>
        <button
          className={`rwA-sortbtn ${sort.col === "status" ? "is-on" : ""}`}
          style={{ width: 96, textAlign: "right" }}
          onClick={() => toggleSort("status")}
        >
          STATUS{sortArrow("status")}
        </button>
      </div>

      <div className="rwA-listcount orb-mono">
        showing <b>{sortedList.length}</b> of {documents.length.toLocaleString()}
        {listLoading ? " · loading" : " · scroll for more"}
      </div>

      <div className="rwA-list">
        {sortedList.map((d) => {
          const dStatus = latestVersion(d)?.status;
          const prog = batchProgress[d.id];
          const isSel = docId === d.id;
          const up = formatUploaded(d.created_at);
          const dBytes = formatBytes(latestVersion(d)?.file_size);
          const firstScope = (d.scopes ?? [])[0];
          return (
            <div
              key={d.id}
              className={`rwA-row ${isSel ? "is-sel" : ""}`}
              onClick={() => setDocId(d.id)}
            >
              <span className="rwA-check" onClick={(e) => e.stopPropagation()}>
                <button
                  type="button"
                  className={`rwA-checkbox ${selected.has(d.id) ? "is-on" : ""}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleSel(d.id);
                  }}
                  aria-label={`Select ${d.original_filename} for batch`}
                  aria-pressed={selected.has(d.id)}
                >
                  {selected.has(d.id) && <Icon name="check" size={10} />}
                </button>
              </span>
              <div className="rwA-rowmain">
                <div className="rwA-fname" title={d.original_filename}>
                  {d.original_filename}
                </div>
                <div className="rwA-rowmeta">
                  <span className="orb-mono">{d.id.slice(0, 8)}</span>
                  <span>·</span>
                  <span>v{d.versions.length}</span>
                  <span>·</span>
                  <span>{dBytes}</span>
                  {firstScope && (
                    <span className="rwA-scope" style={{ color: scopeVar(firstScope.kind) }}>
                      ● {firstScope.kind === "swym_community" ? "community" : firstScope.kind}
                    </span>
                  )}
                </div>
              </div>
              <div className="rwA-rowuploaded orb-mono" title={d.created_at ?? ""}>
                <span className="rwA-up-day">{up.day}</span>
                <span className="rwA-up-time">{up.time}</span>
              </div>
              <div className="rwA-rowstatus">
                {prog ? (
                  <span className={`rwA-prog rwA-prog--${prog.stage}`}>{prog.stage}</span>
                ) : (
                  <StatusBadge status={dStatus} />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );

  /* ───── main canvas ───── */
  if (docLoading && !doc) {
    return (
      <div className="orb-app rwA" style={{ gridTemplateRows: "1fr" }}>
        <div className="rwA-fab">
          {Rail}
          <main className="rwA-main">
            <p style={{ color: "var(--orb-fg-muted)" }}>Loading document <span className="orb-mono">{docId.slice(0, 8)}</span>…</p>
          </main>
        </div>
      </div>
    );
  }
  if (!doc) {
    return (
      <div className="orb-app rwA" style={{ gridTemplateRows: "1fr" }}>
        <div className="rwA-fab">
          {Rail}
          <main className="rwA-main">
            <div className="cat-banner cat-banner--session" role="alert" style={{ borderRadius: 6 }}>
              <Icon name="alert" />
              <span>Failed to load document: {docError ?? "not found"}</span>
              <span style={{ flex: 1 }}></span>
              <button className="cat-link" onClick={() => void fetchDoc()}>
                retry
              </button>
            </div>
          </main>
        </div>
      </div>
    );
  }

  const latest = latestVersion(doc)!;
  const projectionDone = graph && graph.nodes.length > 0;
  const projectionLabel = projectionDone ? "COMPLETED" : graph ? "EMPTY" : "PENDING";
  const projectionColor =
    projectionLabel === "COMPLETED" ? "var(--orb-ok)" : projectionLabel === "EMPTY" ? "var(--orb-warn)" : "var(--orb-fg-faint)";

  return (
    <div className="orb-app rwA" style={{ gridTemplateRows: "1fr" }}>
      <div className="rwA-fab">
        {Rail}

        <main className="rwA-main orb-scroll">
          <div className="rwA-crumbs">
            <button
              type="button"
              onClick={onBackToCatalog}
              style={{ background: "transparent", border: 0, color: "inherit", cursor: "pointer", padding: 0, font: "inherit" }}
            >
              Documents
            </button>
            <span className="rwA-crumbs-sep">
              <Icon name="chev" />
            </span>
            <span className="orb-mono">{doc.id.slice(0, 8)}</span>
            <span className="rwA-crumbs-sep">
              <Icon name="chev" />
            </span>
            <span className="rwA-crumbs-cur">{doc.original_filename}</span>
          </div>

          <header className="rwA-dochead">
            <div>
              <h1 className="rwA-title">{doc.original_filename}</h1>
              <div className="rwA-titlemeta">
                <StatusBadge status={status} />
                <span className="orb-mono" style={{ color: "var(--orb-fg-dim)" }}>
                  {doc.id}
                </span>
                <span style={{ color: "var(--orb-fg-dim)" }}>v{doc.versions.length}</span>
                {(doc.scopes ?? []).map((s, i) => (
                  <ScopeChip key={`${s.kind}:${i}`} scope={s.kind} />
                ))}
                <span className="rwA-pill">
                  <span className="dot" style={{ background: projectionColor }}></span>
                  projection · {projectionLabel}
                  {graph ? ` · ${graph.nodes.length} nodes` : ""}
                </span>
              </div>
            </div>
            <div className="rwA-headactions">
              <Btn
                kind="ghost"
                icon={<Icon name="link" />}
                onClick={() => {
                  const url = new URL(window.location.href);
                  url.searchParams.set("document", doc.id);
                  navigator.clipboard?.writeText(url.toString()).catch(() => {});
                }}
              >
                Copy link
              </Btn>
              <Btn kind="ghost" icon={<Icon name="refresh" />} onClick={() => void fetchDoc()}>
                Refresh
              </Btn>
              <Btn kind="ghost" icon={<Icon name="trash" />} onClick={() => setPurgeOpen(true)}>
                Purge
              </Btn>
            </div>
          </header>

          <div className="rwA-doctabs">
            <button className={`rwA-doctab ${tab === "linked" ? "is-active" : ""}`} onClick={() => setTab("linked")}>
              <Icon name="graph" /> Linked view <span className="rwA-doctab-tag orb-mono">default</span>
            </button>
            <button className={`rwA-doctab ${tab === "pipeline" ? "is-active" : ""}`} onClick={() => setTab("pipeline")}>
              <Icon name="bolt" /> Pipeline &amp; FSM
            </button>
            <span className="orb-mono rwA-hint" style={{ marginLeft: "auto" }}>
              {tab === "linked"
                ? "hover any object — its source span(s) highlight in the document, and vice-versa"
                : "lifecycle · extraction · semantic · versions"}
            </span>
          </div>

          {tab === "linked" && <LinkedView doc={doc} semantic={semantic} graph={graph} />}

          {tab === "pipeline" && (
            <section className="rwA-grid">
              <div className="orb-card rwA-fsmcard">
                <div className="rwA-cardhead">
                  <span className="orb-section-h">Lifecycle</span>
                  <span className="rwA-flow">
                    STORED → EXTRACTED → SEMANTIC_READY → <span style={{ color: "var(--orb-fg)" }}>VALIDATED</span>
                  </span>
                </div>
                <div className="rwA-fsm">
                  <div className="rwA-fsm-actions">
                    <Btn icon={<Icon name="bolt" />} disabled={!canExtract || busy !== null} onClick={() => runAction("extract")}>
                      {busy === "extract" ? "Extracting…" : "Extract"}
                    </Btn>
                    <Btn icon={<Icon name="spark" />} disabled={!canSemantic || busy !== null} onClick={() => runAction("semantic")}>
                      {busy === "semantic" ? "Generating…" : "Semantic"}
                    </Btn>
                    <div style={{ flex: 1 }}></div>
                    <Btn kind="ghost" disabled={!canReject || busy !== null} onClick={() => runAction("reject")}>
                      {busy === "reject" ? "Rejecting…" : "Reject"}
                    </Btn>
                    <Btn kind="primary" icon={<Icon name="check" />} disabled={!canValidate || busy !== null} onClick={() => runAction("validate")}>
                      {busy === "validate" ? "Validating…" : "Validate"}
                    </Btn>
                  </div>
                  <textarea
                    className="rwA-note"
                    placeholder="Reviewer note (optional) — appended to audit trail on validate/reject…"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                  <div className="rwA-fsm-hint">
                    <span className="icon">
                      <Icon name="alert" />
                    </span>
                    Status <b>{status}</b> · review actions gated by current state · double-click guarded
                  </div>
                  {actionError && (
                    <div style={{ marginTop: 8, color: "var(--orb-err-fg)", fontSize: 11 }} role="alert">
                      {actionError}
                    </div>
                  )}
                </div>
              </div>

              <div className="orb-card">
                <div className="rwA-cardhead">
                  <span className="orb-section-h">Document detail</span>
                  <span className="orb-mono rwA-hint">GET /documents/{doc.id.slice(0, 8)}</span>
                </div>
                <div style={{ padding: "6px 14px 14px" }}>
                  <MetaRow k="ID">
                    <span className="orb-mono">{doc.id}</span>
                  </MetaRow>
                  <MetaRow k="Filename">{doc.original_filename}</MetaRow>
                  <MetaRow k="Versions">
                    {doc.versions.length} · latest{" "}
                    <span className="orb-mono">{latest.id.slice(0, 8)}</span>
                  </MetaRow>
                  <MetaRow k="Bytes">{formatBytes(latest.file_size)}</MetaRow>
                  <MetaRow k="Content-type">
                    <span className="orb-mono">{latest.content_type}</span>
                  </MetaRow>
                  <MetaRow k="SHA-256">
                    <span className="orb-mono">{latest.sha256}</span>
                  </MetaRow>
                  <MetaRow k="Scope">{(doc.scopes ?? []).map((s) => s.kind).join(" + ") || "—"}</MetaRow>
                  <MetaRow k="Created">
                    <span className="orb-mono">{latest.created_at}</span>
                  </MetaRow>
                </div>
              </div>

              <div className="orb-card rwA-tall">
                <div className="rwA-cardhead">
                  <span className="orb-section-h">Raw extraction</span>
                  <div className="rwA-tabs">
                    {(["extraction.json", "page-spans", "tables"] as const).map((t) => (
                      <button key={t} className={`rwA-tab ${extractTab === t ? "is-active" : ""}`} onClick={() => setExtractTab(t)}>
                        {t}
                      </button>
                    ))}
                  </div>
                </div>
                {raw ? (
                  <pre className="rwA-code orb-mono orb-scroll">
                    {extractTab === "extraction.json"
                      ? JSON.stringify({ text_length: raw.text?.length ?? 0, sections: raw.sections?.length ?? 0, warnings: raw.warnings }, null, 2)
                      : extractTab === "page-spans"
                        ? (raw.text ?? "").slice(0, 1200) || "(no text)"
                        : (raw.sections ?? []).map((s) => `# ${s.heading ?? ""}\n${s.text ?? ""}`).join("\n\n").slice(0, 1200) || "(no sections)"}
                  </pre>
                ) : (
                  <p style={{ padding: "12px 14px", color: "var(--orb-fg-muted)", fontSize: 11 }}>
                    No raw extraction yet — run <span className="orb-mono">Extract</span> when the version is{" "}
                    <span className="orb-mono">STORED</span>.
                  </p>
                )}
              </div>

              <div className="orb-card">
                <div className="rwA-cardhead">
                  <span className="orb-section-h">Versions</span>
                  <span className="orb-mono rwA-hint">{doc.versions.length} total</span>
                </div>
                <div className="rwA-versbody">
                  {doc.versions
                    .slice(-6)
                    .reverse()
                    .map((v, i) => (
                      <div key={v.id} className={`rwA-versrow ${i === 0 ? "is-cur" : ""}`}>
                        <span className="orb-mono" style={{ width: 30 }}>
                          v{v.version_number}
                        </span>
                        <StatusBadge status={v.status} />
                        <span className="orb-mono rwA-hint" style={{ flex: 1, textAlign: "right" }}>
                          {i === 0 ? "current" : v.created_at?.slice(0, 10) ?? "—"}
                        </span>
                      </div>
                    ))}
                </div>
              </div>

              <div className="orb-card rwA-tall">
                <div className="rwA-cardhead">
                  <span className="orb-section-h">Semantic markdown</span>
                  <div className="rwA-tabs">
                    {(["preview", "source", "diff"] as const).map((t) => (
                      <button key={t} className={`rwA-tab ${mdTab === t ? "is-active" : ""}`} onClick={() => setMdTab(t)}>
                        {t === "diff" ? "diff vs prev" : t}
                      </button>
                    ))}
                  </div>
                </div>
                {markdown ? (
                  <pre className="rwA-code orb-mono orb-scroll" style={{ whiteSpace: "pre-wrap" }}>
                    {markdown}
                  </pre>
                ) : semantic ? (
                  <div className="rwA-md orb-scroll">
                    <h2>{doc.original_filename}</h2>
                    {(semantic.sections ?? []).slice(0, 6).map((s) => (
                      <div key={s.id}>
                        <h3>{s.heading || "(untitled section)"}</h3>
                        <p>{(s.text ?? "").slice(0, 280)}{(s.text?.length ?? 0) > 280 ? "…" : ""}</p>
                      </div>
                    ))}
                    <div className="rwA-md-fade"></div>
                  </div>
                ) : (
                  <p style={{ padding: "12px 14px", color: "var(--orb-fg-muted)", fontSize: 11 }}>
                    No semantic output yet — run <span className="orb-mono">Semantic</span> to generate it.
                  </p>
                )}
              </div>
            </section>
          )}

          {batchFailures.length > 0 && (
            <div className="rwA-batchbanner">
              <div className="rwA-batchbanner-h">
                <span className="icon">
                  <Icon name="bolt" />
                </span>
                <b>Batch pipeline</b>
                <span className="orb-mono rwA-hint">
                  {Object.values(batchProgress).filter((v) => v.stage === "done").length} done · {batchFailures.length} failed
                </span>
                <span style={{ flex: 1 }}></span>
                <button className="rwA-link" onClick={() => setBatchFailures([])}>
                  dismiss
                </button>
              </div>
              <div className="rwA-batchbanner-fail">
                {batchFailures.map((f) => (
                  <div key={f.document_id} className="orb-mono">
                    <span style={{ color: "var(--orb-err)" }}>✗</span> {f.document_id.slice(0, 8)} ·{" "}
                    <span style={{ color: "var(--orb-fg-muted)" }}>{f.reason}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <footer className="rwA-foot orb-mono">
            <span>Documents · {documents.length.toLocaleString()}</span>
            <span>·</span>
            <span>backend kw-api.benhelli.org</span>
            <span style={{ flex: 1 }}></span>
            <span>⌘K commands</span>
            <span>⌘/ search</span>
            <span>j/k row · v validate · r reject</span>
          </footer>
        </main>
      </div>
      <PurgeDialog
        open={purgeOpen}
        onClose={() => setPurgeOpen(false)}
        onConfirmed={() => {
          setPurgeOpen(false);
          onBackToCatalog();
        }}
        documentId={doc.id}
        filename={doc.original_filename}
        versionCount={doc.versions.length}
      />
    </div>
  );
}

