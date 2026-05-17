/**
 * useFsmTransition — gated wrapper around the version-level FSM
 * actions (extract / semantic / validate / reject / demote).
 *
 * Each action checks the version's current `status` against the `from`
 * set the design handoff §3.5 calls out, fires the matching client
 * call, and surfaces an in-flight + error state. The reviewer-facing
 * note is captured here too so the consuming component can simply
 * call `actions.validate()` after typing.
 *
 * The hook does NOT trigger a refetch — the consuming page does that
 * via the `onAfter` callback (typically `useDocumentDetail.refetch`).
 * Decoupled so this hook stays a pure dispatch helper.
 *
 * The `demote` action drives a previously-VALIDATED or
 * previously-REJECTED version back to NEEDS_REVIEW so the operator
 * can re-open the file when new information surfaces (#435 — backend
 * `POST /reset_to_review`).
 */

import { useState } from "react";

import {
  ApiError,
  extractVersion,
  generateSemantic,
  rejectVersion,
  resetVersionToReview,
  retryExtraction,
  validateVersion,
} from "../../api/client";

export type FsmAction =
  | "extract"
  | "semantic"
  | "semantic-rerun"
  | "validate"
  | "reject"
  | "demote"
  | "retry-extraction";

export type FsmStatus = "idle" | "running" | "ok" | "error";

export interface FsmGates {
  extract: boolean;
  semantic: boolean;
  /**
   * True once semantic output already exists (NEEDS_REVIEW /
   * SEMANTIC_READY / VALIDATED / REJECTED). Drives the "Re-run with
   * method" affordance that lets the operator regenerate without
   * leaving the page. Backend skips the EXTRACTED → NEEDS_REVIEW
   * transition on a regeneration, so the FSM contract is unchanged
   * (catalog row is rewritten in place).
   */
  "semantic-rerun": boolean;
  validate: boolean;
  reject: boolean;
  /** True when the current status is VALIDATED or REJECTED. */
  demote: boolean;
  /**
   * True when the current status is ``FAILED``. Drives the dedicated
   * "Retry extraction" affordance that calls the backend's
   * ``/retry-extraction`` route (#87) — distinct from ``extract``
   * because the retry route reuses the same FAILED row rather than
   * driving a fresh STORED → EXTRACTING transition.
   */
  "retry-extraction": boolean;
}

const _RERUN_STATES = new Set<string>([
  "NEEDS_REVIEW",
  "SEMANTIC_READY",
  "VALIDATED",
  "REJECTED",
]);

/** Compute which FSM buttons are enabled for the given current status. */
export function computeGates(status: string | null | undefined): FsmGates {
  return {
    extract: status === "STORED" || status === "FAILED",
    semantic: status === "EXTRACTED",
    "semantic-rerun": status != null && _RERUN_STATES.has(status),
    validate: status === "NEEDS_REVIEW" || status === "SEMANTIC_READY",
    reject: status === "NEEDS_REVIEW" || status === "SEMANTIC_READY",
    demote: status === "VALIDATED" || status === "REJECTED",
    "retry-extraction": status === "FAILED",
  };
}

export interface UseFsmTransitionResult {
  status: FsmStatus;
  /** The action currently in flight, if any. */
  activeAction: FsmAction | null;
  error: Error | null;
  gates: FsmGates;
  /** Dispatch one of the FSM actions. */
  run: (action: FsmAction, reviewerNote?: string) => Promise<void>;
}

export interface UseFsmTransitionOptions {
  documentId: string | null | undefined;
  versionId: string | null | undefined;
  /** The version's current status — used to compute the `gates`. */
  currentStatus: string | null | undefined;
  /** Called after a successful transition. Page typically refetches here. */
  onAfter?: (action: FsmAction) => void;
  /**
   * Semantic-generation method to send on the next ``semantic`` action.
   * Omit (or pass ``undefined``) to keep the deployment default
   * (deterministic). The dropdown surface threads the operator's
   * choice in here.
   */
  semanticMethod?: string | undefined;
}

export function useFsmTransition(
  opts: UseFsmTransitionOptions,
): UseFsmTransitionResult {
  const { documentId, versionId, currentStatus, onAfter, semanticMethod } = opts;
  const [status, setStatus] = useState<FsmStatus>("idle");
  const [activeAction, setActiveAction] = useState<FsmAction | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const gates = computeGates(currentStatus);

  const run = async (action: FsmAction, reviewerNote?: string) => {
    if (!documentId || !versionId) return;
    if (!gates[action]) return;

    setStatus("running");
    setActiveAction(action);
    setError(null);
    try {
      if (action === "extract") {
        await extractVersion(documentId, versionId);
      } else if (action === "retry-extraction") {
        await retryExtraction(documentId, versionId);
      } else if (action === "semantic" || action === "semantic-rerun") {
        // "semantic-rerun" calls the same endpoint as "semantic" — the
        // backend distinguishes via the cached row's recorded
        // ``extraction_method``: a method-switch on a post-EXTRACTED
        // version regenerates without re-firing the FSM transition.
        await generateSemantic(documentId, versionId, {
          method: semanticMethod || undefined,
        });
      } else if (action === "validate") {
        await validateVersion(documentId, versionId, reviewerNote);
      } else if (action === "reject") {
        await rejectVersion(documentId, versionId, reviewerNote);
      } else if (action === "demote") {
        await resetVersionToReview(documentId, versionId, reviewerNote);
      }
      setStatus("ok");
      onAfter?.(action);
    } catch (err) {
      const e =
        err instanceof ApiError || err instanceof Error
          ? err
          : new Error(String(err));
      setStatus("error");
      setError(e);
    } finally {
      setActiveAction(null);
    }
  };

  return { status, activeAction, error, gates, run };
}
