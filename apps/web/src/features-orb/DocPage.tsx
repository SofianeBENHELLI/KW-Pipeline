import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  extractVersion,
  generateSemantic,
  getDocument,
  getDocumentGraph,
  getExtraction,
  getMarkdown,
  getSemantic,
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
import { Btn, Icon, Mono, OrbScopeChip, OrbStatusBadge } from "../ui/orb";

import { LinkedView } from "./LinkedView";
import { PipelineTab, type FsmAction } from "./PipelineTab";
import { OrbPurgeDialog } from "./PurgeDialogs";

const EXTRACTABLE = new Set(["STORED", "EXTRACTED", "FAILED"]);
const SEMANTICABLE = new Set(["EXTRACTED", "SEMANTIC_READY", "NEEDS_REVIEW"]);
const REVIEWABLE = new Set(["NEEDS_REVIEW", "SEMANTIC_READY"]);

type DocTab = "linked" | "pipeline";

export interface DocPageProps {
  documentId: string;
  onBack: () => void;
  onMutated: () => void;
}

/**
 * Variant-A document page — the breadcrumbs + dochead + tab toggle
 * (Linked view / Pipeline & FSM). Owns the data-fetch + FSM
 * orchestration so the tab content components stay presentational.
 */
export function DocPage({ documentId, onBack, onMutated }: DocPageProps) {
  const [doc, setDoc] = useState<ApiDocument | null>(null);
  const [raw, setRaw] = useState<ApiRawExtraction | null>(null);
  const [semantic, setSemantic] = useState<ApiSemanticDocument | null>(null);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [graph, setGraph] = useState<ApiKnowledgeGraphProjection | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [busy, setBusy] = useState<FsmAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reviewerNote, setReviewerNote] = useState("");
  const [tab, setTab] = useState<DocTab>("linked");
  const [purgeOpen, setPurgeOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef<Set<FsmAction>>(new Set());

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
      const version = latestVersion(fresh);
      if (!version) {
        setRaw(null);
        setSemantic(null);
        setMarkdown(null);
        setGraph(null);
        return;
      }
      const [rawRes, semRes, mdRes, graphRes] = await Promise.allSettled([
        getExtraction(documentId, version.id, { signal: controller.signal }),
        getSemantic(documentId, version.id, { signal: controller.signal }),
        getMarkdown(documentId, version.id),
        getDocumentGraph(documentId),
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
          await validateVersion(doc.id, version.id, reviewerNote || undefined);
          break;
        case "reject":
          await rejectVersion(doc.id, version.id, reviewerNote || undefined);
          break;
      }
      setBusy(null);
      onMutated();
      void fetchAll();
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      setActionError(message);
      setBusy(null);
    } finally {
      inFlightRef.current.delete(action);
    }
  };

  if (loading && !doc) {
    return (
      <p style={{ color: "var(--orb-fg-muted)" }}>
        Loading document <Mono>{documentId.slice(0, 8)}</Mono>…
      </p>
    );
  }
  if (fetchError || !doc) {
    return (
      <div className="orb-banner orb-banner--err" role="alert" style={{ borderRadius: 6 }}>
        Failed to load document: {fetchError ?? "not found"}
      </div>
    );
  }

  const latest = latestVersion(doc);
  const status = latest?.status;
  const can = {
    extract: !!status && EXTRACTABLE.has(status),
    semantic: !!status && SEMANTICABLE.has(status),
    validate: !!status && REVIEWABLE.has(status),
    reject: !!status && REVIEWABLE.has(status),
  };
  const projectionDone = graph && graph.nodes.length > 0;
  const projectionLabel = projectionDone ? "COMPLETED" : graph ? "EMPTY" : "PENDING";
  const projectionColor =
    projectionLabel === "COMPLETED"
      ? "var(--orb-ok)"
      : projectionLabel === "EMPTY"
        ? "var(--orb-warn)"
        : "var(--orb-fg-faint)";

  return (
    <>
      <div className="rwA-crumbs">
        <button
          type="button"
          onClick={onBack}
          style={{
            background: "transparent",
            border: 0,
            color: "var(--orb-fg-dim)",
            font: "inherit",
            cursor: "pointer",
            padding: 0,
          }}
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
            <OrbStatusBadge status={status} />
            <span className="orb-mono" style={{ color: "var(--orb-fg-dim)" }}>{doc.id}</span>
            <span style={{ color: "var(--orb-fg-dim)" }}>v{doc.versions.length}</span>
            {(doc.scopes ?? []).map((scope, index) => (
              <OrbScopeChip
                key={`${scope.kind}:${scope.ref}:${index}`}
                scope={scope.kind}
                title={`${scope.kind}: ${scope.ref}`}
              />
            ))}
            <span className="rwA-pill">
              <span className="dot" style={{ background: projectionColor }} />
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
          <Btn kind="ghost" icon={<Icon name="refresh" />} onClick={() => void fetchAll()}>
            Refresh
          </Btn>
        </div>
      </header>

      <div className="rwA-doctabs">
        <button
          type="button"
          className={`rwA-doctab ${tab === "linked" ? "is-active" : ""}`.trim()}
          onClick={() => setTab("linked")}
        >
          <Icon name="graph" /> Linked view
          <span className="rwA-doctab-tag orb-mono">default</span>
        </button>
        <button
          type="button"
          className={`rwA-doctab ${tab === "pipeline" ? "is-active" : ""}`.trim()}
          onClick={() => setTab("pipeline")}
        >
          <Icon name="bolt" /> Pipeline & FSM
        </button>
        <span className="orb-mono rwA-hint" style={{ marginLeft: "auto" }}>
          {tab === "linked"
            ? "hover any object — its source span(s) highlight in the document, and vice-versa"
            : "lifecycle · extraction · semantic · versions"}
        </span>
      </div>

      {tab === "linked" && <LinkedView doc={doc} semantic={semantic} graph={graph} />}
      {tab === "pipeline" && (
        <PipelineTab
          doc={doc}
          raw={raw}
          semantic={semantic}
          markdown={markdown}
          reviewerNote={reviewerNote}
          onReviewerNote={setReviewerNote}
          busy={busy}
          actionError={actionError}
          can={can}
          onAction={runAction}
        />
      )}

      <div style={{ marginTop: 20, paddingTop: 10, borderTop: "1px dashed var(--orb-rule)" }}>
        <Btn kind="ghost" size="xs" onClick={() => setPurgeOpen(true)}>
          Purge document…
        </Btn>
      </div>

      <OrbPurgeDialog
        open={purgeOpen}
        onClose={() => setPurgeOpen(false)}
        onConfirmed={() => {
          onMutated();
          onBack();
        }}
        documentId={doc.id}
        filename={doc.original_filename}
        versionCount={doc.versions.length}
      />
    </>
  );
}
