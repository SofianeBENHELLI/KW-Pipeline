import { useEffect, useRef, useState } from "react";
import type { ApiDocument, ApiRawExtraction, ApiSemanticDocument } from "../../api/types";
import {
  ApiError,
  getExtraction,
  getSemantic,
  rejectVersion,
  validateVersion,
} from "../../api/client";
import { documentScopes, latestVersion } from "../../domain/document";
import { ScopeChip } from "../../ui/ScopeChip";
import { StatusBadge } from "../../ui/StatusBadge";
import { KnowledgeGraphView } from "../graph";
import { ProjectionStatusPill } from "./ProjectionStatusPill";
import { ReviewActions } from "./ReviewActions";
import { SemanticAssetList } from "./SemanticAssetList";
import { SemanticSectionList } from "./SemanticSectionList";
import { SemanticWarningList } from "./SemanticWarningList";
import { useProjectionStatus } from "./useProjectionStatus";

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
  // Total version count for the lineage hint (#59 + EPIC-C #217 UX
  // surface). Lineage modal is deferred until /documents/{id}/lineage
  // exists — for now we just render the count alongside the active
  // version number.
  const totalVersions = document.versions.length;
  const latestVersionNumber = latestVersion(document).version_number;

  const [extraction, setExtraction] = useState<ApiRawExtraction | null>(null);
  const [semantic, setSemantic] = useState<ApiSemanticDocument | null>(null);
  const [loadingDetails, setLoadingDetails] = useState(true);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [reviewerNote, setReviewerNote] = useState("");
  const [reviewBusy, setReviewBusy] = useState<"validate" | "reject" | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  // Bumped after every successful validate so the projection-status
  // hook restarts polling from a fresh window. Without this bump, a
  // re-validation of a version that previously COMPLETED would leave
  // the hook on its terminal state and the pill wouldn't update.
  const [projectionPollToken, setProjectionPollToken] = useState<number>(0);

  // Dedup guard against rapid double-clicks. The disabled-button state
  // is the primary defence; this ref is the belt-and-braces second
  // layer that guarantees only one in-flight handler per (version,
  // action) pair, even if a synthetic event slips through (e.g. tests
  // dispatching two clicks before React re-renders the disabled state).
  const inFlightActionsRef = useRef<Set<string>>(new Set());

  // Re-run on mutations so extracted/semantic blobs reflect the latest
  // server state. `lastMutationAt` is bumped by the parent's
  // useDocumentCatalog hook after every successful action. The
  // AbortController short-circuits in-flight detail fetches when the
  // user switches to a different document — without it, the old fetch
  // can resolve into the new selection and overwrite the right data.
  useEffect(() => {
    const controller = new AbortController();
    setExtraction(null);
    setSemantic(null);
    setLoadingDetails(true);
    setDetailError(null);
    setReviewerNote("");
    setReviewError(null);

    async function fetchDetails() {
      try {
        const [ext, sem] = await Promise.allSettled([
          getExtraction(documentId, versionId, { signal: controller.signal }),
          getSemantic(documentId, versionId, { signal: controller.signal }),
        ]);
        if (controller.signal.aborted) return;

        setExtraction(ext.status === "fulfilled" ? ext.value : null);
        setSemantic(sem.status === "fulfilled" ? sem.value : null);

        // Surface a non-404 error to the user. Aborted fetches throw
        // DOMException("AbortError"); ignore them.
        function isAbortError(reason: unknown): boolean {
          return reason instanceof DOMException && reason.name === "AbortError";
        }
        const firstError =
          (ext.status === "rejected" &&
          !isAbortError(ext.reason) &&
          !(ext.reason instanceof ApiError && ext.reason.status === 404)
            ? ext.reason
            : null) ??
          (sem.status === "rejected" &&
          !isAbortError(sem.reason) &&
          !(sem.reason instanceof ApiError && sem.reason.status === 404)
            ? sem.reason
            : null);

        if (firstError !== null) {
          setDetailError(
            firstError instanceof Error ? firstError.message : "Failed to load document details.",
          );
        }
      } catch (err: unknown) {
        if (!controller.signal.aborted) {
          setDetailError(err instanceof Error ? err.message : "Failed to load document details.");
        }
      } finally {
        if (!controller.signal.aborted) setLoadingDetails(false);
      }
    }

    void fetchDetails();
    return () => {
      controller.abort();
    };
  }, [documentId, versionId, lastMutationAt]);

  function handleReview(action: "validate" | "reject") {
    const dedupKey = `${versionId}:${action}`;
    if (inFlightActionsRef.current.has(dedupKey)) return;
    inFlightActionsRef.current.add(dedupKey);

    setReviewBusy(action);
    setReviewError(null);

    const fn = action === "validate" ? validateVersion : rejectVersion;
    fn(documentId, versionId, reviewerNote || undefined)
      .then(async (updated) => {
        setSemantic(updated);
        if (action === "validate") {
          // Restart the projection-status poll loop on every successful
          // validate so the pill reflects the new projection cycle (and
          // not a stale terminal state from a prior validation).
          setProjectionPollToken((n) => n + 1);
        }
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
        inFlightActionsRef.current.delete(dedupKey);
        setReviewBusy(null);
      });
  }

  const canReview = version.status === "NEEDS_REVIEW" && reviewBusy === null;

  // Poll projection status only for VALIDATED versions — that's the
  // only state where projection ran. NEEDS_REVIEW / FAILED / etc. have
  // nothing to show.
  const projectionStatus = useProjectionStatus(
    version.status === "VALIDATED" ? versionId : null,
    projectionPollToken,
  );

  return (
    <section className="workspace" aria-labelledby="workspace-title">
      <header className="workspace-header">
        <div>
          <p className="eyebrow">Document detail</p>
          <h2 id="workspace-title">
            {document.original_filename}
            <span
              className="version-badge"
              data-testid="latest-version-badge"
              aria-label={`Latest version v${latestVersionNumber}`}
              title={`Latest version v${latestVersionNumber}`}
            >
              v{latestVersionNumber}
            </span>
            {/* Scope chip — same component as the catalog row so the
                review header reflects the workspace the doc was
                uploaded into (EPIC-D #218 / #250). Falls back to a
                "No scope info" placeholder until ``GET /documents``
                is extended to carry ``scopes`` (D.5). */}
            <ScopeChip scopes={documentScopes(document)} />
            {totalVersions > 1 ? (
              <span
                className="version-count muted"
                data-testid="version-count"
              >
                {" "}
                ({totalVersions} versions)
              </span>
            ) : null}
          </h2>
          <p className="muted">
            Version {version.version_number}
            {totalVersions > 1 ? (
              <span data-testid="version-of-total"> of {totalVersions} total</span>
            ) : null}
            {" "}&mdash; SHA-256 {version.sha256.slice(0, 12)}
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
            <>
              {/* Validation status stays in its own one-row block —
                  it's the document-level signal the reviewer is
                  about to act on, distinct from the per-row
                  review_status pills inside the asset list below. */}
              <dl className="semantic-list">
                <div>
                  <dt>Validation</dt>
                  <dd data-testid="sem-validation">{semantic.validation_status}</dd>
                </div>
              </dl>

              {/* #408 — three readable sub-panels. The earlier
                  count-only <dl> told the reviewer how many of each
                  but never let them read the actual content; the
                  decision to validate / reject the document is
                  load-bearing enough that the structured artifacts
                  need to be visible inline. */}
              <section
                className="sem-subpanel"
                aria-labelledby="sem-sections-heading"
                data-testid="sem-sections-subpanel"
              >
                <h4 id="sem-sections-heading" className="sem-subpanel__heading">
                  Sections{semantic.sections.length > 0 && ` · ${semantic.sections.length}`}
                </h4>
                <SemanticSectionList sections={semantic.sections} />
              </section>

              <section
                className="sem-subpanel"
                aria-labelledby="sem-assets-heading"
                data-testid="sem-assets-subpanel"
              >
                <h4 id="sem-assets-heading" className="sem-subpanel__heading">
                  Assets{semantic.assets.length > 0 && ` · ${semantic.assets.length}`}
                </h4>
                <SemanticAssetList assets={semantic.assets} />
              </section>

              <section
                className="sem-subpanel"
                aria-labelledby="sem-warnings-heading"
                data-testid="sem-warnings-subpanel"
              >
                <h4 id="sem-warnings-heading" className="sem-subpanel__heading">
                  Warnings{semantic.warnings.length > 0 && ` · ${semantic.warnings.length}`}
                </h4>
                <SemanticWarningList warnings={semantic.warnings} />
              </section>
            </>
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
        <label className="reviewer-note-label" htmlFor="reviewer-note">
          Reviewer note
        </label>
        <textarea
          id="reviewer-note"
          placeholder="Optional context for the audit trail"
          value={reviewerNote}
          onChange={(e) => setReviewerNote(e.target.value)}
          disabled={!canReview}
        />
        <div className="action-row">
          <button
            className="secondary-button"
            type="button"
            disabled={!canReview}
            aria-busy={reviewBusy === "reject"}
            onClick={() => handleReview("reject")}
          >
            {reviewBusy === "reject" ? "Rejecting…" : "Reject"}
          </button>
          <button
            className="primary-button"
            type="button"
            disabled={!canReview}
            aria-busy={reviewBusy === "validate"}
            onClick={() => handleReview("validate")}
          >
            {reviewBusy === "validate" ? "Validating…" : "Validate"}
          </button>
          <ProjectionStatusPill
            status={projectionStatus.status}
            done={projectionStatus.done}
          />
        </div>
      </footer>
    </section>
  );
}
