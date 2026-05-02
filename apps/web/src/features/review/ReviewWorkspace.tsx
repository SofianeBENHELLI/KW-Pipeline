import { useEffect, useState } from "react";
import type { ApiDocument, ApiRawExtraction, ApiSemanticDocument } from "../../api/types";
import {
  ApiError,
  getExtraction,
  getSemantic,
  rejectVersion,
  validateVersion,
} from "../../api/client";
import { latestVersion } from "../../domain/document";
import { StatusBadge } from "../../ui/StatusBadge";
import { KnowledgeGraphView } from "../graph";
import { ReviewActions } from "./ReviewActions";

interface ReviewWorkspaceProps {
  document: ApiDocument;
  loadingSelected?: boolean;
  refreshError?: string | null;
  lastMutationAt?: number;
  onMutationCompleted?: () => void | Promise<void>;
}

export function ReviewWorkspace({
  document,
  loadingSelected = false,
  refreshError = null,
  lastMutationAt = 0,
  onMutationCompleted,
}: ReviewWorkspaceProps) {
  const version = latestVersion(document);
  const documentId = document.id;
  const versionId = version.id;

  const [extraction, setExtraction] = useState<ApiRawExtraction | null>(null);
  const [semantic, setSemantic] = useState<ApiSemanticDocument | null>(null);
  const [loadingDetails, setLoadingDetails] = useState(true);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [reviewerNote, setReviewerNote] = useState("");
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  // Re-run on mutations so extracted/semantic blobs reflect the latest
  // server state. `lastMutationAt` is bumped by the parent's
  // useDocumentCatalog hook after every successful action.
  useEffect(() => {
    let cancelled = false;
    setExtraction(null);
    setSemantic(null);
    setLoadingDetails(true);
    setDetailError(null);
    setReviewerNote("");
    setReviewError(null);

    async function fetchDetails() {
      try {
        const [ext, sem] = await Promise.allSettled([
          getExtraction(documentId, versionId),
          getSemantic(documentId, versionId),
        ]);
        if (cancelled) return;

        setExtraction(ext.status === "fulfilled" ? ext.value : null);
        setSemantic(sem.status === "fulfilled" ? sem.value : null);

        // Surface a non-404 error to the user
        const firstError =
          (ext.status === "rejected" && !(ext.reason instanceof ApiError && ext.reason.status === 404)
            ? ext.reason
            : null) ??
          (sem.status === "rejected" && !(sem.reason instanceof ApiError && sem.reason.status === 404)
            ? sem.reason
            : null);

        if (firstError !== null) {
          setDetailError(
            firstError instanceof Error ? firstError.message : "Failed to load document details.",
          );
        }
      } catch (err: unknown) {
        if (!cancelled) {
          setDetailError(err instanceof Error ? err.message : "Failed to load document details.");
        }
      } finally {
        if (!cancelled) setLoadingDetails(false);
      }
    }

    void fetchDetails();
    return () => {
      cancelled = true;
    };
  }, [documentId, versionId, lastMutationAt]);

  function handleReview(action: "validate" | "reject") {
    setReviewLoading(true);
    setReviewError(null);

    const fn = action === "validate" ? validateVersion : rejectVersion;
    fn(documentId, versionId, reviewerNote || undefined)
      .then(async (updated) => {
        setSemantic(updated);
        if (onMutationCompleted) await onMutationCompleted();
      })
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError
            ? err.detail
            : err instanceof Error
              ? err.message
              : "Review action failed.";
        setReviewError(message);
      })
      .finally(() => {
        setReviewLoading(false);
      });
  }

  const canReview = version.status === "NEEDS_REVIEW" && !reviewLoading;

  return (
    <section className="workspace" aria-labelledby="workspace-title">
      <header className="workspace-header">
        <div>
          <p className="eyebrow">Document detail</p>
          <h2 id="workspace-title">{document.original_filename}</h2>
          <p className="muted">
            Version {version.version_number} &mdash; SHA-256 {version.sha256.slice(0, 12)}
          </p>
        </div>
        <div className="workspace-header-meta">
          {loadingSelected ? (
            <span
              className="refresh-indicator"
              role="status"
              aria-live="polite"
              aria-label="Refreshing document"
            >
              <span className="spinner" aria-hidden="true" /> Refreshing…
            </span>
          ) : null}
          <StatusBadge status={version.status} />
        </div>
      </header>

      {refreshError ? (
        <div className="notice warning" role="alert">
          <strong>Refresh failed</strong>
          <span>{refreshError}</span>
        </div>
      ) : null}

      {version.failure_reason ? (
        <div className="notice danger" role="status">
          <strong>Extraction failed</strong>
          <span>{version.failure_reason}</span>
        </div>
      ) : null}

      {detailError !== null ? (
        <div className="notice danger" role="alert">
          <strong>Error loading details</strong>
          <span>{detailError}</span>
        </div>
      ) : null}

      <ReviewActions
        documentId={documentId}
        versionId={versionId}
        status={version.status}
        onMutationCompleted={async () => {
          if (onMutationCompleted) await onMutationCompleted();
        }}
      />

      <div className="workspace-grid">
        <article className="panel">
          <div className="panel-heading">
            <h3>Raw extraction</h3>
          </div>
          {loadingDetails ? (
            <p className="muted" role="status">Loading…</p>
          ) : (
            <pre>{extraction?.text ?? "No extraction output is available."}</pre>
          )}
        </article>

        <article className="panel">
          <div className="panel-heading">
            <h3>Semantic output</h3>
          </div>
          {loadingDetails ? (
            <p className="muted" role="status">Loading…</p>
          ) : semantic !== null ? (
            <dl className="semantic-list">
              <div>
                <dt>Validation</dt>
                <dd>{semantic.validation_status}</dd>
              </div>
              <div>
                <dt>Sections</dt>
                <dd>{semantic.sections.length}</dd>
              </div>
              <div>
                <dt>Assets</dt>
                <dd>{semantic.assets.length}</dd>
              </div>
              <div>
                <dt>Warnings</dt>
                <dd>{semantic.warnings.length}</dd>
              </div>
            </dl>
          ) : (
            <p className="muted">Semantic output has not been generated.</p>
          )}
        </article>

        <article className="panel markdown-panel">
          <div className="panel-heading">
            <h3>Markdown preview</h3>
          </div>
          {loadingDetails ? (
            <p className="muted" role="status">Loading…</p>
          ) : (
            <pre>{semantic?.markdown ?? "Markdown preview is not available."}</pre>
          )}
        </article>

        {/* `refreshKey` is the coordination seam with the graph slice
            (issue #133) — `<KnowledgeGraphView>` re-fetches the projection
            whenever this prop changes, so successful mutations elsewhere
            in the workspace propagate into the graph view. `documentStatus`
            lets the panel pick the right empty-state copy
            (pre-validation vs knowledge-layer-disabled). */}
        <KnowledgeGraphView
          documentId={documentId}
          documentStatus={version?.status ?? null}
          refreshKey={lastMutationAt}
        />
      </div>

      <footer className="review-actions" aria-label="Review actions">
        {reviewError !== null ? (
          <div className="notice danger" role="alert">
            <strong>Review failed</strong>
            <span>{reviewError}</span>
          </div>
        ) : null}
        <textarea
          placeholder="Reviewer note"
          aria-label="Reviewer note"
          value={reviewerNote}
          onChange={(e) => setReviewerNote(e.target.value)}
          disabled={!canReview}
        />
        <div className="action-row">
          <button
            className="secondary-button"
            type="button"
            disabled={!canReview}
            onClick={() => handleReview("reject")}
          >
            Reject
          </button>
          <button
            className="primary-button"
            type="button"
            disabled={!canReview}
            onClick={() => handleReview("validate")}
          >
            Validate
          </button>
        </div>
      </footer>
    </section>
  );
}
