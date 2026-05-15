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
import {
  DEFAULT_SEMANTIC_METHOD_ID,
  SEMANTIC_METHOD_OPTIONS,
  UNDER_DEVELOPMENT_SUFFIX,
} from "./semanticMethods";

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
  /**
   * Currently-selected semantic-generation method id. Defaults to
   * the deployment default ("structure_first" — Method 1). The
   * dropdown sits next to the Semantic button and threads this
   * value out via ``onSemanticMethodChange``.
   */
  semanticMethod?: string;
  /** Called when the operator picks a different semantic method. */
  onSemanticMethodChange?: (method: string) => void;
}

const DISABLED_REASONS: Record<FsmAction, string> = {
  extract:
    "Available only when the version is in STORED or FAILED — re-extract from the source.",
  semantic:
    "Available only after extraction has succeeded (status EXTRACTED).",
  "semantic-rerun":
    "Re-run is available once semantic output already exists (NEEDS_REVIEW / SEMANTIC_READY / VALIDATED / REJECTED).",
  validate:
    "Available only when the version is in NEEDS_REVIEW or SEMANTIC_READY.",
  reject:
    "Available only when the version is in NEEDS_REVIEW or SEMANTIC_READY.",
  demote:
    "Re-open is available only on a VALIDATED or REJECTED version — drives it back to NEEDS_REVIEW.",
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
  semanticMethod = DEFAULT_SEMANTIC_METHOD_ID,
  onSemanticMethodChange,
}: FsmActionsProps): ReactElement {
  const [note, setNote] = useState("");

  const inflight = (a: FsmAction) =>
    status === "running" && activeAction === a;
  const buttonTitle = (a: FsmAction) => (gates[a] ? undefined : DISABLED_REASONS[a]);

  const semanticHint =
    SEMANTIC_METHOD_OPTIONS.find((o) => o.id === semanticMethod)?.hint ?? "";

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
        <div className="kf-fsm__semantic-group">
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
          <label
            className="kf-fsm__method"
            title={semanticHint}
          >
            <span className="kf-fsm__method-label orb-mono">method</span>
            <select
              className="kf-fsm__method-select"
              value={semanticMethod}
              onChange={(e) => onSemanticMethodChange?.(e.target.value)}
              aria-label="Semantic generation method"
              data-testid="kf-fsm-semantic-method"
              disabled={status === "running"}
            >
              {SEMANTIC_METHOD_OPTIONS.map((opt) => (
                <option
                  key={opt.id}
                  value={opt.id}
                  disabled={opt.disabled}
                  title={opt.disabled ? opt.hint : undefined}
                  data-testid={`kf-fsm-semantic-method-option-${opt.id}`}
                >
                  {opt.disabled
                    ? `${opt.label}${UNDER_DEVELOPMENT_SUFFIX}`
                    : opt.label}
                </option>
              ))}
            </select>
          </label>
          {/* Re-run: regenerate semantic with the dropdown's current
              method when the version already has a semantic row. The
              backend skips the FSM transition for regeneration so the
              lifecycle decision (NEEDS_REVIEW / VALIDATED / REJECTED)
              is unchanged — only the persisted semantic shape is
              rewritten. */}
          <Btn
            kind="ghost"
            xs
            icon={OrbI.refresh}
            disabled={!gates["semantic-rerun"] || status === "running"}
            onClick={() => onRun("semantic-rerun", note || undefined)}
            title={buttonTitle("semantic-rerun")}
            aria-busy={inflight("semantic-rerun")}
            data-testid="kf-fsm-semantic-rerun"
          >
            {inflight("semantic-rerun") ? "Re-running…" : "Re-run"}
          </Btn>
        </div>
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

      {/* Demote button is only meaningful when the version is in a
          terminal review state (VALIDATED / REJECTED). Render it as
          a separate row so the prominent forward actions stay tidy
          and the operator can see at a glance that the version is
          re-openable. The button stays mounted but disabled when
          the gate is closed so the affordance discoverability is
          consistent across statuses. */}
      <div className="kf-fsm__demote">
        <Btn
          kind="ghost"
          icon={OrbI.refresh}
          xs
          disabled={!gates.demote || status === "running"}
          onClick={() => onRun("demote", note || undefined)}
          title={buttonTitle("demote")}
          aria-busy={inflight("demote")}
          data-testid="kf-fsm-demote"
        >
          {inflight("demote") ? "Re-opening…" : "Re-open for review"}
        </Btn>
        <span className="kf-fsm__demote-hint orb-mono">
          demote VALIDATED or REJECTED → NEEDS_REVIEW
        </span>
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
