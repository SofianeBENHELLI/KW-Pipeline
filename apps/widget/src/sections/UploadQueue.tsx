import React, { useCallback, useMemo, useRef, useState } from "react";

import { ApiError, uploadDocumentWithProgress } from "../api/client";
import { Icon } from "../components/icons";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";

const CONCURRENCY = 2;

/**
 * Upload scope (EPIC-D #218 — UX preview only).
 *
 * Models the future selector ahead of the backend — today the upload
 * still POSTs only `file`. When `/documents/upload` accepts
 * `scope_kind` / `scope_ref`, the dropdown's value is already shaped
 * correctly and only `submit()` needs to change.
 */
export type Scope =
  | { kind: "personal"; ref: string }
  | { kind: "swym_community"; ref: string };

const DEFAULT_SCOPE: Scope = { kind: "personal", ref: "me" };

interface Props {
  apiBaseUrl: string;
  /** Bumped after each successful upload so sibling sections refresh. */
  onUploaded: () => void;
  /** Called whenever the in-flight count changes — drives the rail badge. */
  onInFlightChange?: (count: number) => void;
}

type ItemStatus = "queued" | "uploading" | "done" | "failed";

interface QueueItem {
  id: string;
  file: File;
  relativePath: string;
  /** Folder root (first path segment) when picked via the folder picker. */
  folderRoot: string | null;
  status: ItemStatus;
  /** 0 to 1; only meaningful while `status === "uploading"`. */
  progress: number;
  error?: string;
}

let _id = 0;
const nextId = () => `q-${++_id}`;

function relPath(file: File): string {
  return file.webkitRelativePath && file.webkitRelativePath.length > 0
    ? file.webkitRelativePath
    : file.name;
}

function folderRootOf(relativePath: string): string | null {
  const slash = relativePath.indexOf("/");
  if (slash <= 0) return null;
  return relativePath.slice(0, slash);
}

export const UploadQueue: React.FC<Props> = ({ apiBaseUrl, onUploaded, onInFlightChange }) => {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  // Scope selection (EPIC-D #218 mockup). Component-only state — no
  // localStorage, no URL, no wire field. The selected value is purely
  // visual until the backend lands; see the TODO near the upload call.
  const [selectedScope, setSelectedScope] = useState<Scope>(DEFAULT_SCOPE);
  const inflightRef = useRef<number>(0);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const multiInputRef = useRef<HTMLInputElement | null>(null);

  const updateItem = useCallback((id: string, patch: Partial<QueueItem>) => {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)));
  }, []);

  const reportInFlight = useCallback(
    (next: number) => {
      inflightRef.current = next;
      onInFlightChange?.(next);
    },
    [onInFlightChange],
  );

  const drain = useCallback(() => {
    setItems((prev) => {
      let inflight = inflightRef.current;
      const next = prev.map((it) => {
        if (inflight >= CONCURRENCY) return it;
        if (it.status !== "queued") return it;
        inflight += 1;
        reportInFlight(inflight);
        // Kick off the upload outside the setState callback so we don't
        // block the React commit. Uses item id to address the row later.
        void (async () => {
          try {
            // TODO(EPIC-D): pass selectedScope to uploadDocument once /documents/upload accepts scope_kind + scope_ref. ADR-020.
            await uploadDocumentWithProgress(it.file, {
              baseUrl: apiBaseUrl,
              onProgress: (fraction) => updateItem(it.id, { progress: fraction }),
            });
            updateItem(it.id, { status: "done", progress: 1 });
            onUploaded();
          } catch (error) {
            const message =
              error instanceof ApiError
                ? `${error.code}: ${error.detail}`
                : error instanceof Error
                  ? error.message
                  : "Upload failed";
            updateItem(it.id, { status: "failed", error: message });
          } finally {
            reportInFlight(inflightRef.current - 1);
            // Re-enter the scheduler to pick up the next queued row.
            setTimeout(drain, 0);
          }
        })();
        return { ...it, status: "uploading" as const };
      });
      return next;
    });
  }, [apiBaseUrl, onUploaded, reportInFlight, updateItem]);

  const enqueue = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const additions: QueueItem[] = [];
      for (let i = 0; i < files.length; i += 1) {
        const file = files.item(i);
        if (!file) continue;
        const relativePath = relPath(file);
        additions.push({
          id: nextId(),
          file,
          relativePath,
          folderRoot: folderRootOf(relativePath),
          status: "queued",
          progress: 0,
        });
      }
      if (additions.length === 0) return;
      setItems((prev) => [...prev, ...additions]);
      // setTimeout so the queue mutation flushes before drain reads it.
      setTimeout(drain, 0);
    },
    [drain],
  );

  const enqueueDataTransfer = useCallback(
    (dt: DataTransfer | null) => {
      if (!dt) return;
      // The simple path — `dt.files` covers both single-file drops and
      // multi-file drops. Folder drops are not supported via DataTransfer
      // without `webkitGetAsEntry()`, which would meaningfully bloat
      // this handler; folder picker still covers that case.
      enqueue(dt.files);
    },
    [enqueue],
  );

  const clearDone = () =>
    setItems((prev) => prev.filter((it) => it.status !== "done"));

  const stats = useMemo(() => {
    const totals = { total: items.length, done: 0, failed: 0, inflight: 0 };
    for (const it of items) {
      if (it.status === "done") totals.done += 1;
      else if (it.status === "failed") totals.failed += 1;
      else if (it.status === "uploading") totals.inflight += 1;
    }
    return totals;
  }, [items]);

  // Aggregate fraction across all queue items: queued counts as 0,
  // uploading uses live progress, done/failed count as 1.
  const aggregateFraction = useMemo(() => {
    if (items.length === 0) return 0;
    const sum = items.reduce((acc, it) => {
      if (it.status === "done" || it.status === "failed") return acc + 1;
      if (it.status === "uploading") return acc + it.progress;
      return acc;
    }, 0);
    return Math.min(1, sum / items.length);
  }, [items]);

  // Folder summary — show only when at least one item belongs to a folder.
  const folderSummary = useMemo(() => {
    const byRoot = new Map<string, { queued: number; done: number; failed: number }>();
    for (const it of items) {
      if (!it.folderRoot) continue;
      const prev = byRoot.get(it.folderRoot) ?? { queued: 0, done: 0, failed: 0 };
      if (it.status === "done") prev.done += 1;
      else if (it.status === "failed") prev.failed += 1;
      else prev.queued += 1;
      byRoot.set(it.folderRoot, prev);
    }
    if (byRoot.size === 0) return null;
    const [root, counts] = Array.from(byRoot.entries())[0];
    return { root, ...counts };
  }, [items]);

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);
  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);
  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setIsDragging(false);
      enqueueDataTransfer(e.dataTransfer);
    },
    [enqueueDataTransfer],
  );

  // Scope picker change handler — only the personal option is real.
  // The Swym option is disabled at the <option> level so this guard
  // is belt-and-braces: even if a synthetic event slips through (or a
  // future browser ignores `disabled`), we never accept it.
  const handleScopeChange = useCallback(
    (event: React.ChangeEvent<HTMLSelectElement>) => {
      if (event.target.value === "personal") {
        setSelectedScope({ kind: "personal", ref: "me" });
      }
      // Swym option is disabled — ignore any other value silently.
    },
    [],
  );

  return (
    <section className="kw-section" aria-label="Upload">
      <SectionHeader
        icon="upload-cloud"
        title="Upload"
        meta={
          stats.total > 0
            ? `${stats.inflight} of ${stats.total} in flight`
            : undefined
        }
      />

      {/* Scope picker (EPIC-D #218 mockup). UX-only — does NOT change
          the upload wire contract. The Swym option is disabled until
          the backend can accept scope_kind / scope_ref. */}
      <div className="kw-scope" aria-label="Upload destination">
        <label className="kw-scope__label" htmlFor="kw-upload-scope">
          Destination
        </label>
        <select
          id="kw-upload-scope"
          className="kw-scope__select"
          value={selectedScope.kind}
          onChange={handleScopeChange}
          data-testid="kw-upload-scope-select"
        >
          <option value="personal">Personal workspace — Visible only to you</option>
          <option
            value="swym_community"
            disabled
            title="Available once your 3DSwym communities are connected (EPIC-D)"
          >
            Swym community… (Coming soon)
          </option>
        </select>
        {selectedScope.kind === "personal" && (
          <div className="kw-scope__sub">Visible only to you</div>
        )}
        <div
          className="kw-scope__pill"
          aria-hidden="true"
          title="Available once your 3DSwym communities are connected (EPIC-D)"
        >
          Coming soon
        </div>
        <div
          className="kw-scope__banner"
          role="note"
          data-testid="kw-upload-scope-banner"
        >
          <Icon name="info" size={12} /> New: pick where your document goes.
          Scope-aware filtering arrives with the next ingestion update.
        </div>
      </div>

      <div
        className={isDragging ? "kw-drop kw-drop--active" : "kw-drop"}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <div className="kw-drop__icon" aria-hidden="true">
          <Icon name="upload-cloud" size={28} />
        </div>
        <div className="kw-drop__strong">Drop files or folders here</div>
        <div className="kw-drop__sub">
          Max 2 concurrent · keeps folder paths · pushes to /documents/upload
        </div>
      </div>

      <div className="kw-upload__buttons">
        <button
          type="button"
          className="kw-btn kw-btn--primary"
          onClick={() => fileInputRef.current?.click()}
        >
          <Icon name="plus" size={12} /> Add file
        </button>
        <button
          type="button"
          className="kw-btn"
          onClick={() => multiInputRef.current?.click()}
        >
          <Icon name="files" size={12} /> Multiple
        </button>
        <button
          type="button"
          className="kw-btn"
          onClick={() => folderInputRef.current?.click()}
        >
          <Icon name="folder" size={12} /> Folder
        </button>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        className="kw-upload__file-input"
        onChange={(e) => {
          enqueue(e.target.files);
          e.target.value = "";
        }}
      />
      <input
        ref={multiInputRef}
        type="file"
        multiple
        className="kw-upload__file-input"
        onChange={(e) => {
          enqueue(e.target.files);
          e.target.value = "";
        }}
      />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        // The directory attributes are non-standard but supported by every
        // browser the dashboard targets. Casting to `any` because the
        // React DOM typings don't model them.
        /* eslint-disable @typescript-eslint/no-explicit-any */
        {...({ webkitdirectory: "", directory: "", mozdirectory: "" } as any)}
        /* eslint-enable @typescript-eslint/no-explicit-any */
        className="kw-upload__file-input"
        onChange={(e) => {
          enqueue(e.target.files);
          e.target.value = "";
        }}
      />

      {items.length > 0 && (
        <ul className="kw-queue">
          {items.map((it) => (
            <li
              key={it.id}
              className={
                it.status === "failed"
                  ? "kw-queue__row kw-queue__row--failed"
                  : "kw-queue__row"
              }
              title={it.relativePath}
            >
              <span className="kw-queue__name">{it.relativePath}</span>
              {it.status === "queued" && <StatusBadge status="INGESTED" label="QUEUED" />}
              {it.status === "uploading" && (
                <span className="kw-queue__progress">
                  <span className="kw-spinner" aria-hidden="true" />
                  <span className="kw-mono kw-mono--accent">
                    {Math.round(it.progress * 100)}%
                  </span>
                </span>
              )}
              {it.status === "done" && <StatusBadge status="VALIDATED" label="DONE" />}
              {it.status === "failed" && <StatusBadge status="FAILED" />}
              {it.error && <div className="kw-queue__err">{it.error}</div>}
            </li>
          ))}
        </ul>
      )}

      {items.length > 0 && (
        <div className="kw-progress">
          <div className="kw-progress__track">
            <span style={{ width: `${Math.round(aggregateFraction * 100)}%` }} />
          </div>
          <span className="kw-mono kw-mono--muted">
            {stats.done} / {stats.total} done · {Math.round(aggregateFraction * 100)}%
          </span>
        </div>
      )}

      {folderSummary && (
        <div className="kw-folder-summary">
          <div className="kw-folder-summary__head">
            <Icon name="folder" size={12} /> {folderSummary.root}
          </div>
          <div className="kw-folder-summary__nums">
            <b>{folderSummary.queued}</b> in flight · <b>{folderSummary.done}</b> done
            {folderSummary.failed > 0 && (
              <>
                {" · "}
                <b>{folderSummary.failed}</b> failed — re-pick the file to retry.
              </>
            )}
          </div>
        </div>
      )}

      {items.length > 0 && (
        <div className="kw-queue__footer">
          <span className="kw-status">
            {stats.total} files · {stats.done} done
            {stats.failed > 0 && ` · ${stats.failed} failed`}
          </span>
          {stats.done > 0 && (
            <button type="button" className="kw-btn kw-btn--ghost" onClick={clearDone}>
              Clear done
            </button>
          )}
        </div>
      )}
    </section>
  );
};
