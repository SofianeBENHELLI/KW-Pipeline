import { useRef, useState } from "react";

import { ApiError, uploadDocument } from "../api/client";

import { Btn, Icon } from "./atoms";

interface UploadProgress {
  filename: string;
  state: "uploading" | "done" | "failed";
  reason?: string;
  documentId?: string;
}

export interface UploadZoneProps {
  onUploaded: () => void;
  onOpenDocument?: (id: string) => void;
}

/**
 * Drag-drop + click-to-pick upload surface. Streams every file through
 * `POST /documents/upload` sequentially, surfacing per-file progress so
 * an operator can recover from per-file failures without re-picking the
 * whole batch.
 */
export function UploadZone({ onUploaded, onOpenDocument }: UploadZoneProps) {
  const [dragging, setDragging] = useState(false);
  const [items, setItems] = useState<UploadProgress[]>([]);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const runUpload = async (files: FileList | File[]) => {
    if (busy) return;
    const list = Array.from(files);
    if (list.length === 0) return;
    setItems(list.map((f) => ({ filename: f.name, state: "uploading" })));
    setBusy(true);
    try {
      for (let i = 0; i < list.length; i++) {
        const file = list[i];
        try {
          const response = await uploadDocument(file);
          setItems((current) =>
            current.map((entry, idx) =>
              idx === i ? { ...entry, state: "done", documentId: response.document_id } : entry,
            ),
          );
        } catch (err) {
          const reason =
            err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
          setItems((current) =>
            current.map((entry, idx) => (idx === i ? { ...entry, state: "failed", reason } : entry)),
          );
        }
      }
    } finally {
      setBusy(false);
      onUploaded();
    }
  };

  return (
    <div
      style={{
        border: `1px ${dragging ? "solid" : "dashed"} ${dragging ? "var(--orb-fg)" : "var(--orb-border)"}`,
        background: dragging ? "var(--orb-bg-hov)" : "var(--orb-bg-elev)",
        borderRadius: 8,
        padding: 12,
        margin: "12px 0",
        transition: "background .12s, border-color .12s",
      }}
      onDragOver={(e) => {
        e.preventDefault();
        if (!dragging) setDragging(true);
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files.length > 0) void runUpload(e.dataTransfer.files);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        style={{ display: "none" }}
        onChange={(e) => {
          if (e.target.files) void runUpload(e.target.files);
          e.target.value = "";
        }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Icon name="archive" />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: "var(--orb-fg)" }}>
            Drop files here to upload — or click <button type="button" className="cat-link" onClick={() => inputRef.current?.click()}>pick files</button>
          </div>
          <div style={{ fontSize: 11, color: "var(--orb-fg-muted)" }}>
            PDF · DOCX · PPTX · text — files stream straight into the catalog as STORED documents.
          </div>
        </div>
        <Btn xs icon={<Icon name="plus" />} onClick={() => inputRef.current?.click()} disabled={busy}>
          {busy ? "Uploading…" : "Add files"}
        </Btn>
      </div>
      {items.length > 0 && (
        <div style={{ marginTop: 10, fontSize: 11, fontFamily: "var(--orb-font-mono)", color: "var(--orb-fg-muted)" }}>
          {items.map((item, i) => (
            <div key={`${item.filename}-${i}`} style={{ display: "flex", gap: 6, padding: "3px 0", borderBottom: "1px dashed var(--orb-rule)" }}>
              <span style={{ width: 90, color: item.state === "done" ? "var(--orb-ok-fg)" : item.state === "failed" ? "var(--orb-err-fg)" : "var(--orb-info-fg)" }}>
                {item.state}
              </span>
              <span style={{ flex: 1, color: "var(--orb-fg)" }}>{item.filename}</span>
              {item.state === "failed" && item.reason && (
                <span style={{ color: "var(--orb-err-fg)" }}>{item.reason}</span>
              )}
              {item.state === "done" && onOpenDocument && item.documentId && (
                <button type="button" className="sp-jump" onClick={() => onOpenDocument(item.documentId!)}>
                  open <Icon name="ext" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
