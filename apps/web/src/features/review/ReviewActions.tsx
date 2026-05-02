import { useState } from "react";
import {
  ApiError,
  extractVersion,
  generateSemantic,
} from "../../api/client";
import type { DocumentVersionStatus } from "../../api/types";

/**
 * The FSM transitions surfaced as buttons here. Disabled states pull
 * directly from the document version's lifecycle status (see
 * `latestVersion()`); the explanatory copy is displayed via `title`
 * AND `aria-disabled` so it's reachable from a screen reader.
 */
const EXTRACT_ALLOWED_STATUSES: DocumentVersionStatus[] = ["STORED", "EXTRACTED"];
const SEMANTIC_ALLOWED_STATUSES: DocumentVersionStatus[] = [
  "EXTRACTED",
  "SEMANTIC_READY",
  "NEEDS_REVIEW",
];

interface ReviewActionsProps {
  documentId: string;
  versionId: string;
  status: DocumentVersionStatus;
  /**
   * Called after every successful action so the parent can refresh
   * the catalog row + selected document and bump `lastMutationAt`.
   */
  onMutationCompleted: () => void | Promise<void>;
}

export function ReviewActions({
  documentId,
  versionId,
  status,
  onMutationCompleted,
}: ReviewActionsProps) {
  const [busyAction, setBusyAction] = useState<
    "extract" | "semantic" | "refresh" | null
  >(null);
  const [errors, setErrors] = useState<{
    extract?: string;
    semantic?: string;
    refresh?: string;
  }>({});

  const canExtract = EXTRACT_ALLOWED_STATUSES.includes(status);
  const canSemantic = SEMANTIC_ALLOWED_STATUSES.includes(status);

  const extractDisabledReason = canExtract
    ? null
    : status === "VALIDATED"
      ? "Already validated"
      : status === "REJECTED"
        ? "This version was rejected"
        : status === "FAILED"
          ? "Upload a clean version first"
          : "Upload a document first";

  const semanticDisabledReason = canSemantic
    ? null
    : status === "VALIDATED"
      ? "Already validated"
      : status === "REJECTED"
        ? "This version was rejected"
        : status === "STORED"
          ? "Run extraction first"
          : "Run extraction first";

  function setActionError(
    action: "extract" | "semantic" | "refresh",
    message: string | undefined,
  ) {
    setErrors((prev) => ({ ...prev, [action]: message }));
  }

  async function runAction(
    action: "extract" | "semantic" | "refresh",
    fn: () => Promise<unknown>,
  ) {
    setBusyAction(action);
    setActionError(action, undefined);
    try {
      await fn();
      await onMutationCompleted();
    } catch (err: unknown) {
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : "Action failed.";
      setActionError(action, message);
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <div className="review-action-bar" aria-label="Document actions">
      <div className="review-action-row">
        <button
          type="button"
          className="secondary-button"
          aria-disabled={!canExtract || busyAction !== null}
          aria-busy={busyAction === "extract"}
          disabled={!canExtract || busyAction !== null}
          title={extractDisabledReason ?? "Run extraction"}
          onClick={() =>
            void runAction("extract", () => extractVersion(documentId, versionId))
          }
        >
          {busyAction === "extract" ? "Running…" : "Run extraction"}
        </button>
        <button
          type="button"
          className="secondary-button"
          aria-disabled={!canSemantic || busyAction !== null}
          aria-busy={busyAction === "semantic"}
          disabled={!canSemantic || busyAction !== null}
          title={semanticDisabledReason ?? "Generate semantic output"}
          onClick={() =>
            void runAction("semantic", () =>
              generateSemantic(documentId, versionId),
            )
          }
        >
          {busyAction === "semantic" ? "Generating…" : "Generate semantic output"}
        </button>
        <button
          type="button"
          className="secondary-button"
          aria-busy={busyAction === "refresh"}
          disabled={busyAction !== null}
          title="Refresh document state"
          onClick={() => void runAction("refresh", async () => undefined)}
        >
          {busyAction === "refresh" ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {errors.extract ? (
        <div className="notice danger" role="alert">
          <strong>Extraction failed</strong>
          <span>{errors.extract}</span>
        </div>
      ) : null}
      {errors.semantic ? (
        <div className="notice danger" role="alert">
          <strong>Semantic generation failed</strong>
          <span>{errors.semantic}</span>
        </div>
      ) : null}
      {errors.refresh ? (
        <div className="notice danger" role="alert">
          <strong>Refresh failed</strong>
          <span>{errors.refresh}</span>
        </div>
      ) : null}
    </div>
  );
}
