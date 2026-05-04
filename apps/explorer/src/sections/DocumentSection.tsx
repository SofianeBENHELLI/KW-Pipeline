/**
 * Document — the three-pane "navigate the knowledge of one document"
 * surface.
 *
 * Layout (left to right):
 *
 *   1. Original — the source binary, rendered by the per-type viewer
 *      (PDF / image / Office / text / …).
 *   2. Raw — the parser's structured extraction, broken into sections.
 *   3. Semantic — the synthesised / structured view (profile, sections,
 *      assets), grouped by `type` and tagged with confidence + review.
 *
 * Cross-pane sync: clicking a section's source-reference id in the Raw
 * pane (or "Show source" in the Semantic pane) propagates the chosen
 * id to the other pane via `activeSourceReferenceId`. Both panes
 * highlight matching content.
 *
 * Per-type structure: the layout itself doesn't change with the
 * document kind — we always show the same three panes — but the
 * Original viewer adapts. The user said the *visualizer* should be
 * "structured per object type"; today that means the viewer renderer
 * dispatches on `DocumentKind`. If a future requirement is to also
 * re-shape the Raw / Semantic panes per-type, do it here.
 */

import React, { useEffect, useMemo, useState } from "react";

import { ApiError, getDocument, getExtraction, getSemantic, rawFileUrl } from "../api/client";
import type {
  Document,
  DocumentVersion,
  RawExtraction,
  SemanticDocument,
} from "../api/types";
import { Icon } from "../components/icons";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { latestVersion } from "../state/document-facets";
import {
  classifyDocument,
  type DocumentKind,
  KIND_LABELS,
} from "../viewers/document-kind";
import { OriginalViewer } from "../viewers/OriginalViewer";
import { RawExtractionPane } from "../viewers/RawExtractionPane";
import { SemanticPane } from "../viewers/SemanticPane";

type PaneId = "original" | "raw" | "semantic";

const ALL_PANES: PaneId[] = ["original", "raw", "semantic"];

interface Props {
  apiBaseUrl: string;
  documentId: string;
  refreshTick: number;
  onBack: () => void;
  onOpenGraph: () => void;
}

export const DocumentSection: React.FC<Props> = ({
  apiBaseUrl,
  documentId,
  refreshTick,
  onBack,
  onOpenGraph,
}) => {
  const [document, setDocument] = useState<Document | null>(null);
  const [extraction, setExtraction] = useState<RawExtraction | null>(null);
  const [semantic, setSemantic] = useState<SemanticDocument | null>(null);
  const [loading, setLoading] = useState({ doc: true, extraction: true, semantic: true });
  const [errors, setErrors] = useState<{ doc: string | null; extraction: string | null; semantic: string | null }>(
    { doc: null, extraction: null, semantic: null },
  );
  const [activeSourceReferenceId, setActiveSourceReferenceId] = useState<string | null>(null);
  const [visiblePanes, setVisiblePanes] = useState<PaneId[]>(ALL_PANES);

  // Pull document → version → extraction + semantic in sequence. The
  // extraction / semantic fetches share the same in-flight controller
  // so navigating away cancels in-flight work cleanly.
  useEffect(() => {
    const controller = new AbortController();
    setLoading({ doc: true, extraction: true, semantic: true });
    setErrors({ doc: null, extraction: null, semantic: null });
    setExtraction(null);
    setSemantic(null);
    setActiveSourceReferenceId(null);

    let cancelled = false;
    (async () => {
      let doc: Document | null = null;
      try {
        doc = await getDocument(documentId, { baseUrl: apiBaseUrl, signal: controller.signal });
        if (cancelled) return;
        setDocument(doc);
      } catch (err: unknown) {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        const message = err instanceof Error ? err.message : "Failed to load document.";
        setErrors((prev) => ({ ...prev, doc: message }));
      } finally {
        if (!cancelled) setLoading((prev) => ({ ...prev, doc: false }));
      }
      const latest = doc ? latestVersion(doc) : null;
      if (!latest) {
        setLoading((prev) => ({ ...prev, extraction: false, semantic: false }));
        return;
      }

      const [ext, sem] = await Promise.allSettled([
        getExtraction(documentId, latest.id, { baseUrl: apiBaseUrl, signal: controller.signal }),
        getSemantic(documentId, latest.id, { baseUrl: apiBaseUrl, signal: controller.signal }),
      ]);
      if (cancelled) return;

      if (ext.status === "fulfilled") {
        setExtraction(ext.value);
      } else if (!isAbortError(ext.reason)) {
        if (ext.reason instanceof ApiError && ext.reason.status === 404) {
          // Pre-extraction — fall through with no error.
        } else {
          const m = ext.reason instanceof Error ? ext.reason.message : "Failed to load extraction.";
          setErrors((prev) => ({ ...prev, extraction: m }));
        }
      }
      setLoading((prev) => ({ ...prev, extraction: false }));

      if (sem.status === "fulfilled") {
        setSemantic(sem.value);
      } else if (!isAbortError(sem.reason)) {
        if (sem.reason instanceof ApiError && sem.reason.status === 404) {
          // Pre-semantic — fall through with no error.
        } else {
          const m = sem.reason instanceof Error ? sem.reason.message : "Failed to load semantic.";
          setErrors((prev) => ({ ...prev, semantic: m }));
        }
      }
      setLoading((prev) => ({ ...prev, semantic: false }));
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [apiBaseUrl, documentId, refreshTick]);

  const latest = useMemo(() => (document ? latestVersion(document) : null), [document]);
  const kind: DocumentKind = useMemo(
    () =>
      latest
        ? classifyDocument(latest.content_type, latest.filename ?? document?.original_filename)
        : "binary",
    [latest, document],
  );

  if (loading.doc && document === null) {
    return (
      <section className="kw-section">
        <SectionHeader icon="docs" title="Document" />
        <p className="kw-status">Loading document…</p>
      </section>
    );
  }

  if (errors.doc !== null) {
    return (
      <section className="kw-section">
        <SectionHeader icon="docs" title="Document" />
        <p className="kw-error" role="alert">
          {errors.doc}
        </p>
        <button type="button" className="kw-btn" onClick={onBack}>
          ← Back to browse
        </button>
      </section>
    );
  }

  if (document === null || latest === null) {
    return (
      <section className="kw-section">
        <SectionHeader icon="docs" title="Document" />
        <p className="kw-status">Document has no versions to render.</p>
        <button type="button" className="kw-btn" onClick={onBack}>
          ← Back to browse
        </button>
      </section>
    );
  }

  const rawUrl = rawFileUrl(documentId, latest.id, apiBaseUrl);

  return (
    <section className="kw-section kx-doc-section" aria-labelledby="document-section-title">
      <SectionHeader
        icon="docs"
        title={document.original_filename}
        meta={`${KIND_LABELS[kind]} · v${latest.version_number}`}
        actions={
          <>
            <button
              type="button"
              className="kw-btn kw-btn--sm kw-btn--ghost"
              onClick={onBack}
            >
              ← Browse
            </button>
            <button
              type="button"
              className="kw-btn kw-btn--sm"
              onClick={onOpenGraph}
              title="Open the knowledge graph centred on this document"
            >
              <Icon name="graph" size={12} /> Graph
            </button>
          </>
        }
      />
      <h2 id="document-section-title" className="visually-hidden">
        {document.original_filename}
      </h2>

      <DocumentToolbar
        latest={latest}
        kind={kind}
        visiblePanes={visiblePanes}
        onTogglePane={(pane) => setVisiblePanes((prev) => togglePane(prev, pane))}
      />

      <div className={`kx-doc-grid kx-doc-grid--${visiblePanes.length}`}>
        {visiblePanes.includes("original") && (
          <article className="kx-pane">
            <header className="kx-pane__hdr">
              <h3 className="kx-pane__title">Original</h3>
              <span className="kw-mono kw-mono--muted">{latest.content_type}</span>
            </header>
            <div className="kx-pane__body kx-pane__body--viewer">
              <OriginalViewer
                kind={kind}
                src={rawUrl}
                filename={latest.filename}
                fileSize={latest.file_size}
              />
            </div>
          </article>
        )}

        {visiblePanes.includes("raw") && (
          <article className="kx-pane">
            <header className="kx-pane__hdr">
              <h3 className="kx-pane__title">Raw extraction</h3>
              {extraction && (
                <span className="kw-mono kw-mono--muted">
                  {extraction.parser_name}
                </span>
              )}
            </header>
            <div className="kx-pane__body">
              <RawExtractionPane
                extraction={extraction}
                loading={loading.extraction}
                error={errors.extraction}
                activeSourceReferenceId={activeSourceReferenceId}
                onPickSourceReference={setActiveSourceReferenceId}
              />
            </div>
          </article>
        )}

        {visiblePanes.includes("semantic") && (
          <article className="kx-pane">
            <header className="kx-pane__hdr">
              <h3 className="kx-pane__title">Semantic synthesis</h3>
              {semantic && (
                <span className="kw-mono kw-mono--muted">
                  schema {semantic.schema_version}
                </span>
              )}
            </header>
            <div className="kx-pane__body">
              <SemanticPane
                semantic={semantic}
                loading={loading.semantic}
                error={errors.semantic}
                activeSourceReferenceId={activeSourceReferenceId}
                onPickSourceReference={setActiveSourceReferenceId}
              />
            </div>
          </article>
        )}
      </div>
    </section>
  );
};

interface ToolbarProps {
  latest: DocumentVersion;
  kind: DocumentKind;
  visiblePanes: PaneId[];
  onTogglePane: (pane: PaneId) => void;
}

const DocumentToolbar: React.FC<ToolbarProps> = ({ latest, kind, visiblePanes, onTogglePane }) => {
  return (
    <div className="kx-doc-toolbar" role="toolbar" aria-label="Document layout">
      <div className="kx-doc-toolbar__meta">
        <StatusBadge status={latest.status} />
        <span className="kw-mono kw-mono--muted">SHA {latest.sha256.slice(0, 12)}</span>
        <span className="kw-mono kw-mono--muted">{latest.file_size.toLocaleString()} bytes</span>
        <span className="kw-mono kw-mono--muted">{KIND_LABELS[kind]}</span>
      </div>
      <div className="kx-doc-toolbar__panes">
        {ALL_PANES.map((pane) => {
          const active = visiblePanes.includes(pane);
          return (
            <button
              key={pane}
              type="button"
              className={`kw-btn kw-btn--sm${active ? " kw-btn--primary" : ""}`}
              aria-pressed={active}
              onClick={() => onTogglePane(pane)}
              disabled={active && visiblePanes.length === 1}
              title={
                active && visiblePanes.length === 1
                  ? "At least one pane must stay open"
                  : `Toggle ${pane} pane`
              }
            >
              {paneLabel(pane)}
            </button>
          );
        })}
      </div>
    </div>
  );
};

function paneLabel(pane: PaneId): string {
  switch (pane) {
    case "original":
      return "Original";
    case "raw":
      return "Raw";
    case "semantic":
      return "Semantic";
  }
}

function togglePane(panes: PaneId[], pane: PaneId): PaneId[] {
  if (panes.includes(pane)) {
    if (panes.length === 1) return panes;
    return panes.filter((p) => p !== pane);
  }
  // Insert in canonical order so the layout doesn't reshuffle.
  return ALL_PANES.filter((p) => panes.includes(p) || p === pane);
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}
