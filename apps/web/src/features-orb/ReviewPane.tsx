import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  extractVersion,
  generateSemantic,
  getDocument,
  getExtraction,
  getMarkdown,
  getSemantic,
  rejectVersion,
  validateVersion,
} from "../api/client";
import type {
  ApiDocument,
  ApiRawExtraction,
  ApiSemanticDocument,
} from "../api/types";
import { latestVersion } from "../domain/document";
import { Btn, Card, Mono, OrbScopeChip, OrbStatusBadge, SectionHeading } from "../ui/orb";
import { MetaRow } from "../ui/orb/atoms";

type ReviewAction = "extract" | "semantic" | "validate" | "reject";

const EXTRACTABLE = new Set(["STORED", "EXTRACTED", "FAILED"]);
const SEMANTICABLE = new Set(["EXTRACTED", "SEMANTIC_READY", "NEEDS_REVIEW"]);
const REVIEWABLE = new Set(["NEEDS_REVIEW", "SEMANTIC_READY"]);

interface ActionState {
  busy: ReviewAction | null;
  error: string | null;
}

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function formatDateLong(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
}

export interface ReviewPaneProps {
  documentId: string;
  onMutated?: (next: ApiDocument) => void;
}

/**
 * Phase-2 review pane — opens to the right of the catalog when a row is
 * selected. Fetches the document + its latest extraction + semantic +
 * markdown in parallel and renders them stacked. Action buttons (Extract
 * / Semantic / Validate / Reject) are state-gated against the latest
 * version's FSM state. Mutations refetch the document and bubble up via
 * onMutated so the catalog row's status badge stays in sync.
 */
export function ReviewPane({ documentId, onMutated }: ReviewPaneProps) {
  const [doc, setDoc] = useState<ApiDocument | null>(null);
  const [raw, setRaw] = useState<ApiRawExtraction | null>(null);
  const [semantic, setSemantic] = useState<ApiSemanticDocument | null>(null);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [actionState, setActionState] = useState<ActionState>({ busy: null, error: null });
  const [reviewerNote, setReviewerNote] = useState("");
  const inFlightRef = useRef<Set<ReviewAction>>(new Set());
  const abortRef = useRef<AbortController | null>(null);

  const fetchAll = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setFetchError(null);
    try {
      const fresh = await getDocument(documentId);
      if (controller.signal.aborted) return;
      setDoc(fresh);
      const latest = latestVersion(fresh);
      if (!latest) {
        setRaw(null);
        setSemantic(null);
        setMarkdown(null);
        return;
      }
      const [rawRes, semRes, mdRes] = await Promise.allSettled([
        getExtraction(documentId, latest.id, { signal: controller.signal }),
        getSemantic(documentId, latest.id, { signal: controller.signal }),
        getMarkdown(documentId, latest.id),
      ]);
      if (controller.signal.aborted) return;
      setRaw(rawRes.status === "fulfilled" ? rawRes.value : null);
      setSemantic(semRes.status === "fulfilled" ? semRes.value : null);
      setMarkdown(mdRes.status === "fulfilled" ? mdRes.value : null);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      setFetchError(message);
      setDoc(null);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    fetchAll();
    return () => abortRef.current?.abort();
  }, [fetchAll]);

  const latest = doc ? latestVersion(doc) : null;
  const status = latest?.status;
  const can = useMemo(
    () => ({
      extract: !!status && EXTRACTABLE.has(status),
      semantic: !!status && SEMANTICABLE.has(status),
      validate: !!status && REVIEWABLE.has(status),
      reject: !!status && REVIEWABLE.has(status),
    }),
    [status],
  );

  const runAction = async (action: ReviewAction) => {
    if (!doc || !latest) return;
    if (inFlightRef.current.has(action)) return;
    inFlightRef.current.add(action);
    setActionState({ busy: action, error: null });
    try {
      switch (action) {
        case "extract":
          await extractVersion(doc.id, latest.id);
          break;
        case "semantic":
          await generateSemantic(doc.id, latest.id);
          break;
        case "validate":
          await validateVersion(doc.id, latest.id, reviewerNote || undefined);
          break;
        case "reject":
          await rejectVersion(doc.id, latest.id, reviewerNote || undefined);
          break;
      }
      setActionState({ busy: null, error: null });
      const refreshed = await getDocument(doc.id);
      setDoc(refreshed);
      onMutated?.(refreshed);
      // Re-pull derived artefacts; status changes typically affect them.
      void fetchAll();
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      setActionState({ busy: null, error: message });
    } finally {
      inFlightRef.current.delete(action);
    }
  };

  if (loading && !doc) {
    return <div className="orb-review__placeholder">Loading document {documentId.slice(0, 8)}…</div>;
  }
  if (fetchError) {
    return (
      <div className="orb-review__placeholder orb-review__placeholder--error" role="alert">
        Failed to load document: {fetchError}
      </div>
    );
  }
  if (!doc || !latest) {
    return <div className="orb-review__placeholder">No version data for this document.</div>;
  }

  return (
    <div className="orb-review">
      <header className="orb-review__head">
        <div className="orb-review__title">
          <h2 className="orb-review__filename">{doc.original_filename}</h2>
          <div className="orb-review__title-meta">
            <OrbStatusBadge status={latest.status} />
            <Mono className="orb-review__doc-id">{doc.id}</Mono>
            <span className="orb-review__sep">·</span>
            <span>v{doc.versions.length}</span>
            <span className="orb-review__sep">·</span>
            <span className="orb-review__scopes">
              {(doc.scopes ?? []).map((scope, index) => (
                <OrbScopeChip
                  key={`${scope.kind}:${scope.ref}:${index}`}
                  scope={scope.kind}
                  title={`${scope.kind}: ${scope.ref}`}
                />
              ))}
            </span>
          </div>
        </div>
      </header>

      <section className="orb-review__section">
        <SectionHeading>Version metadata</SectionHeading>
        <Card className="orb-review__card">
          <MetaRow label="document_id">
            <Mono>{doc.id}</Mono>
          </MetaRow>
          <MetaRow label="version_id">
            <Mono>{latest.id}</Mono>
          </MetaRow>
          <MetaRow label="filename">{latest.filename}</MetaRow>
          <MetaRow label="content_type">
            <Mono>{latest.content_type}</Mono>
          </MetaRow>
          <MetaRow label="file_size">{formatBytes(latest.file_size)}</MetaRow>
          <MetaRow label="sha256">
            <Mono>{latest.sha256}</Mono>
          </MetaRow>
          <MetaRow label="created_at">
            <Mono>{formatDateLong(latest.created_at)}</Mono>
          </MetaRow>
          {latest.failure_reason && (
            <MetaRow label="failure">
              <span style={{ color: "var(--orb-err-fg)" }}>{latest.failure_reason}</span>
            </MetaRow>
          )}
        </Card>
      </section>

      <section className="orb-review__section">
        <SectionHeading>Raw extraction</SectionHeading>
        <Card className="orb-review__card orb-review__card--code">
          {raw ? (
            <pre className="orb-review__pre orb-mono orb-scroll">
              {raw.text || (raw.sections ?? []).map((s) => `# ${s.heading ?? ""}\n${s.text ?? ""}`).join("\n\n")}
            </pre>
          ) : (
            <p className="orb-review__placeholder">
              No raw extraction yet — run <Mono>Extract</Mono> when the version is in <Mono>STORED</Mono>.
            </p>
          )}
        </Card>
      </section>

      <section className="orb-review__section">
        <SectionHeading>Markdown preview</SectionHeading>
        <Card className="orb-review__card orb-review__card--code">
          {markdown ? (
            <pre className="orb-review__pre orb-mono orb-scroll">{markdown}</pre>
          ) : (
            <p className="orb-review__placeholder">
              No markdown yet — run <Mono>Semantic</Mono> to generate it.
            </p>
          )}
        </Card>
      </section>

      <section className="orb-review__section">
        <SectionHeading>Reviewer note</SectionHeading>
        <textarea
          className="orb-input orb-review__note"
          rows={3}
          placeholder="Optional context the audit trail will capture on validate / reject…"
          value={reviewerNote}
          onChange={(event) => setReviewerNote(event.target.value)}
        />
      </section>

      <footer className="orb-review__actions">
        <Btn
          kind="default"
          onClick={() => runAction("extract")}
          disabled={!can.extract || actionState.busy !== null}
          title={can.extract ? undefined : `Disabled in status ${status}`}
        >
          {actionState.busy === "extract" ? "Extracting…" : "Extract"}
        </Btn>
        <Btn
          kind="default"
          onClick={() => runAction("semantic")}
          disabled={!can.semantic || actionState.busy !== null}
          title={can.semantic ? undefined : `Disabled in status ${status}`}
        >
          {actionState.busy === "semantic" ? "Generating…" : "Semantic"}
        </Btn>
        <span style={{ flex: 1 }} />
        <Btn
          kind="danger"
          onClick={() => runAction("reject")}
          disabled={!can.reject || actionState.busy !== null}
          title={can.reject ? undefined : `Disabled in status ${status}`}
        >
          {actionState.busy === "reject" ? "Rejecting…" : "Reject"}
        </Btn>
        <Btn
          kind="primary"
          onClick={() => runAction("validate")}
          disabled={!can.validate || actionState.busy !== null}
          title={can.validate ? undefined : `Disabled in status ${status}`}
        >
          {actionState.busy === "validate" ? "Validating…" : "Validate"}
        </Btn>
      </footer>

      {actionState.error && (
        <div className="orb-review__action-error" role="alert">
          {actionState.error}
        </div>
      )}
      {/* Quietly surface that semantic data exists for future Phase-4 hooks */}
      {semantic && (
        <div className="orb-review__footnote orb-mono">
          semantic.validation_status = {semantic.validation_status}
          {semantic.sections ? ` · ${semantic.sections.length} sections` : ""}
        </div>
      )}
    </div>
  );
}
