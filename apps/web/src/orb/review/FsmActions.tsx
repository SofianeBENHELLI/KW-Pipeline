/**
 * FsmActions — the action surface for the Review tab. Per design §3.5:
 *
 *   [Extract] [Semantic]     [Reject (ghost)] [Validate (primary)]
 *   <textarea: reviewer note>
 *   ⚠ Conf 0.78 · below auto-validate threshold 0.85 · routed to NEEDS_REVIEW
 *
 * Buttons are gated by the version's current status (computeGates).
 * Disabled buttons keep their slot — design §3.5: "Don't hide them."
 * The `title=` attribute carries the disabled reason.
 */

import { useState } from "react";
import type { ReactElement } from "react";

import { Btn, OrbI } from "../index";
import type { FsmAction, FsmGates, FsmStatus } from "../hooks/useFsmTransition";

export interface FsmActionsProps {
  gates: FsmGates;
  status: FsmStatus;
  activeAction: FsmAction | null;
  error: Error | null;
  onRun: (action: FsmAction, reviewerNote?: string) => void;
  /** Optional confidence number for the hint line (0..1). */
  confidence?: number | null;
  /** Optional auto-validate threshold for the hint line. */
  autoValidateThreshold?: number;
  /** Optional reviewer / actor identifier shown in the hint line. */
  actor?: string;
}

const DISABLED_REASONS: Record<FsmAction, string> = {
  extract:
    "Available only when the version is in STORED or FAILED — re-extract from the source.",
  semantic:
    "Available only after extraction has succeeded (status EXTRACTED).",
  validate:
    "Available only when the version is in NEEDS_REVIEW or SEMANTIC_READY.",
  reject:
    "Available only when the version is in NEEDS_REVIEW or SEMANTIC_READY.",
};

export function FsmActions({
  gates,
  status,
  activeAction,
  error,
  onRun,
  confidence,
  autoValidateThreshold = 0.85,
  actor,
}: FsmActionsProps): ReactElement {
  const [note, setNote] = useState("");

  const inflight = (a: FsmAction) =>
    status === "running" && activeAction === a;
  const buttonTitle = (a: FsmAction) => (gates[a] ? undefined : DISABLED_REASONS[a]);

  return (
    <div className="kf-fsm">
      <div className="kf-fsm__actions">
        <Btn
          icon={OrbI.bolt}
          disabled={!gates.extract || status === "running"}
          onClick={() => onRun("extract", note || undefined)}
          title={buttonTitle("extract")}
          aria-busy={inflight("extract")}
          data-testid="kf-fsm-extract"
        >
          {inflight("extract") ? "Extracting…" : "Extract"}
        </Btn>
        <Btn
          icon={OrbI.spark}
          disabled={!gates.semantic || status === "running"}
          onClick={() => onRun("semantic", note || undefined)}
          title={buttonTitle("semantic")}
          aria-busy={inflight("semantic")}
          data-testid="kf-fsm-semantic"
        >
          {inflight("semantic") ? "Generating…" : "Semantic"}
        </Btn>
        <span className="kf-fsm__spacer" />
        <Btn
          kind="ghost"
          disabled={!gates.reject || status === "running"}
          onClick={() => onRun("reject", note || undefined)}
          title={buttonTitle("reject")}
          aria-busy={inflight("reject")}
          data-testid="kf-fsm-reject"
        >
          {inflight("reject") ? "Rejecting…" : "Reject"}
        </Btn>
        <Btn
          kind="primary"
          icon={OrbI.check}
          disabled={!gates.validate || status === "running"}
          onClick={() => onRun("validate", note || undefined)}
          title={buttonTitle("validate")}
          aria-busy={inflight("validate")}
          data-testid="kf-fsm-validate"
        >
          {inflight("validate") ? "Validating…" : "Validate"}
        </Btn>
      </div>

      <textarea
        className="kf-fsm__note"
        placeholder="Reviewer note (optional) — appended to audit trail on validate/reject…"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        aria-label="Reviewer note"
      />

      {confidence != null && (
        <div className="kf-fsm__hint orb-mono" data-testid="kf-fsm-hint">
          <span className="kf-fsm__hint-icon" aria-hidden="true">
            {OrbI.alert}
          </span>
          Conf <b>{confidence.toFixed(2)}</b> ·{" "}
          {confidence < autoValidateThreshold ? "below" : "at or above"}{" "}
          auto-validate threshold {autoValidateThreshold.toFixed(2)}
          {actor ? ` · routed by ${actor}` : ""}
        </div>
      )}

      {error && status === "error" && (
        <div className="kf-fsm__error" role="alert" data-testid="kf-fsm-error">
          <strong>Action failed:</strong> {error.message}
        </div>
      )}
    </div>
  );
}
