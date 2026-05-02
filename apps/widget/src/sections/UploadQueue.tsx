import React, { useCallback, useRef, useState } from "react";

import { ApiError, uploadDocument } from "../api/client";

const CONCURRENCY = 2;

interface Props {
  apiBaseUrl: string;
  /** Bumped after each successful upload so sibling sections refresh. */
  onUploaded: () => void;
}

type ItemStatus = "queued" | "uploading" | "done" | "failed";

interface QueueItem {
  id: string;
  file: File;
  relativePath: string;
  status: ItemStatus;
  error?: string;
}

let _id = 0;
const nextId = () => `q-${++_id}`;

function relPath(file: File): string {
  // `webkitRelativePath` is populated by `<input webkitdirectory />`; falls
  // back to the bare name for individual file/multi-file pickers.
  type WebkitFile = File & { webkitRelativePath?: string };
  const wf = file as WebkitFile;
  return wf.webkitRelativePath && wf.webkitRelativePath.length > 0
    ? wf.webkitRelativePath
    : file.name;
}

export const UploadQueue: React.FC<Props> = ({ apiBaseUrl, onUploaded }) => {
  const [items, setItems] = useState<QueueItem[]>([]);
  const inflightRef = useRef<number>(0);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const multiInputRef = useRef<HTMLInputElement | null>(null);

  const updateItem = useCallback(
    (id: string, patch: Partial<QueueItem>) => {
      setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)));
    },
    [],
  );

  const drain = useCallback(() => {
    setItems((prev) => {
      let inflight = inflightRef.current;
      const next = prev.map((it) => {
        if (inflight >= CONCURRENCY) return it;
        if (it.status !== "queued") return it;
        inflight += 1;
        inflightRef.current = inflight;
        // Kick off the upload outside the setState callback so we don't
        // block the React commit. Uses item id to address the row later.
        void (async () => {
          try {
            await uploadDocument(it.file, { baseUrl: apiBaseUrl });
            updateItem(it.id, { status: "done" });
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
            inflightRef.current -= 1;
            // Re-enter the scheduler to pick up the next queued row.
            setTimeout(drain, 0);
          }
        })();
        return { ...it, status: "uploading" as const };
      });
      return next;
    });
  }, [apiBaseUrl, onUploaded, updateItem]);

  const enqueue = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const additions: QueueItem[] = [];
      for (let i = 0; i < files.length; i += 1) {
        const file = files.item(i);
        if (!file) continue;
        additions.push({
          id: nextId(),
          file,
          relativePath: relPath(file),
          status: "queued",
        });
      }
      if (additions.length === 0) return;
      setItems((prev) => [...prev, ...additions]);
      // setTimeout so the queue mutation flushes before drain reads it.
      setTimeout(drain, 0);
    },
    [drain],
  );

  const clearDone = () =>
    setItems((prev) => prev.filter((it) => it.status !== "done"));

  return (
    <section className="kw-card" aria-label="Upload">
      <h3 className="kw-card__title">Upload</h3>
      <div className="kw-upload">
        <div className="kw-upload__buttons">
          <button
            type="button"
            className="kw-btn kw-btn--primary"
            onClick={() => fileInputRef.current?.click()}
          >
            Add file
          </button>
          <button
            type="button"
            className="kw-btn"
            onClick={() => multiInputRef.current?.click()}
          >
            Add multiple
          </button>
          <button
            type="button"
            className="kw-btn"
            onClick={() => folderInputRef.current?.click()}
          >
            Add folder
          </button>
          {items.some((it) => it.status === "done") && (
            <button type="button" className="kw-btn" onClick={clearDone}>
              Clear done
            </button>
          )}
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
          <ul className="kw-upload__queue">
            {items.map((it) => (
              <li key={it.id} className="kw-upload__item" title={it.relativePath}>
                <span className="kw-doc-list__name">{it.relativePath}</span>
                <span
                  className={
                    it.status === "done"
                      ? "kw-badge kw-badge--ok"
                      : it.status === "failed"
                        ? "kw-badge kw-badge--err"
                        : it.status === "uploading"
                          ? "kw-badge kw-badge--info"
                          : "kw-badge"
                  }
                >
                  {it.status}
                </span>
                {it.error && (
                  <span
                    className="kw-error"
                    style={{ gridColumn: "1 / -1" }}
                    title={it.error}
                  >
                    {it.error}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
};
