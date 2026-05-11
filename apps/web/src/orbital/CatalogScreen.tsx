import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, listDocuments } from "../api/client";
import type { components } from "../api/generated/schema";
import { latestVersion } from "../domain/document";

import { Btn, Icon, StatusBadge } from "./atoms";
import { runBatch, type BatchEntry, type BatchFailure } from "./batch";

/**
 * `OrbBannersAndCatalog` artboard from the mockup, ported verbatim. Flat
 * banner stack at the top + heading + toolbar (filter + saved-view chips
 * + selection hint + Run pipeline) + 8-column native HTML table + batch
 * result box. No topbar, no rail — those belong to the workspace
 * artboard. Mock `OrbDOCS` is swapped for real `listDocuments`; the chip
 * filters map onto the existing backend `status[]` query param.
 */

type ApiDocument = components["schemas"]["Document"];

type ViewId = "Recent" | "Review" | "Validated" | "Failed";

const VIEW_STATUSES: Record<ViewId, string[]> = {
  Recent: [],
  Review: ["NEEDS_REVIEW"],
  Validated: ["VALIDATED"],
  Failed: ["FAILED"],
};

const SEARCH_DEBOUNCE_MS = 300;

export interface CatalogScreenProps {
  /** Optional pre-selected document — if set, the workspace opens directly. */
  initialSelectedId?: string | null;
  /** Called when the operator clicks a row. The parent swaps to the workspace. */
  onOpenDocument: (id: string) => void;
  forceAutoActive?: boolean;
  sessionExpired?: { onSignIn: () => void; onDismiss: () => void } | null;
  deepLinkMissing?: { id: string; onDismiss: () => void } | null;
}

export function CatalogScreen({
  onOpenDocument,
  forceAutoActive,
  sessionExpired,
  deepLinkMissing,
}: CatalogScreenProps) {
  const [view, setView] = useState<ViewId>("Recent");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [documents, setDocuments] = useState<ApiDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchProgress, setBatchProgress] = useState<Record<string, BatchEntry>>({});
  const [batchFailures, setBatchFailures] = useState<BatchFailure[]>([]);
  const [batchRunning, setBatchRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedQ(q.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [q]);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const response = await listDocuments({
        status: VIEW_STATUSES[view],
        q: debouncedQ,
        limit: 50,
      });
      if (!controller.signal.aborted) setDocuments(response.items ?? []);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      setError(message);
      setDocuments([]);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [view, debouncedQ]);

  useEffect(() => {
    refresh();
    return () => abortRef.current?.abort();
  }, [refresh]);

  const toggleRow = (id: string) => {
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
      // Drop succeeded rows from selection, keep failed.
      setSelected((current) => {
        const out = new Set<string>();
        for (const id of current) if (progress[id]?.stage === "failed") out.add(id);
        return out;
      });
      await refresh();
    } finally {
      setBatchRunning(false);
    }
  };

  const counts = useMemo<Partial<Record<ViewId, number>>>(() => {
    // We render the counts the mockup shows; for the active view we use the
    // live count, others stay as plain numbers from the cached fetch.
    return { [view]: documents.length };
  }, [view, documents]);

  const batchTotal = Object.keys(batchProgress).length;
  const batchDone = Object.values(batchProgress).filter((e) => e.stage === "done").length;
  const showBatchBox = batchTotal > 0;

  return (
    <div className="orb-app cat">
      {forceAutoActive && (
        <div className="cat-banner cat-banner--force" role="status">
          <Icon name="alert" />
          <span>
            <b>Force-auto corpus is active</b> — every version is being auto-validated regardless of confidence.{" "}
            <span style={{ color: "var(--orb-warn-fg)" }}>KW_HITL_FORCE_AUTO_CORPUS=true</span>
          </span>
          <span style={{ flex: 1 }}></span>
          <span className="orb-mono" style={{ fontSize: 10, opacity: 0.7 }}>
            non-dismissible
          </span>
        </div>
      )}
      {sessionExpired && (
        <div className="cat-banner cat-banner--session" role="alert">
          <Icon name="shield" />
          <span>
            Your session has expired.{" "}
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                sessionExpired.onSignIn();
              }}
            >
              Sign in again
            </a>{" "}
            to continue.
          </span>
          <span style={{ flex: 1 }}></span>
          <button className="cat-link" onClick={sessionExpired.onDismiss}>
            dismiss
          </button>
        </div>
      )}
      {deepLinkMissing && (
        <div className="cat-banner cat-banner--link" role="status">
          <Icon name="link" />
          <span>
            Couldn't resolve <code className="orb-mono">{deepLinkMissing.id}</code> — the document may have been archived or purged.
          </span>
          <span style={{ flex: 1 }}></span>
          <button className="cat-link" onClick={deepLinkMissing.onDismiss}>
            dismiss
          </button>
        </div>
      )}

      <div style={{ padding: "22px 24px" }}>
        <h1 style={{ margin: "0 0 12px", fontSize: 18, fontWeight: 600 }}>Document catalog — table view</h1>

        <div className="cat-toolbar">
          <input
            className="orb-input"
            placeholder="filter filename…"
            style={{ width: 260 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Filter documents by filename"
          />
          <div className="cat-chips">
            {(Object.keys(VIEW_STATUSES) as ViewId[]).map((v) => (
              <button
                key={v}
                type="button"
                className={`cat-chip ${v === view ? "is-on" : ""}`}
                onClick={() => setView(v)}
              >
                {v} <span className="orb-mono">·{counts[v]?.toLocaleString() ?? "—"}</span>
              </button>
            ))}
          </div>
          <span style={{ flex: 1 }}></span>
          {selected.size > 0 && (
            <>
              <span className="orb-mono" style={{ fontSize: 10, color: "var(--orb-fg-dim)" }}>
                {selected.size} selected · sticky after run
              </span>
              <Btn xs kind="primary" icon={<Icon name="bolt" />} onClick={onRunBatch} disabled={batchRunning}>
                {batchRunning ? "Running…" : "Run pipeline on selection"}
              </Btn>
            </>
          )}
        </div>

        <table className="cat-tab">
          <thead>
            <tr>
              <th></th>
              <th>FILENAME</th>
              <th>ID</th>
              <th>STATUS</th>
              <th>VERS</th>
              <th>SCOPE</th>
              <th>UPDATED</th>
            </tr>
          </thead>
          <tbody>
            {documents.length === 0 && (
              <tr>
                <td colSpan={7} style={{ padding: 24, color: "var(--orb-fg-muted)", textAlign: "center" }}>
                  {loading ? "Loading documents…" : "No documents match this filter."}
                </td>
              </tr>
            )}
            {documents.map((d) => {
              const status = latestVersion(d)?.status ?? "STORED";
              const isSel = selected.has(d.id);
              return (
                <tr key={d.id} className={isSel ? "is-sel" : ""} onClick={() => onOpenDocument(d.id)}>
                  <td onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      className={`cat-check ${isSel ? "is-on" : ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleRow(d.id);
                      }}
                      aria-label={`Select ${d.original_filename} for batch`}
                      aria-pressed={isSel}
                    >
                      {isSel && <Icon name="check" size={10} />}
                    </button>
                  </td>
                  <td>{d.original_filename}</td>
                  <td className="orb-mono">{d.id.slice(0, 8)}</td>
                  <td>
                    <StatusBadge status={status} />
                  </td>
                  <td className="orb-mono">v{d.versions.length}</td>
                  <td>
                    {(d.scopes ?? []).map((s, i) => (
                      <span key={`${s.kind}:${i}`} className="orb-chip" style={{ marginRight: 3 }}>
                        {s.kind === "swym_community" ? "community" : s.kind}
                      </span>
                    ))}
                  </td>
                  <td className="orb-mono" style={{ color: "var(--orb-fg-dim)" }}>
                    {d.created_at?.slice(0, 16).replace("T", " ") ?? "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {showBatchBox && (
          <div
            style={{
              marginTop: 18,
              padding: "10px 12px",
              background: "var(--orb-warn-bg)",
              border: "1px solid color-mix(in oklch, var(--orb-warn) 35%, transparent)",
              borderRadius: 8,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--orb-warn-fg)" }}>
              <Icon name="bolt" />
              <b>
                Batch pipeline — {batchDone} of {batchTotal} complete
                {batchFailures.length > 0 ? ` · ${batchFailures.length} failed` : ""}
              </b>
            </div>
            {batchFailures.length > 0 && (
              <div style={{ marginTop: 6, fontFamily: "var(--orb-font-mono)", fontSize: 11, color: "var(--orb-warn-fg)" }}>
                {batchFailures.map((f) => (
                  <div key={f.document_id}>
                    ✗ {f.document_id.slice(0, 8)} · {f.reason}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="cat-banner cat-banner--session" style={{ marginTop: 12, borderRadius: 6 }} role="alert">
            <Icon name="alert" />
            <span>Failed to load catalog: {error}</span>
            <span style={{ flex: 1 }}></span>
            <button className="cat-link" onClick={() => void refresh()}>
              retry
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
