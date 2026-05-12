/**
 * useFsmTransition — gated wrapper around the four version-level FSM
 * actions (extract / semantic / validate / reject).
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
 */

import { useState } from "react";

import {
  ApiError,
  extractVersion,
  generateSemantic,
  rejectVersion,
  validateVersion,
} from "../../api/client";

export type FsmAction = "extract" | "semantic" | "validate" | "reject";

export type FsmStatus = "idle" | "running" | "ok" | "error";

export interface FsmGates {
  extract: boolean;
  semantic: boolean;
  validate: boolean;
  reject: boolean;
}

/** Compute which FSM buttons are enabled for the given current status. */
export function computeGates(status: string | null | undefined): FsmGates {
  return {
    extract: status === "STORED" || status === "FAILED",
    semantic: status === "EXTRACTED",
    validate:
      status === "NEEDS_REVIEW" || status === "SEMANTIC_READY",
    reject:
      status === "NEEDS_REVIEW" || status === "SEMANTIC_READY",
  };
}

export interface UseFsmTransitionResult {
  status: FsmStatus;
  /** The action currently in flight, if any. */
  activeAction: FsmAction | null;
  error: Error | null;
  gates: FsmGates;
  /** Dispatch one of the four actions. */
  run: (action: FsmAction, reviewerNote?: string) => Promise<void>;
}

export interface UseFsmTransitionOptions {
  documentId: string | null | undefined;
  versionId: string | null | undefined;
  /** The version's current status — used to compute the `gates`. */
  currentStatus: string | null | undefined;
  /** Called after a successful transition. Page typically refetches here. */
  onAfter?: (action: FsmAction) => void;
}

export function useFsmTransition(
  opts: UseFsmTransitionOptions,
): UseFsmTransitionResult {
  const { documentId, versionId, currentStatus, onAfter } = opts;
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
      } else if (action === "semantic") {
        await generateSemantic(documentId, versionId);
      } else if (action === "validate") {
        await validateVersion(documentId, versionId, reviewerNote);
      } else if (action === "reject") {
        await rejectVersion(documentId, versionId, reviewerNote);
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
