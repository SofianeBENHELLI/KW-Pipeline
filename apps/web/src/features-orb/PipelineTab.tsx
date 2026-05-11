import { useState } from "react";

import type { ApiDocument, ApiRawExtraction, ApiSemanticDocument } from "../api/types";
import { latestVersion } from "../domain/document";
import { Btn, Icon, Mono, SectionHeading } from "../ui/orb";
import { MetaRow } from "../ui/orb/atoms";

export type FsmAction = "extract" | "semantic" | "validate" | "reject";

export interface PipelineTabProps {
  doc: ApiDocument;
  raw: ApiRawExtraction | null;
  semantic: ApiSemanticDocument | null;
  markdown: string | null;
  reviewerNote: string;
  onReviewerNote: (next: string) => void;
  busy: FsmAction | null;
  actionError: string | null;
  can: { extract: boolean; semantic: boolean; validate: boolean; reject: boolean };
  onAction: (action: FsmAction) => void;
}

const EXTRACT_TAB = ["extraction.json", "page-spans", "tables"] as const;
const MD_TAB = ["preview", "source", "diff vs prev"] as const;

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

/**
 * Variant-A "Pipeline & FSM" tab — the multi-card grid.
 *
 * Layout (per the mockup `rwA-grid`):
 *   Row 1 (full width): Lifecycle card (FSM actions + reviewer note + HITL hint)
 *   Row 2 columns:      Raw extraction (left, tall) · Document detail (right)
 *   Row 3 columns:      Semantic markdown (left, tall) · Versions (right)
 */
export function PipelineTab({
  doc,
  raw,
  semantic,
  markdown,
  reviewerNote,
  onReviewerNote,
  busy,
  actionError,
  can,
  onAction,
}: PipelineTabProps) {
  const [extractTab, setExtractTab] = useState<typeof EXTRACT_TAB[number]>("extraction.json");
  const [mdTab, setMdTab] = useState<typeof MD_TAB[number]>("preview");

  const latest = latestVersion(doc);
  const status = latest?.status ?? "UNKNOWN";

  return (
    <>
      <section className="rwA-grid">
        {/* ---- Lifecycle card (full-width) ---- */}
        <div className="orb-card rwA-fsmcard">
          <div className="rwA-cardhead">
            <SectionHeading>Lifecycle</SectionHeading>
            <span className="rwA-flow">
              STORED → EXTRACTED → SEMANTIC_READY → <span style={{ color: "var(--orb-fg)" }}>VALIDATED</span>
            </span>
          </div>
          <div className="rwA-fsm">
            <div className="rwA-fsm-actions">
              <Btn
                icon={<Icon name="bolt" />}
                disabled={!can.extract || busy !== null}
                onClick={() => onAction("extract")}
                title={can.extract ? "Re-run extractor" : `Disabled in ${status}`}
              >
                {busy === "extract" ? "Extracting…" : "Extract"}
              </Btn>
              <Btn
                icon={<Icon name="spark" />}
                disabled={!can.semantic || busy !== null}
                onClick={() => onAction("semantic")}
                title={can.semantic ? "Generate semantic" : `Disabled in ${status}`}
              >
                {busy === "semantic" ? "Generating…" : "Semantic"}
              </Btn>
              <div style={{ flex: 1 }} />
              <Btn
                kind="ghost"
                disabled={!can.reject || busy !== null}
                onClick={() => onAction("reject")}
              >
                {busy === "reject" ? "Rejecting…" : "Reject"}
              </Btn>
              <Btn
                kind="primary"
                icon={<Icon name="check" />}
                disabled={!can.validate || busy !== null}
                onClick={() => onAction("validate")}
              >
                {busy === "validate" ? "Validating…" : "Validate"}
              </Btn>
            </div>
            <textarea
              className="rwA-note"
              placeholder="Reviewer note (optional) — appended to audit trail on validate/reject…"
              value={reviewerNote}
              onChange={(event) => onReviewerNote(event.target.value)}
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

        {/* ---- Raw extraction (left, tall) ---- */}
        <div className="orb-card rwA-tall">
          <div className="rwA-cardhead">
            <SectionHeading>Raw extraction</SectionHeading>
            <div className="rwA-tabs">
              {EXTRACT_TAB.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  className={`rwA-tab ${extractTab === tab ? "is-active" : ""}`.trim()}
                  onClick={() => setExtractTab(tab)}
                >
                  {tab}
                </button>
              ))}
            </div>
          </div>
          {raw ? (
            <pre className="rwA-code orb-mono orb-scroll">
              {extractTab === "extraction.json"
                ? JSON.stringify({ text_length: raw.text?.length ?? 0, sections: raw.sections?.length ?? 0, warnings: raw.warnings }, null, 2)
                : extractTab === "page-spans"
                  ? raw.text?.slice(0, 1200) ?? "(no text)"
                  : (raw.sections ?? []).map((s) => `# ${s.heading ?? ""}\n${s.text ?? ""}`).join("\n\n").slice(0, 1200) || "(no sections)"}
            </pre>
          ) : (
            <p style={{ padding: "12px 14px", color: "var(--orb-fg-muted)", fontSize: 11 }}>
              No raw extraction yet — run <Mono>Extract</Mono> when the version is <Mono>STORED</Mono>.
            </p>
          )}
        </div>

        {/* ---- Document detail (right) ---- */}
        <div className="orb-card">
          <div className="rwA-cardhead">
            <SectionHeading>Document detail</SectionHeading>
            <span className="orb-mono rwA-hint">GET /documents/{doc.id.slice(0, 8)}</span>
          </div>
          <div style={{ padding: "6px 14px 14px" }}>
            <MetaRow label="ID">
              <Mono>{doc.id}</Mono>
            </MetaRow>
            <MetaRow label="Filename">{doc.original_filename}</MetaRow>
            <MetaRow label="Versions">
              {doc.versions.length} · latest <Mono>{latest?.id ?? "—"}</Mono>
            </MetaRow>
            <MetaRow label="Bytes">{formatBytes(latest?.file_size)}</MetaRow>
            <MetaRow label="Content-type">
              <Mono>{latest?.content_type ?? "—"}</Mono>
            </MetaRow>
            <MetaRow label="SHA-256">
              <Mono>{latest?.sha256 ?? "—"}</Mono>
            </MetaRow>
            <MetaRow label="Scope">{(doc.scopes ?? []).map((s) => s.kind).join(" + ") || "—"}</MetaRow>
            <MetaRow label="Created">
              <Mono>{latest?.created_at ?? "—"}</Mono>
            </MetaRow>
            {latest?.failure_reason && (
              <MetaRow label="Failure">
                <span style={{ color: "var(--orb-err-fg)" }}>{latest.failure_reason}</span>
              </MetaRow>
            )}
          </div>
        </div>

        {/* ---- Semantic markdown (left, tall) ---- */}
        <div className="orb-card rwA-tall">
          <div className="rwA-cardhead">
            <SectionHeading>Semantic markdown</SectionHeading>
            <div className="rwA-tabs">
              {MD_TAB.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  className={`rwA-tab ${mdTab === tab ? "is-active" : ""}`.trim()}
                  onClick={() => setMdTab(tab)}
                >
                  {tab}
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
              {(semantic.sections ?? []).slice(0, 6).map((section) => (
                <div key={section.id}>
                  <h3>{section.heading || "(untitled section)"}</h3>
                  <p>{section.text?.slice(0, 280)}{(section.text?.length ?? 0) > 280 ? "…" : ""}</p>
                </div>
              ))}
              <div className="rwA-md-fade" />
            </div>
          ) : (
            <p style={{ padding: "12px 14px", color: "var(--orb-fg-muted)", fontSize: 11 }}>
              No semantic output yet — run <Mono>Semantic</Mono> to generate it.
            </p>
          )}
        </div>

        {/* ---- Versions (right) ---- */}
        <div className="orb-card">
          <div className="rwA-cardhead">
            <SectionHeading>Versions</SectionHeading>
            <span className="orb-mono rwA-hint">{doc.versions.length} total</span>
          </div>
          <div className="rwA-versbody">
            {doc.versions.slice(-6).reverse().map((version, index) => {
              const isCurrent = version.id === latest?.id;
              return (
                <div
                  key={version.id}
                  className={`rwA-versrow ${isCurrent ? "is-cur" : ""}`.trim()}
                >
                  <span className="orb-mono" style={{ width: 30 }}>v{version.version_number}</span>
                  <span style={{ color: "var(--orb-fg-muted)" }}>{version.status}</span>
                  <span className="orb-mono rwA-hint" style={{ flex: 1, textAlign: "right" }}>
                    {index === 0 ? "current" : version.created_at?.slice(0, 10) ?? "—"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </section>
    </>
  );
}
